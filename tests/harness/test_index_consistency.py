"""
Index Consistency Tests for StateManager.

<approach>
StateManager maintains three internal indexes for O(1) task lookup:
- _pending_deque: FIFO queue of pending task IDs
- _pending_set: O(1) membership test for pending tasks
- _worker_index: Maps worker_id -> task_id for running tasks

These MUST remain synchronized with the tasks dict under concurrent
claim/complete operations. Testing strategy:

1. Verify invariants after every batch of concurrent operations
2. Test edge cases: stale entries, concurrent rotation, idempotency
3. Use Hypothesis to explore operation sequences
</approach>

Tests focus on:
- Index consistency under concurrent claim/complete cycles
- Stale index entry detection and cleanup
- Deque rotation during concurrent deletion
- Worker index idempotency
"""

import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import time_machine
from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from harness.state import Task, TaskStatus, WorkflowState, WorkflowStateStore


def verify_index_invariants(state: WorkflowState) -> list[str]:
    """Verify all index invariants and return list of violations.

    Returns:
        List of violation descriptions (empty if all invariants hold)
    """
    violations = []

    # Invariant 1: Deque and set must match exactly
    deque_set = set(state._pending_deque)
    if deque_set != state._pending_set:
        only_in_deque = deque_set - state._pending_set
        only_in_set = state._pending_set - deque_set
        violations.append(
            f"Deque/set mismatch: only_in_deque={only_in_deque}, only_in_set={only_in_set}"
        )

    # Invariant 2: All pending entries must exist in tasks dict with PENDING status
    for tid in state._pending_deque:
        task = state.tasks.get(tid)
        if task is None:
            violations.append(f"Deque contains missing task '{tid}'")
        elif task.status != TaskStatus.PENDING:
            violations.append(f"Deque contains non-pending task '{tid}' with status {task.status}")

    # Invariant 3: All PENDING tasks must be in pending index
    for tid, task in state.tasks.items():
        if task.status == TaskStatus.PENDING and tid not in state._pending_set:
            violations.append(f"PENDING task '{tid}' not in pending index")

    # Invariant 4: Worker index entries must point to valid RUNNING tasks
    for worker_id, tid in state._worker_index.items():
        task = state.tasks.get(tid)
        if task is None:
            violations.append(f"Worker index: {worker_id} -> missing task '{tid}'")
        elif task.status != TaskStatus.RUNNING:
            violations.append(
                f"Worker index: {worker_id} -> task '{tid}' "
                f"has status {task.status}, expected RUNNING"
            )
        elif task.claimed_by != worker_id:
            violations.append(
                f"Worker index: {worker_id} -> task '{tid}' claimed by '{task.claimed_by}'"
            )

    # Invariant 5: All RUNNING tasks must have worker index entry
    for tid, task in state.tasks.items():
        if task.status == TaskStatus.RUNNING and task.claimed_by:
            if task.claimed_by not in state._worker_index:
                violations.append(
                    f"RUNNING task '{tid}' claimed by '{task.claimed_by}' but worker not in index"
                )
            elif state._worker_index.get(task.claimed_by) != tid:
                violations.append(
                    f"RUNNING task '{tid}' claimed by '{task.claimed_by}' but index points to "
                    f"'{state._worker_index.get(task.claimed_by)}'"
                )

    return violations


