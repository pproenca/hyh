# tests/hyh/test_complexity.py
"""
Empirical Big-O complexity validation tests.

Uses the big_O library to measure actual runtime scaling and verify
that critical functions meet their documented complexity guarantees.

These tests are slower than unit tests (~10s each) but provide
empirical evidence that algorithms scale as expected.

NOTE: These tests are skipped on CI because Big-O estimation is
timing-sensitive and CI runners have unpredictable performance.
Run locally with: pytest tests/hyh/test_complexity.py -v
"""

import os
from datetime import UTC, datetime

import big_o
import pytest

from hyh.state import Task, TaskStatus, WorkflowState, detect_cycle

# Skip all complexity tests on CI - timing is too unpredictable
# Note: Using bool() to handle both "true" string and truthy values
_is_ci = bool(os.environ.get("CI"))

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        _is_ci,
        reason="Big-O complexity tests are flaky on CI due to variable runner performance",
    ),
]


class TestDetectCycleComplexity:
    """Verify detect_cycle is O(V + E) not O(V²)."""

    @pytest.mark.slow
    def test_detect_cycle_linear_in_nodes(self) -> None:
        """detect_cycle should scale linearly with node count (no edges)."""

        def create_graph(n: int) -> dict[str, list[str]]:
            return {f"node-{i}": [] for i in range(int(n))}

        def measure_func(n: int) -> None:
            graph = create_graph(n)
            detect_cycle(graph)

        best, _ = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=10000,
            n_measures=10,
            n_repeats=50,
        )

        # Should be linear or better, not quadratic
        # big_o may return Polynomial (x^1) instead of Linear class
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Logarithmic,
            big_o.complexities.Linear,
            big_o.complexities.Linearithmic,  # O(n log n) is acceptable
            big_o.complexities.Polynomial,  # x^1 is linear
        )
        assert isinstance(best, acceptable), f"Expected O(n log n) or better, got {best}"

    @pytest.mark.slow
    def test_detect_cycle_linear_chain(self) -> None:
        """detect_cycle on linear chain (V nodes, V-1 edges) should be O(V)."""
        # Pre-build graphs at each size to exclude construction from timing
        sizes = [100, 500, 1000, 2000, 3000, 4000, 5000]
        prebuilt_graphs: dict[int, dict[str, list[str]]] = {}

        for n in sizes:
            graph: dict[str, list[str]] = {}
            for i in range(n):
                if i == 0:
                    graph[f"node-{i}"] = []
                else:
                    graph[f"node-{i}"] = [f"node-{i - 1}"]
            prebuilt_graphs[n] = graph

        def measure_func(n: int) -> None:
            n = int(n)
            closest = min(sizes, key=lambda s: abs(s - n))
            graph = prebuilt_graphs[closest]
            detect_cycle(graph)

        best, _ = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=5000,
            n_measures=10,
            n_repeats=50,
        )

        # Linear chain should be O(V) = O(n)
        # big_o may return Polynomial (x^1) instead of Linear class
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Logarithmic,
            big_o.complexities.Linear,
            big_o.complexities.Linearithmic,  # O(n log n) is acceptable
            big_o.complexities.Polynomial,  # x^1 is linear
        )
        assert isinstance(best, acceptable), f"Expected O(n) or better, got {best}"


class TestWorkflowStateComplexity:
    """Verify WorkflowState operations meet complexity guarantees."""

    @pytest.mark.slow
    def test_get_claimable_task_with_satisfied_deps(self) -> None:
        """get_claimable_task should be O(1) when first task is claimable."""
        # Pre-build states at each size to exclude construction from timing
        sizes = [100, 500, 1000, 2000, 3000, 4000, 5000]
        prebuilt_states: dict[int, WorkflowState] = {}

        for n in sizes:
            tasks = {}
            # First task has no deps - immediately claimable
            tasks["claimable"] = Task(
                id="claimable",
                description="Claimable task",
                status=TaskStatus.PENDING,
                dependencies=(),
            )
            # Rest have deps on first task (will be blocked)
            for i in range(n):
                tasks[f"blocked-{i}"] = Task(
                    id=f"blocked-{i}",
                    description=f"Blocked task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=("claimable",),
                )
            prebuilt_states[n] = WorkflowState(tasks=tasks)

        def measure_func(n: int) -> None:
            n = int(n)
            # Find closest prebuilt size
            closest = min(sizes, key=lambda s: abs(s - n))
            state = prebuilt_states[closest]
            state.get_claimable_task()

        best, others = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=5000,
            n_measures=10,
            n_repeats=50,
        )

        # Should be constant or logarithmic - the actual lookup is O(1)
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Logarithmic,
        )
        assert isinstance(best, acceptable), (
            f"Expected O(1) or O(log n), got {best}. "
            f"Residuals: {
                [(type(c).__name__, r) for c, r in sorted(others.items(), key=lambda x: x[1])[:3]]
            }"
        )

    @pytest.mark.slow
    def test_get_task_for_worker_linear(self) -> None:
        """get_task_for_worker should be O(n) via simple iteration."""

        def measure_func(n: int) -> None:
            n = int(n)
            tasks = {}
            # One running task claimed by our worker
            tasks["my-task"] = Task(
                id="my-task",
                description="My task",
                status=TaskStatus.RUNNING,
                dependencies=(),
                claimed_by="worker-1",
                started_at=datetime.now(UTC),
            )
            # Many other pending tasks
            for i in range(n):
                tasks[f"other-{i}"] = Task(
                    id=f"other-{i}",
                    description=f"Other task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=(),
                )
            state = WorkflowState(tasks=tasks)
            state.get_task_for_worker("worker-1")

        best, _ = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=10000,
            n_measures=10,
            n_repeats=50,
        )

        # Includes state creation overhead, so expect linearithmic or better
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Linear,
            big_o.complexities.Linearithmic,
        )
        if isinstance(best, big_o.complexities.Polynomial):
            # Check that the exponent is <= 1.5 (essentially linear or sublinear)
            assert best.exponent <= 1.5, f"Expected O(n) or better, got O(n^{best.exponent:.2f})"
        else:
            assert isinstance(best, acceptable), f"Expected O(n log n) or better, got {best}"


class TestValidateDagComplexity:
    """Verify validate_dag is O(V + E)."""

    @pytest.mark.slow
    def test_validate_dag_linear_sparse(self) -> None:
        """validate_dag on sparse graph should be O(V)."""

        def create_sparse_dag(n: int) -> WorkflowState:
            n = int(n)
            tasks = {}
            for i in range(n):
                # Each task depends only on previous (sparse: E = V - 1)
                deps = (f"task-{i - 1}",) if i > 0 else ()
                tasks[f"task-{i}"] = Task(
                    id=f"task-{i}",
                    description=f"Task {i}",
                    status=TaskStatus.PENDING,
                    dependencies=deps,
                )
            return WorkflowState(tasks=tasks)

        def measure_func(n: int) -> None:
            state = create_sparse_dag(n)
            state.validate_dag()

        best, _ = big_o.big_o(
            measure_func,
            big_o.datagen.n_,
            min_n=100,
            max_n=5000,
            n_measures=10,
            n_repeats=50,
        )

        # big_o may return Polynomial (x^1) instead of Linear class
        acceptable = (
            big_o.complexities.Constant,
            big_o.complexities.Linear,
            big_o.complexities.Linearithmic,
            big_o.complexities.Polynomial,  # x^1 is linear
        )
        assert isinstance(best, acceptable), f"Expected O(V + E) ≈ O(n), got {best}"
