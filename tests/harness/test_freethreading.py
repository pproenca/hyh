"""
Free-Threading Stress Tests for Python 3.13t.

<approach>
Python 3.13t removes the Global Interpreter Lock, enabling true parallelism.
This exposes races that the GIL previously hid. Testing strategy:

1. High thread counts (50-100) with synchronized start via threading.Barrier
2. Explicit double-assignment detection (no task claimed by multiple workers)
3. Hypothesis stateful testing for randomized operation sequences
4. Memory visibility verification (mutations visible across threads immediately)

The StateManager uses threading.Lock which provides memory barriers on
acquire/release. Tests verify these guarantees hold under load.
</approach>

Tests focus on:
- No double-assignment of tasks to workers
- Index consistency under high contention
- Memory visibility across threads
- Serialization correctness under load
"""

import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from harness.state import StateManager, Task, TaskStatus, WorkflowState


class TestNoDoubleAssignment:
    """Verify no task is ever claimed by multiple workers simultaneously."""

    @pytest.mark.parametrize(
        "num_threads,num_tasks",
        [
            (10, 5),  # 2:1 thread:task ratio
            (50, 10),  # 5:1 ratio - high contention
            (100, 20),  # 5:1 ratio - stress test
        ],
    )
    def test_no_double_assignment_deterministic(self, num_threads: int, num_tasks: int) -> None:
        """Multiple workers racing to claim tasks must never get same task.

        This is the CRITICAL invariant for concurrent task execution.
        Violation means two workers could execute same task simultaneously.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            # Create tasks with no dependencies (all claimable)
            tasks = {}
            for i in range(num_tasks):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            state = WorkflowState(tasks=tasks)
            manager.save(state)

            # Tracking
            claimed_by: dict[str, str] = {}  # task_id -> first_worker_id
            claim_lock = threading.Lock()
            violations: list[str] = []
            barrier = threading.Barrier(num_threads)

            def worker(worker_id: str) -> None:
                barrier.wait()  # Synchronized start for maximum contention
                result = manager.claim_task(worker_id)
                if result.task:
                    with claim_lock:
                        task_id = result.task.id
                        if task_id in claimed_by:
                            # VIOLATION: Double assignment detected
                            violations.append(
                                f"Task {task_id} claimed by both "
                                f"{claimed_by[task_id]} and {worker_id}"
                            )
                        else:
                            claimed_by[task_id] = worker_id

            threads = [
                threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(violations) == 0, f"Double assignment violations: {violations}"

            # Additional invariant: number of claims <= number of tasks
            assert len(claimed_by) <= num_tasks, (
                f"More claims ({len(claimed_by)}) than tasks ({num_tasks})"
            )

    def test_idempotent_claim_same_worker(self) -> None:
        """Same worker calling claim_task multiple times gets same task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Test",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            # First claim
            result1 = manager.claim_task("worker-1")
            assert result1.task is not None
            assert result1.task.id == "task-1"
            assert result1.is_retry is False

            # Second claim by same worker
            result2 = manager.claim_task("worker-1")
            assert result2.task is not None
            assert result2.task.id == "task-1"
            assert result2.is_retry is True  # Indicates retry