class TestIndexConsistencyUnderLoad:
    """Test index consistency under concurrent claim/complete operations."""

    @pytest.mark.parametrize(
        "num_threads,cycles_per_thread",
        [
            (5, 10),  # Light load
            (20, 50),  # Heavy load
            (10, 100),  # Deep cycling
        ],
    )
    def test_index_consistency_under_concurrent_mutations(
        self, num_threads: int, cycles_per_thread: int
    ) -> None:
        """Multiple threads doing claim/complete cycles must preserve indexes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # Create enough tasks for all workers
            num_tasks = num_threads * 2
            tasks = {}
            for i in range(num_tasks):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            errors: list[str] = []
            errors_lock = threading.Lock()
            barrier = threading.Barrier(num_threads)

            def worker(worker_id: str) -> None:
                barrier.wait()
                for _ in range(cycles_per_thread):
                    try:
                        result = manager.claim_task(worker_id)
                        if result.task and not result.is_retry:
                            # Complete immediately
                            manager.complete_task(result.task.id, worker_id)
                        elif result.task and result.is_retry:
                            # Still have a task from previous iteration
                            manager.complete_task(result.task.id, worker_id)
                    except Exception as e:
                        with errors_lock:
                            errors.append(f"{worker_id}: {e}")

            threads = [
                threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(num_threads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Worker errors: {errors}"

            # Verify final state indexes are consistent
            final_state = manager.load()
            assert final_state is not None
            violations = verify_index_invariants(final_state)
            assert len(violations) == 0, f"Index violations: {violations}"


class TestStaleIndexCleanup:
    """Test detection and cleanup of stale index entries.

    KNOWN ISSUE: state.py:324-330 uses lazy cleanup which may leave stale
    entries. This test should FAIL until the issue is fixed.
    """

    def test_stale_worker_index_cleanup(self) -> None:
        """Worker index entries referencing completed tasks should be cleaned.

        This test verifies the index cleanup mechanism works correctly.
        If a task is completed but its worker still has an index entry,
        subsequent operations should clean it up.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {}
            for i in range(5):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            # Worker claims and completes a task
            result = manager.claim_task("worker-1")
            assert result.task is not None
            task_id = result.task.id
            manager.complete_task(task_id, "worker-1")

            # Verify worker is not in index (cleanup should have happened)
            state = manager.load()
            assert state is not None

            # The worker should NOT be in the index after completing
            assert "worker-1" not in state._worker_index, (
                f"Stale worker index entry: worker-1 -> {state._worker_index.get('worker-1')}"
            )

    def test_stale_pending_index_cleanup(self) -> None:
        """Pending index entries for claimed tasks should be removed."""
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

            # Task starts in pending index
            state = manager.load()
            assert state is not None
            assert "task-1" in state._pending_set, "Task should be in pending index initially"

            # Claim task
            result = manager.claim_task("worker-1")
            assert result.task is not None

            # After claim, task should NOT be in pending index
            state = manager.load()
            assert state is not None
            assert "task-1" not in state._pending_set, (
                f"Claimed task still in pending index: {state._pending_set}"
            )

    @pytest.mark.parametrize("num_workers", [5, 10, 20])
    def test_stale_index_accumulation(self, num_workers: int) -> None:
        """Stale index entries should not accumulate over many operations.

        KNOWN ISSUE: This test may fail if lazy cleanup is insufficient.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # Create enough tasks
            num_tasks = num_workers * 3
            tasks = {}
            for i in range(num_tasks):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            # Many workers claim and complete rapidly
            for i in range(num_workers):
                worker_id = f"worker-{i}"
                result = manager.claim_task(worker_id)
                if result.task:
                    manager.complete_task(result.task.id, worker_id)

            # After all completions, worker index should be empty
            state = manager.load()
            assert state is not None

            # Count stale entries
            stale_entries = []
            for worker_id, task_id in state._worker_index.items():
                task = state.tasks.get(task_id)
                if task is None or task.status != TaskStatus.RUNNING:
                    stale_entries.append(f"{worker_id} -> {task_id}")

            assert len(stale_entries) == 0, (
                f"Stale worker index entries accumulated: {stale_entries}"
            )


class TestDequeRotationConcurrency:
    """Test deque rotation behavior under concurrent operations."""

    def test_deque_rotation_concurrent_with_claim(self) -> None:
        """Deque rotation during claim should not cause issues.

        The pending deque rotates blocked tasks to the back.
        Concurrent claims should not corrupt the deque.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # Create tasks with dependencies to trigger rotation
            # task-0 is independent, task-1..9 depend on task-0
            tasks = {
                "task-0": Task(
                    id="task-0",
                    description="Independent",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
            }
            for i in range(1, 10):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Dependent {i}",
                    status=TaskStatus.PENDING,
                    dependencies=["task-0"],
                )
            manager.save(WorkflowState(tasks=tasks))

            claimed_tasks: list[str] = []
            lock = threading.Lock()

            def worker(worker_id: str) -> None:
                # All workers try to claim - only one should get task-0
                result = manager.claim_task(worker_id)
                if result.task:
                    with lock:
                        claimed_tasks.append(result.task.id)

            # Launch many threads to maximize rotation
            threads = [threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Only task-0 should be claimed (others blocked by dependency)
            assert "task-0" in claimed_tasks, "Independent task should be claimed"

            # Verify index consistency after rotation stress
            state = manager.load()
            assert state is not None
            violations = verify_index_invariants(state)
            assert len(violations) == 0, f"Index violations after rotation: {violations}"

    def test_deque_rotation_with_completion(self) -> None:
        """Completing a task should unblock dependents and update indexes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # task-1 depends on task-0
            tasks = {
                "task-0": Task(
                    id="task-0",
                    description="Dependency",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
                "task-1": Task(
                    id="task-1",
                    description="Dependent",
                    status=TaskStatus.PENDING,
                    dependencies=["task-0"],
                ),
            }
            manager.save(WorkflowState(tasks=tasks))

            # Worker 1 claims and completes task-0
            result = manager.claim_task("worker-1")
            assert result.task is not None
            assert result.task.id == "task-0"
            manager.complete_task("task-0", "worker-1")

            # Now task-1 should be claimable
            result = manager.claim_task("worker-2")
            assert result.task is not None
            assert result.task.id == "task-1", f"Expected task-1, got {result.task.id}"

            # Verify indexes
            state = manager.load()
            assert state is not None
            violations = verify_index_invariants(state)
            assert len(violations) == 0, f"Index violations: {violations}"


class TestWorkerIndexIdempotency:
    """Test worker index idempotency under repeated claims."""

    def test_same_worker_repeated_claims(self) -> None:
        """Same worker claiming repeatedly should have single index entry."""
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

            # Claim multiple times
            for _ in range(5):
                result = manager.claim_task("worker-1")
                assert result.task is not None

                state = manager.load()
                assert state is not None

                # Worker should have exactly one index entry
                worker_entries = [(w, t) for w, t in state._worker_index.items() if w == "worker-1"]
                assert len(worker_entries) == 1, (
                    f"Worker has {len(worker_entries)} index entries, expected 1"
                )

    def test_worker_index_after_reclaim(self) -> None:
        """After task timeout and reclaim, old worker's index should be cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Test",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                    timeout_seconds=1,
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            initial_time = datetime.now(UTC)
            with time_machine.travel(initial_time, tick=False) as traveller:
                # Worker 1 claims
                result1 = manager.claim_task("worker-1")
                assert result1.task is not None

                # Advance past timeout (no real sleep!)
                traveller.shift(timedelta(seconds=1.5))

                # Worker 2 reclaims
                result2 = manager.claim_task("worker-2")
                assert result2.task is not None
                assert result2.is_reclaim is True, "Should be a reclaim"

                # Verify indexes
                state = manager.load()
                assert state is not None

                # Worker 1 should NOT be in index
                assert "worker-1" not in state._worker_index, (
                    f"Old worker still in index: {state._worker_index}"
                )

                # Worker 2 should be in index
                assert "worker-2" in state._worker_index, (
                    f"New worker not in index: {state._worker_index}"
                )
                assert state._worker_index["worker-2"] == "task-1"


class TestPendingSetMembershipInvariant:
    """Test that pending set always matches pending deque."""

    def test_pending_set_matches_deque_after_operations(self) -> None:
        """Pending set must exactly match pending deque after any operation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {}
            for i in range(10):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            # Perform various operations
            for i in range(5):
                result = manager.claim_task(f"worker-{i}")
                if result.task:
                    # Check after each claim
                    state = manager.load()
                    assert state is not None
                    deque_set = set(state._pending_deque)
                    assert deque_set == state._pending_set, (
                        f"After claim {i}: deque={deque_set}, set={state._pending_set}"
                    )

                    manager.complete_task(result.task.id, f"worker-{i}")

                    # Check after each complete
                    state = manager.load()
                    assert state is not None
                    deque_set = set(state._pending_deque)
                    assert deque_set == state._pending_set, (
                        f"After complete {i}: deque={deque_set}, set={state._pending_set}"
                    )


# -----------------------------------------------------------------------------
# Hypothesis Stateful Testing for Index Consistency
# -----------------------------------------------------------------------------


@settings(max_examples=50, stateful_step_count=30)
class IndexConsistencyStateMachine(RuleBasedStateMachine):
    """Stateful test verifying index consistency across operation sequences."""

    def __init__(self) -> None:
        super().__init__()
        self.tmpdir = tempfile.mkdtemp()
        self.manager = WorkflowStateStore(Path(self.tmpdir))

        # Create initial tasks with dependencies
        tasks = {}
        for i in range(15):
            deps = [f"task-{j}" for j in range(max(0, i - 2), i)]
            tasks[f"task-{i}"] = Task(
                id=f"task-{i}",
                description=f"Task {i}",
                status=TaskStatus.PENDING,
                dependencies=deps,
            )
        self.manager.save(WorkflowState(tasks=tasks))

        self.active_workers: dict[str, str] = {}
        self.worker_count = 0

    workers = Bundle("workers")

    @rule(target=workers)
    def create_worker(self) -> str:
        worker_id = f"worker-{self.worker_count}"
        self.worker_count += 1
        return worker_id

    @rule(worker=workers)
    def claim(self, worker: str) -> None:
        result = self.manager.claim_task(worker)
        if result.task and not result.is_retry:
            self.active_workers[worker] = result.task.id

    @rule(worker=workers)
    def complete(self, worker: str) -> None:
        if worker in self.active_workers:
            task_id = self.active_workers[worker]
            try:
                self.manager.complete_task(task_id, worker)
                del self.active_workers[worker]
            except ValueError:
                # Task may have been reclaimed
                del self.active_workers[worker]

    @invariant()
    def indexes_consistent(self) -> None:
        state = self.manager.load()
        assert state is not None
        violations = verify_index_invariants(state)
        assert len(violations) == 0, f"Index violations: {violations}"


TestIndexConsistencyHypothesis = IndexConsistencyStateMachine.TestCase


# -----------------------------------------------------------------------------
# Complexity Analysis
# -----------------------------------------------------------------------------
"""
<complexity_analysis>
| Metric | Value |
|--------|-------|
| Time Complexity (Best) | O(1) index lookup |
| Time Complexity (Average) | O(1) amortized with hash set |
| Time Complexity (Worst) | O(n) rebuild_indexes() on every mutation |
| Space Complexity | O(n) for each index structure |
| Scalability Limit | rebuild_indexes() becomes bottleneck at 10K+ tasks |
</complexity_analysis>

<self_critique>
1. Tests verify final state but not intermediate states during concurrent
   operations; a linearizability checker would provide stronger guarantees.
2. Stale index cleanup test may pass spuriously if timing hides the bug;
   explicit injection of stale entries would be more deterministic.
3. No test for index corruption recovery; if indexes get corrupted, rebuild
   should restore consistency but this isn't verified.
</self_critique>
"""
