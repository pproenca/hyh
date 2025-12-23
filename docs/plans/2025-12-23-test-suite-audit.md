# Test Suite Audit: Anti-Patterns, Inefficiencies & Quality Framework

**Date:** 2025-12-23
**Status:** Proposed
**Author:** Claude (via brainstorming session)

## Executive Summary

A comprehensive audit of hyh's test suite (~12,900 lines across 35 files) identified structural issues, redundancies, and opportunities for improvement. This document proposes a prioritized remediation plan and an ongoing test health scorecard for quality maintenance.

## Current State

### Test Suite Overview

| Metric                   | Value                         |
| ------------------------ | ----------------------------- |
| Total test files         | 35                            |
| Total lines of test code | ~12,900                       |
| Largest file             | `test_state.py` (1,229 lines) |
| Average file size        | ~370 lines                    |
| Test:Code ratio          | ~2.5:1 (heavy)                |

### Test Categories

| Category    | Files                                                | Purpose                  |
| ----------- | ---------------------------------------------------- | ------------------------ |
| Unit tests  | `test_state.py`, `test_plan.py`, `test_version.py`   | Core logic validation    |
| Integration | `test_integration.py`, `test_daemon.py`              | End-to-end flows         |
| Concurrency | `test_freethreading.py`, `test_concurrency_audit.py` | Race conditions, locking |
| Security    | `test_security_audit.py`, `test_boundary_audit.py`   | Red team vulnerabilities |
| Performance | `test_performance.py`                                | Benchmark regressions    |
| Edge cases  | `test_*_edge_cases.py`                               | Boundary conditions      |

### Infrastructure Strengths

The test suite has several well-designed elements:

1. **Condition-based waiting** (`wait_until`, `wait_for_socket`) - avoids flaky `time.sleep` polling
2. **Thread isolation fixture** (autouse) - prevents test pollution in Python 3.14t
3. **Session-scoped git template** - reduces subprocess overhead
4. **`time-machine` library** - deterministic time testing
5. **`LockTracker` helper** - deadlock detection for lock hierarchy verification
6. **Hypothesis stateful testing** - property-based concurrency testing

---

## Issues Identified

### High Impact

#### 1. Fixture Duplication & Inconsistency

Multiple similar fixtures with subtle differences create confusion:

| Fixture                | Location               | Description                     |
| ---------------------- | ---------------------- | ------------------------------- |
| `worktree`             | conftest.py:143        | Basic git repo (5 subprocesses) |
| `fast_worktree`        | conftest.py:360        | Optimized with session template |
| `integration_worktree` | test_integration.py:21 | Includes socket path cleanup    |

The `send_command` helper function is duplicated **4 times** across test files with slight variations in retry logic and timeout values:

- `test_integration.py:73`
- `test_integration.py:436` (inside `workflow_with_tasks`)
- `test_integration.py:647` (inside `workflow_with_short_timeout`)
- `test_integration.py:763` (inside `workflow_with_parallel_tasks`)

**Impact:** Maintenance burden, inconsistent behavior, confusion about which to use.

#### 2. Large Test Files

Some files are unwieldy and difficult to navigate:

| File                    | Lines | Test Count |
| ----------------------- | ----- | ---------- |
| `test_state.py`         | 1,229 | 50+        |
| `test_integration.py`   | 1,235 | 25+        |
| `test_freethreading.py` | 539   | 15+        |

**Impact:** Hard to find specific tests, unclear test scope, merge conflicts.

#### 3. Inline Fixtures in Test Files

Several integration tests define complex fixtures locally instead of in `conftest.py`:

- `workflow_with_tasks` (test_integration.py:393) - ~75 lines
- `workflow_with_short_timeout` (test_integration.py:615) - ~65 lines
- `workflow_with_parallel_tasks` (test_integration.py:720) - ~75 lines

These share ~80% identical setup code with minor variations.

**Impact:** Code duplication, inconsistent setup, harder to maintain.

### Medium Impact

#### 4. Residual `time.sleep()` Calls

Despite having the `wait_until` utility, some tests still use raw sleeps:

```python
time.sleep(0.1)   # test_concurrency_audit.py:53
time.sleep(0.001) # test_freethreading.py:134
time.sleep(0.01)  # test_freethreading.py:174
```

