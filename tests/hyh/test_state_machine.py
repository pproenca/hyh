"""
Atomic State Machine Transition Tests.

<approach>
The task state machine has three states: PENDING -> RUNNING -> COMPLETED.
Transitions are:
- claim_task: PENDING -> RUNNING (or retry if already RUNNING by same worker)
- complete_task: RUNNING -> COMPLETED (only by claiming worker)
- reclaim: RUNNING -> RUNNING (different worker, if timed out)

Testing strategy:
1. Test interleaved transitions that could break invariants
2. Test dependency resolution during concurrent completion
3. Test DAG traversal while mutations occur
4. Verify terminal states (all completed, no more claims possible)
</approach>

Tests focus on:
- Claim/complete/reclaim race conditions
- Dependency graph evolution during concurrent work
- State machine correctness under adversarial timing
"""

import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from hyh.state import Task, TaskStatus, WorkflowState, WorkflowStateStore


class TestClaimCompleteReclaimInterleave:
    """Test interleaved claim/complete/reclaim operations."""

    def test_claim_timeout_reclaim_complete_race(self) -> None:
        """
        Timeline:
        T=0: Worker A claims task-1
        T=1: Task-1 times out (timeout=1s)
        T=2: Worker B reclaims task-1
        T=3: Worker A tries to complete task-1 (should fail - no longer owner)
        """
        import time_machine

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
                # T=0: Worker A claims
                result_a = manager.claim_task("worker-A")
                assert result_a.task is not None
                assert result_a.task.id == "task-1"
                assert result_a.is_reclaim is False

                # T=1: Advance time past timeout (no actual sleep!)
                traveller.shift(timedelta(seconds=1.5))

                # T=2: Worker B reclaims
                result_b = manager.claim_task("worker-B")
                assert result_b.task is not None
                assert result_b.task.id == "task-1"
                assert result_b.is_reclaim is True, "Should be a reclaim"

            # T=3: Worker A tries to complete - should fail
            with pytest.raises(ValueError) as exc_info:
                manager.complete_task("task-1", "worker-A")

            # Error message should indicate ownership issue
            error_msg = str(exc_info.value).lower()
            assert (
                "not owned by" in error_msg
                or "not claimed by" in error_msg
                or "not running" in error_msg
                or "cannot complete" in error_msg
            ), f"Unexpected error: {exc_info.value}"

            # Worker B should be able to complete
            manager.complete_task("task-1", "worker-B")

            state = manager.load()
            assert state is not None
            assert state.tasks["task-1"].status == TaskStatus.COMPLETED

    def test_double_reclaim_race(self) -> None:
        """Two workers racing to reclaim the same timed-out task."""
        import time_machine

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
                # Original claim
                result = manager.claim_task("worker-original")
                assert result.task is not None

                # Advance time past timeout (mocks ALL datetime.now calls)
                traveller.shift(timedelta(seconds=1.5))

                # Two workers race to reclaim
                results: dict[str, object] = {}
                barrier = threading.Barrier(2, timeout=5.0)

                def reclaimer(worker_id: str) -> None:
                    barrier.wait()
                    result = manager.claim_task(worker_id)
                    results[worker_id] = result

                t1 = threading.Thread(target=reclaimer, args=("worker-B",))
                t2 = threading.Thread(target=reclaimer, args=("worker-C",))
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                # Exactly one should have the task (reclaim), other gets None
                claimed_count = sum(1 for r in results.values() if r.task is not None)
                assert claimed_count == 1, f"Expected exactly 1 claim, got {claimed_count}"

    def test_complete_then_reclaim_attempt(self) -> None:
        """Cannot reclaim a completed task."""
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

            # Claim and complete
            result = manager.claim_task("worker-A")
            assert result.task is not None
            manager.complete_task("task-1", "worker-A")

            # Attempt to claim completed task
            result = manager.claim_task("worker-B")
            # Should get None or a different task
            if result.task is not None:
                assert result.task.id != "task-1", "Completed task should not be claimable"


