"""
Free-Threading Stress Tests for Python 3.13t.

<approach>
Python 3.13t removes the Global Interpreter Lock, enabling true parallelism.
This exposes races that the GIL previously hid. Testing strategy:

1. High thread counts (50-100) with synchronized start via threading.Barrier
2. Explicit double-assignment detection (no task claimed by multiple workers)
3. Deterministic stress tests with synchronized thread start
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

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore


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
            manager = WorkflowStateStore(Path(tmpdir))

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
            barrier = threading.Barrier(num_threads, timeout=5.0)

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
            manager = WorkflowStateStore(Path(tmpdir))

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
            manager = WorkflowStateStore(Path(tmpdir))

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
            barrier = threading.Barrier(num_threads, timeout=5.0)

            def worker(worker_id: str) -> None:
                try:
                    barrier.wait()
                    result = manager.claim_task(worker_id)
                    if result.task and not result.is_retry:
                        with count_lock:
                            claim_counts[result.task.id] += 1

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
            manager = WorkflowStateStore(Path(tmpdir))

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
            manager = WorkflowStateStore(Path(tmpdir))

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
            manager = WorkflowStateStore(Path(tmpdir))
            num_threads = 20

            # Create single task - maximum contention
            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Test",
                    status=TaskStatus.PENDING,
                    dependencies=(),
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            acquisition_times: list[float] = []
            times_lock = threading.Lock()
            barrier = threading.Barrier(num_threads, timeout=5.0)

            def contending_claimer(worker_id: str) -> None:
                barrier.wait()
                start = time.monotonic()
                manager.claim_task(worker_id)
                elapsed = time.monotonic() - start
                with times_lock:
                    acquisition_times.append(elapsed)

            threads = [
                threading.Thread(target=contending_claimer, args=(f"worker-{i}",))
                for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify we got measurements from all threads
            assert len(acquisition_times) == num_threads

            # Under contention, later acquisitions take longer (serialization)
            # This documents expected behavior, not a correctness check
            avg_time = sum(acquisition_times) / len(acquisition_times)
            max_time = max(acquisition_times)

            # Sanity check: max should be significantly > avg under contention
            # If not, either very fast machine or lock not serializing
            assert max_time >= avg_time  # Always true, but documents expectation