**Impact:** Potential flakiness, slower tests than necessary.

#### 5. Skipped Tests as Placeholders

```python
def test_spawn_creates_socket(self):
    pytest.skip("Integration test - requires daemon infrastructure")

def test_daemon_crash_during_spawn(self):
    pytest.skip("Integration test - requires process control")
```

**Impact:** Noise in test output, unclear if these are TODOs or intentionally deferred.

#### 6. Test Method Length

Some test methods embed 40+ lines of setup before assertions:

```python
def test_plan_import_then_claim_with_injection(socket_path, worktree):
    # 50+ lines of setup
    # ...
    # Finally, 2 lines of assertions
    assert r.returncode == 0
    assert "Do this carefully" in data["task"]["instructions"]
```

**Impact:** Hard to understand test intent, setup obscures what's being tested.

#### 7. Overlapping Test Coverage

Multiple test files cover the same scenarios:

| Scenario            | Files Testing It                                                                 |
| ------------------- | -------------------------------------------------------------------------------- |
| Worker claims task  | `test_state`, `test_integration`, `test_freethreading`, `test_concurrency_audit` |
| DAG cycle detection | `test_state`, `test_integration`, `test_plan`                                    |
| Timeout reclaim     | `test_state`, `test_integration`                                                 |

**Impact:** Unclear if intentional (defense-in-depth) or accidental duplication.

### Low Impact

#### 8. Inconsistent Test Naming

Mixed conventions across the codebase:

- `test_task_model_basic_validation` (descriptive snake_case)
- `test_claim_task_atomic` (action-focused)
- `test_100_threads_5_tasks_serialization` (numbers in name)

#### 9. Magic Numbers Without Context

```python
assert len(worker_id) == 19  # "worker-" + 12 hex chars
timeout_seconds=600          # Why 600?
num_threads = 100            # Why 100?
```

Comments exist sometimes but not consistently.

#### 10. Test Classes vs Functions

Inconsistent grouping with no clear pattern:

- Some files use classes: `class TestSocketMessageFragmentation`
- Others use plain functions: `def test_task_model_basic_validation()`

#### 11. Missing Docstrings on Test Classes

Test functions often have docstrings, but test classes rarely explain the grouping rationale.

---

## Efficiency Concerns

### Performance

#### 1. Subprocess Overhead

Each `integration_worktree` fixture runs 5-6 `git` subprocesses. While `fast_worktree` exists (copies from session template), many tests still use the slower fixtures.

#### 2. Daemon Lifecycle Per Test

Integration tests spin up/tear down daemons individually. Tests that don't require strict isolation could share a session-scoped daemon with state reset.

#### 3. Large Fixture Generation

Performance tests create 10K-event trajectories and 1000-task DAGs on every run:

```python
for i in range(10_000):
    logger.log({"event": f"event-{i}", "data": "x" * 100})
```

These could potentially be pre-generated or cached.

### Redundancy

#### 4. Repeated Assertion Patterns

This pattern appears 20+ times:

```python
assert resp["status"] == "ok"
assert resp["data"]["task"]["id"] == "task-1"
```

No assertion helper like `assert_task_claimed(resp, "task-1")` exists.

#### 5. Coverage Gaps Despite Volume

Despite ~13K lines of tests, some edge cases have placeholder skips:

- Daemon crash during spawn
- Socket creation verification

---

## Recommendations

### P0 - Quick Wins (Low effort, High impact)

| Issue                     | Action                                                     | Estimated Effort |
| ------------------------- | ---------------------------------------------------------- | ---------------- |
| Duplicated `send_command` | Move to `conftest.py` as `send_command_with_retry` fixture | 1 hour           |
| Inline workflow fixtures  | Extract to `conftest.py` with parametrization              | 2 hours          |
| Skipped placeholder tests | Delete or convert to GitHub issues                         | 30 min           |
| Residual `time.sleep()`   | Replace with `wait_until` pattern                          | 1 hour           |

### P1 - Structural Improvements (Medium effort)

| Issue                 | Action                                                                                 | Estimated Effort |
| --------------------- | -------------------------------------------------------------------------------------- | ---------------- |
| Large test files      | Split `test_state.py` into `test_task.py`, `test_workflow.py`, `test_state_manager.py` | 4 hours          |
| Overlapping coverage  | Audit and document intentional overlap; consolidate accidental duplication             | 3 hours          |
| Inconsistent fixtures | Standardize on `fast_worktree`; deprecate `worktree` for most uses                     | 2 hours          |