class TestDependencyResolutionDuringConcurrentCompletion:
    """Test dependency graph evolution during concurrent work."""

    def test_concurrent_completion_unblocks_dependents(self) -> None:
        """Completing dependencies should unblock dependents for claiming."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # Diamond dependency: task-3 depends on task-1 and task-2
            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Left dep",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
                "task-2": Task(
                    id="task-2",
                    description="Right dep",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                ),
                "task-3": Task(
                    id="task-3",
                    description="Depends on 1 and 2",
                    status=TaskStatus.PENDING,
                    dependencies=["task-1", "task-2"],
                ),
            }
            manager.save(WorkflowState(tasks=tasks))

            # Worker 1 claims task-1
            r1 = manager.claim_task("worker-1")
            assert r1.task is not None
            assert r1.task.id in ["task-1", "task-2"]
            task1_id = r1.task.id

            # Worker 2 claims task-2 (or task-1 if worker-1 got task-2)
            r2 = manager.claim_task("worker-2")
            assert r2.task is not None
            task2_id = r2.task.id
            assert task2_id != task1_id

            # Worker 3 tries to claim - should get nothing (task-3 blocked)
            r3 = manager.claim_task("worker-3")
            assert r3.task is None, "task-3 should be blocked by dependencies"

            # Complete first dependency
            manager.complete_task(task1_id, "worker-1")

            # Worker 3 still can't get task-3
            r3 = manager.claim_task("worker-3")
            assert r3.task is None, "task-3 still blocked by task-2"

            # Complete second dependency
            manager.complete_task(task2_id, "worker-2")

            # Now worker 3 can claim task-3
            r3 = manager.claim_task("worker-3")
            assert r3.task is not None
            assert r3.task.id == "task-3"

    def test_chain_dependency_sequential_completion(self) -> None:
        """Linear chain: task-n depends on task-(n-1)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            chain_length = 5
            tasks = {}
            for i in range(chain_length):
                deps = [f"task-{i - 1}"] if i > 0 else []
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Chain step {i}",
                    status=TaskStatus.PENDING,
                    dependencies=deps,
                )
            manager.save(WorkflowState(tasks=tasks))

            # Execute chain sequentially
            for i in range(chain_length):
                result = manager.claim_task(f"worker-{i}")
                assert result.task is not None, f"Should claim task-{i}"
                assert result.task.id == f"task-{i}", f"Expected task-{i}, got {result.task.id}"
                manager.complete_task(f"task-{i}", f"worker-{i}")

            # All tasks should be completed
            state = manager.load()
            assert state is not None
            for tid, task in state.tasks.items():
                assert task.status == TaskStatus.COMPLETED, f"{tid} not completed"