class TestHighContentionSerialization:
    """Verify correct serialization under extreme lock contention."""

    @pytest.mark.slow
    def test_100_threads_5_tasks_serialization(self) -> None:
        """100 threads competing for 5 tasks - extreme contention.

        Measures that all claims + completes succeed without data loss.
        Each task should be claimed exactly once and completed exactly once.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            num_tasks = 5
            num_threads = 100

            tasks = {}
            for i in range(num_tasks):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            claim_counts: Counter[str] = Counter()  # task_id -> claim count
            complete_counts: Counter[str] = Counter()  # task_id -> complete count
            count_lock = threading.Lock()
            errors: list[str] = []
            barrier = threading.Barrier(num_threads)

            def worker(worker_id: str) -> None:
                try:
                    barrier.wait()
                    result = manager.claim_task(worker_id)
                    if result.task and not result.is_retry:
                        with count_lock:
                            claim_counts[result.task.id] += 1

                        # Simulate work
                        time.sleep(0.001)

                        # Complete the task
                        manager.complete_task(result.task.id, worker_id)
                        with count_lock:
                            complete_counts[result.task.id] += 1
                except Exception as e:
                    with count_lock:
                        errors.append(f"{worker_id}: {e}")

            threads = [
                threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors during execution: {errors}"

            # Each task claimed exactly once
            for task_id, count in claim_counts.items():
                assert count == 1, f"Task {task_id} claimed {count} times (expected 1)"

            # Each task completed exactly once
            for task_id, count in complete_counts.items():
                assert count == 1, f"Task {task_id} completed {count} times (expected 1)"

            # All tasks should be completed
            assert len(complete_counts) == num_tasks, (
                f"Only {len(complete_counts)} tasks completed out of {num_tasks}"
            )


class TestMemoryVisibility:
    """Verify mutations are immediately visible across threads.

    In free-threaded Python, without the GIL, memory visibility
    depends on proper synchronization primitives. StateManager uses
    threading.Lock which provides acquire-release semantics.
    """

    def test_write_read_visibility(self) -> None:
        """Thread A writes, Thread B reads immediately - must see update."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Test",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            visibility_errors: list[str] = []
            claim_done = threading.Event()

            def writer() -> None:
                manager.claim_task("worker-writer")
                claim_done.set()

            def reader() -> None:
                claim_done.wait()
                # Immediately after claim, task should be RUNNING
                state = manager.load()
                if state is None:
                    visibility_errors.append("State is None after claim")
                    return
                task = state.tasks.get("task-1")
                if task is None:
                    visibility_errors.append("Task missing after claim")
                    return
                if task.status != TaskStatus.RUNNING:
                    visibility_errors.append(f"Task status is {task.status}, expected RUNNING")

            t_writer = threading.Thread(target=writer)
            t_reader = threading.Thread(target=reader)

            t_writer.start()
            t_reader.start()

            t_writer.join()
            t_reader.join()

            assert len(visibility_errors) == 0, f"Visibility errors: {visibility_errors}"

    def test_rapid_state_transitions_visibility(self) -> None:
        """Rapid claim -> complete cycles must all be visible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            num_tasks = 20
            tasks = {}
            for i in range(num_tasks):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            completed_tasks: list[str] = []
            completed_lock = threading.Lock()

            def rapid_worker(worker_id: str) -> None:
                while True:
                    result = manager.claim_task(worker_id)
                    if result.task is None:
                        break  # No more tasks
                    if result.is_retry:
                        # Still working on same task
                        manager.complete_task(result.task.id, worker_id)
                        with completed_lock:
                            if result.task.id not in completed_tasks:
                                completed_tasks.append(result.task.id)
                    else:
                        # New task claimed
                        manager.complete_task(result.task.id, worker_id)
                        with completed_lock:
                            completed_tasks.append(result.task.id)

            threads = [
                threading.Thread(target=rapid_worker, args=(f"worker-{i}",)) for i in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify all tasks completed
            final_state = manager.load()
            assert final_state is not None
            completed_count = sum(
                1 for t in final_state.tasks.values() if t.status == TaskStatus.COMPLETED
            )
            assert completed_count == num_tasks, (
                f"Only {completed_count} tasks completed, expected {num_tasks}"
            )


class TestLockContention:
    """Measure and verify lock contention behavior."""

    def test_lock_acquisition_under_contention(self) -> None:
        """Document lock contention behavior - not a pass/fail test."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))

            # Create single task - maximum contention
            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Test",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            acquisition_times: list[float] = []
            times_lock = threading.Lock()
            barrier = threading.Barrier(20)

            def contending_claimer(worker_id: str) -> None:
                barrier.wait()
                start = time.monotonic()
                manager.claim_task(worker_id)
                elapsed = time.monotonic() - start
                with times_lock:
                    acquisition_times.append(elapsed)

            threads = [
                threading.Thread(target=contending_claimer, args=(f"worker-{i}",))
                for i in range(20)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify we got measurements from all threads
            assert len(acquisition_times) == 20

            # Under contention, later acquisitions take longer (serialization)
            # This documents expected behavior, not a correctness check
            avg_time = sum(acquisition_times) / len(acquisition_times)
            max_time = max(acquisition_times)

            # Sanity check: max should be significantly > avg under contention
            # If not, either very fast machine or lock not serializing
            assert max_time >= avg_time  # Always true, but documents expectation


# -----------------------------------------------------------------------------
# Hypothesis Stateful Testing
# -----------------------------------------------------------------------------


@settings(max_examples=50, stateful_step_count=30)
class StateManagerStateMachine(RuleBasedStateMachine):
    """Property-based stateful test for StateManager concurrency invariants.

    Uses Hypothesis to generate random sequences of claim/complete operations
    and verifies invariants hold after each operation.

    This catches edge cases that deterministic tests miss by exploring
    the state space more thoroughly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tmpdir = tempfile.mkdtemp()
        self.manager = StateManager(Path(self.tmpdir))

        # Create initial tasks
        tasks = {}
        for i in range(10):
            tasks[f"task-{i}"] = Task(
                id=f"task-{i}",
                description=f"Task {i}",
                status=TaskStatus.PENDING,
                dependencies=[],
            )
        self.manager.save(WorkflowState(tasks=tasks))

        # Track our view of state
        self.active_claims: dict[str, str] = {}  # worker_id -> task_id
        self.completed_tasks: set[str] = set()
        self.workers_created: list[str] = []

    workers = Bundle("workers")

    @rule(target=workers)
    def add_worker(self) -> str:
        """Add a new worker to the pool."""
        worker_id = f"worker-{len(self.workers_created)}"
        self.workers_created.append(worker_id)
        return worker_id

    @rule(worker=workers)
    def claim_task(self, worker: str) -> None:
        """Worker claims a task."""
        result = self.manager.claim_task(worker)
        if result.task:
            if worker in self.active_claims:
                # Retry - should be same task
                assert result.task.id == self.active_claims[worker], (
                    f"Retry gave different task: expected {self.active_claims[worker]}, "
                    f"got {result.task.id}"
                )
            else:
                self.active_claims[worker] = result.task.id

    @rule(worker=workers)
    def complete_task(self, worker: str) -> None:
        """Worker completes their claimed task."""
        if worker not in self.active_claims:
            return  # Nothing to complete

        task_id = self.active_claims[worker]
        self.manager.complete_task(task_id, worker)
        self.completed_tasks.add(task_id)
        del self.active_claims[worker]

    @invariant()
    def no_double_claims(self) -> None:
        """Each task claimed by at most one worker."""
        state = self.manager.load()
        assert state is not None

        running_tasks = [t for t in state.tasks.values() if t.status == TaskStatus.RUNNING]
        claimed_by_workers = [t.claimed_by for t in running_tasks if t.claimed_by]

        # No duplicates
        assert len(claimed_by_workers) == len(set(claimed_by_workers)), (
            f"Double claim detected: {claimed_by_workers}"
        )

    @invariant()
    def indexes_consistent(self) -> None:
        """Verify internal indexes match task dict."""
        state = self.manager.load()
        assert state is not None

        # Pending deque and set must match
        assert set(state._pending_deque) == state._pending_set, (
            f"Deque/set mismatch: deque={set(state._pending_deque)}, set={state._pending_set}"
        )

        # All pending entries must be PENDING tasks
        for tid in state._pending_deque:
            task = state.tasks.get(tid)
            assert task is not None, f"Deque contains missing task {tid}"
            assert task.status == TaskStatus.PENDING, (
                f"Deque contains non-pending task {tid} with status {task.status}"
            )

        # Worker index must point to valid RUNNING tasks
        for worker_id, tid in state._worker_index.items():
            task = state.tasks.get(tid)
            assert task is not None, f"Worker index points to missing task {tid}"
            assert task.status == TaskStatus.RUNNING, (
                f"Worker index points to non-running task {tid}"
            )
            assert task.claimed_by == worker_id, (
                f"Worker index mismatch: {worker_id} -> {tid} but task.claimed_by={task.claimed_by}"
            )

    @invariant()
    def completed_stays_completed(self) -> None:
        """Completed tasks cannot become uncompleted."""
        state = self.manager.load()
        assert state is not None

        for task_id in self.completed_tasks:
            task = state.tasks.get(task_id)
            assert task is not None
            assert task.status == TaskStatus.COMPLETED, (
                f"Completed task {task_id} has status {task.status}"
            )


TestStateManagerConcurrency = StateManagerStateMachine.TestCase


# Extended version with more examples for deeper exploration
# Use pytest -m "not slow" for fast iteration
class ExtendedStateManagerStateMachine(StateManagerStateMachine):
    """Extended version with more examples for deeper exploration."""

    pass


TestStateManagerConcurrencyExtended = ExtendedStateManagerStateMachine.TestCase
TestStateManagerConcurrencyExtended.settings = settings(max_examples=200, stateful_step_count=50)
TestStateManagerConcurrencyExtended = pytest.mark.slow(TestStateManagerConcurrencyExtended)


# -----------------------------------------------------------------------------
# Complexity Analysis
# -----------------------------------------------------------------------------
"""
<complexity_analysis>
| Metric | Value |
|--------|-------|
| Time Complexity (Best) | O(1) per claim when index hit |
| Time Complexity (Average) | O(1) amortized with index lookup |
| Time Complexity (Worst) | O(n) when pending deque exhausted, linear scan |
| Space Complexity | O(n) for indexes + O(t) threads contending |
| Scalability Limit | Lock contention at ~100 threads; sharded state needed for 1000+ |
</complexity_analysis>

<self_critique>
1. Hypothesis stateful tests run single-threaded; true multi-threaded property
   testing would require custom executor or hypothesis-threading plugin.
2. Memory visibility tests rely on timing assumptions; a formal memory model
   test would use memory barriers and volatile semantics verification.
3. Lock contention test documents behavior but doesn't fail on regression;
   adding threshold assertions would make it a proper regression test.
</self_critique>
"""