### P2 - Long-term Quality (Higher effort)

| Issue                | Action                                                        | Estimated Effort |
| -------------------- | ------------------------------------------------------------- | ---------------- |
| No assertion helpers | Create `tests/hyh/helpers/assertions.py` with common patterns | 2 hours          |
| Magic numbers        | Define constants module or inline documentation               | 1 hour           |
| Naming conventions   | Document standard in CLAUDE.md, apply incrementally           | Ongoing          |

---

## Test Health Scorecard

Proposed metrics for quarterly review:

| Metric               | Target        | Current (Est.) | Measurement Method                      |
| -------------------- | ------------- | -------------- | --------------------------------------- |
| Fixture duplication  | < 5 instances | ~15            | Grep for duplicate function definitions |
| Avg test file size   | < 300 lines   | ~370           | `wc -l tests/**/*.py \| awk`            |
| `time.sleep()` calls | 0             | ~8             | `grep -r "time.sleep" tests/`           |
| Skipped tests        | 0             | 2              | `pytest --collect-only \| grep skip`    |
| Flaky test rate      | < 1%          | Unknown        | CI failure analysis                     |
| Test:Code ratio      | 1:1 - 2:1     | ~2.5:1         | Line count comparison                   |
| Assertion helpers    | Yes           | No             | File existence check                    |

### CI Integration

Add scorecard checks to CI pipeline:

```yaml
# .github/workflows/test-quality.yml
- name: Check for time.sleep in tests
  run: |
    count=$(grep -r "time\.sleep" tests/ | wc -l)
    if [ "$count" -gt 0 ]; then
      echo "::warning::Found $count time.sleep() calls in tests"
    fi

- name: Check test file sizes
  run: |
    for f in tests/hyh/*.py; do
      lines=$(wc -l < "$f")
      if [ "$lines" -gt 500 ]; then
        echo "::warning::$f has $lines lines (target: <300)"
      fi
    done
```

---

## Implementation Roadmap

### Week 1: Quick Wins

- [ ] Consolidate `send_command` helper to conftest.py
- [ ] Remove or issue-ify skipped placeholder tests
- [ ] Replace remaining `time.sleep()` calls

### Week 2: Fixture Cleanup

- [ ] Extract inline workflow fixtures to conftest.py
- [ ] Add parametrization for timeout/task count variations
- [ ] Deprecate `worktree` in favor of `fast_worktree`

### Week 3: File Restructuring

- [ ] Split `test_state.py` into focused modules
- [ ] Document test category boundaries
- [ ] Add test class docstrings

### Week 4: Quality Infrastructure

- [ ] Create assertion helpers module
- [ ] Add scorecard metrics to CI
- [ ] Document testing conventions in CLAUDE.md

---

## Appendix: File-by-File Analysis

### Files Requiring Immediate Attention

| File                        | Priority | Issues                                    |
| --------------------------- | -------- | ----------------------------------------- |
| `test_integration.py`       | P0       | 4x duplicated helpers, 3x inline fixtures |
| `test_state.py`             | P1       | 1,229 lines, should split                 |
| `test_client_edge_cases.py` | P0       | Contains skipped placeholders             |
| `test_concurrency_audit.py` | P0       | Uses `time.sleep(0.1)`                    |

### Files in Good Shape

| File                      | Notes                               |
| ------------------------- | ----------------------------------- |
| `conftest.py`             | Well-structured, good documentation |
| `test_performance.py`     | Clean benchmarks with clear purpose |
| `helpers/lock_tracker.py` | Sophisticated, well-documented      |

---

## Decision Log

| Decision                                             | Rationale                                             | Date       |
| ---------------------------------------------------- | ----------------------------------------------------- | ---------- |
| Prioritize fixture consolidation over file splitting | Immediate impact on maintenance burden                | 2025-12-23 |
| Keep overlapping coverage for now                    | Need audit to distinguish intentional from accidental | 2025-12-23 |
| Add scorecard to CI as warnings, not failures        | Avoid blocking PRs during transition                  | 2025-12-23 |