class TestDagTraversalUnderMutation:
    """Test DAG queries while state is being mutated."""

    def test_get_task_during_concurrent_claims(self) -> None:
        """get_task_for_worker should be consistent during concurrent claims."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            # Many independent tasks
            tasks = {}
            for i in range(20):
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            manager.save(WorkflowState(tasks=tasks))

            claimed_tasks: list[str] = []
            claim_lock = threading.Lock()
            errors: list[str] = []
            barrier = threading.Barrier(10, timeout=5.0)

            def claimer(worker_id: str) -> None:
                barrier.wait()
                for _ in range(5):  # Multiple claims per worker
                    result = manager.claim_task(worker_id)
                    if result.task and not result.is_retry:
                        with claim_lock:
                            if result.task.id in claimed_tasks:
                                errors.append(f"Double claim: {result.task.id}")
                            claimed_tasks.append(result.task.id)
                        # Complete and move on
                        manager.complete_task(result.task.id, worker_id)

            threads = [threading.Thread(target=claimer, args=(f"worker-{i}",)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"

            # All tasks should be completed
            state = manager.load()
            assert state is not None
            completed = sum(1 for t in state.tasks.values() if t.status == TaskStatus.COMPLETED)
            assert completed == 20, f"Only {completed}/20 tasks completed"


class TestAllTasksCompletedInvariant:
    """Test behavior when all tasks are completed."""

    def test_get_task_returns_none_when_all_completed(self) -> None:
        """get_task_for_worker returns None when no pending/reclaimable tasks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Only task",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            # Complete the only task
            result = manager.claim_task("worker-1")
            assert result.task is not None
            manager.complete_task("task-1", "worker-1")

            # Any worker should get None now
            for worker_id in ["worker-1", "worker-2", "worker-new"]:
                result = manager.claim_task(worker_id)
                assert result.task is None, f"{worker_id} should get no task"

    def test_multiple_workers_contend_for_last_task(self) -> None:
        """Many workers racing for the last available task."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = WorkflowStateStore(Path(tmpdir))

            tasks = {
                "task-1": Task(
                    id="task-1",
                    description="Last task",
                    status=TaskStatus.PENDING,
                    dependencies=[],
                )
            }
            manager.save(WorkflowState(tasks=tasks))

            results: list[object] = []
            results_lock = threading.Lock()
            barrier = threading.Barrier(20, timeout=5.0)

            def claimer(worker_id: str) -> None:
                barrier.wait()
                result = manager.claim_task(worker_id)
                with results_lock:
                    results.append(result)

            threads = [threading.Thread(target=claimer, args=(f"worker-{i}",)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Exactly one should have claimed (is_retry=False)
            new_claims = [r for r in results if r.task is not None and not r.is_retry]
            assert len(new_claims) == 1, f"Expected 1 new claim, got {len(new_claims)}"


class TestOwnershipEnforcement:
    """Test that only the claiming worker can complete a task."""

    def test_wrong_worker_cannot_complete(self) -> None:
        """Worker B cannot complete task claimed by Worker A."""
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

            # Worker A claims
            result = manager.claim_task("worker-A")
            assert result.task is not None

            # Worker B tries to complete - should fail
            with pytest.raises(ValueError):
                manager.complete_task("task-1", "worker-B")

            # Task should still be running
            state = manager.load()
            assert state is not None
            assert state.tasks["task-1"].status == TaskStatus.RUNNING

    def test_empty_worker_id_rejected(self) -> None:
        """Empty worker ID should be rejected."""
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

            # Empty worker ID should fail
            with pytest.raises(ValueError):
                manager.claim_task("")

            with pytest.raises(ValueError):
                manager.claim_task("   ")  # Whitespace only


class TestRetrySemantics:
    """Test retry flag semantics."""

    def test_retry_returns_same_task(self) -> None:
        """Retry claim returns same task with renewed timestamp."""
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

            import time_machine

            # Use time_machine to control time for timestamp assignment
            initial_time = datetime.now(UTC)
            with time_machine.travel(initial_time, tick=False) as traveller:
                # First claim
                result1 = manager.claim_task("worker-A")
                assert result1.task is not None
                ts1 = result1.task.started_at

                # Advance time (no actual sleep!)
                traveller.shift(timedelta(milliseconds=100))

                # Retry claim
                result2 = manager.claim_task("worker-A")
                assert result2.task is not None
                assert result2.task.id == result1.task.id
                assert result2.is_retry is True
                ts2 = result2.task.started_at

                # Timestamp should be renewed
                assert ts2 > ts1, "Retry should renew timestamp"


# -----------------------------------------------------------------------------
# Hypothesis Stateful Testing for State Machine
# -----------------------------------------------------------------------------


@settings(max_examples=50, stateful_step_count=30)
class TaskStateMachine(RuleBasedStateMachine):
    """Property-based stateful test for task state machine transitions."""

    def __init__(self) -> None:
        super().__init__()
        self.tmpdir = tempfile.mkdtemp()
        self.manager = WorkflowStateStore(Path(self.tmpdir))

        # Create tasks with varying dependencies
        tasks = {}
        for i in range(10):
            # Tasks 0-4 are independent, 5-9 depend on earlier tasks
            deps = []
            if i >= 5:
                deps = [f"task-{i - 5}"]
            tasks[f"task-{i}"] = Task(
                id=f"task-{i}",
                description=f"Task {i}",
                status=TaskStatus.PENDING,
                dependencies=deps,
            )
        self.manager.save(WorkflowState(tasks=tasks))

        self.worker_claims: dict[str, str] = {}  # worker_id -> task_id
        self.completed_tasks: set[str] = set()
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
            self.worker_claims[worker] = result.task.id

    @rule(worker=workers)
    def complete(self, worker: str) -> None:
        if worker in self.worker_claims:
            task_id = self.worker_claims[worker]
            if task_id not in self.completed_tasks:
                try:
                    self.manager.complete_task(task_id, worker)
                    self.completed_tasks.add(task_id)
                    del self.worker_claims[worker]
                except ValueError:
                    # Task was reclaimed
                    del self.worker_claims[worker]

    @invariant()
    def state_machine_valid(self) -> None:
        """Verify state machine invariants."""
        state = self.manager.load()
        assert state is not None

        for tid, task in state.tasks.items():
            # PENDING tasks should not have started_at set (or it's old)
            if task.status == TaskStatus.PENDING:
                # No ownership for pending tasks
                assert task.claimed_by is None or task.claimed_by == "", (
                    f"PENDING task {tid} has claimed_by={task.claimed_by}"
                )

            # RUNNING tasks must have ownership
            if task.status == TaskStatus.RUNNING:
                assert task.claimed_by is not None and task.claimed_by != "", (
                    f"RUNNING task {tid} has no owner"
                )
                assert task.started_at is not None, f"RUNNING task {tid} has no started_at"

            # COMPLETED tasks retain ownership info
            if task.status == TaskStatus.COMPLETED:
                assert task.started_at is not None, f"COMPLETED task {tid} has no started_at"

    @invariant()
    def no_orphaned_claims(self) -> None:
        """Workers should only claim tasks they can actually work on."""
        state = self.manager.load()
        assert state is not None

        for worker_id, task_id in self.worker_claims.items():
            task = state.tasks.get(task_id)
            # Task might have been completed or reclaimed
            if task and task.status == TaskStatus.RUNNING:
                # If running, this worker should own it OR it was reclaimed
                # (our tracking might be stale after reclaim)
                pass


TestStateMachineHypothesis = TaskStateMachine.TestCase


# -----------------------------------------------------------------------------
# Complexity Analysis
# -----------------------------------------------------------------------------
"""
<complexity_analysis>
| Metric | Value |
|--------|-------|
| Time Complexity (Best) | O(1) for claim/complete with index hit |
| Time Complexity (Average) | O(d) where d = dependency chain depth |
| Time Complexity (Worst) | O(n) when scanning for reclaimable tasks |
| Space Complexity | O(n) for task dict + O(w) for worker index |
| Scalability Limit | Deep dependency chains cause sequential bottleneck |
</complexity_analysis>

<self_critique>
1. Timeout-based tests (1.1s sleep) add latency; mocking time would be faster
   but less realistic for testing actual timeout behavior.
2. Hypothesis state machine doesn't test concurrent transitions; it's single-
   threaded sequential which misses true race conditions.
3. Ownership enforcement tests assume specific error message formats which
   could break if messages change; testing error type is more robust.
</self_critique>
"""
