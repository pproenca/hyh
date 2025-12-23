# Threading & Big-O Test Audit Design

## Overview

Audit of multithreaded tests and big-O complexity validation tests against official documentation and Python 3.14 free-threaded best practices.

## Sources Consulted

- [pberkes/big_O GitHub](https://github.com/pberkes/big_O) - Official big-O library documentation
- [Python Free-Threading Guide - Testing](https://py-free-threading.github.io/testing/) - Best practices for free-threaded testing
- [Python 3.14 threading documentation](https://docs.python.org/3/library/threading.html) - Barrier, Lock semantics
- [Hypothesis issue #4451](https://github.com/HypothesisWorks/hypothesis/issues/4451) - Thread-safety limitations

## Findings

### big-O Library Usage Issues

#### 1. `n_repeats=3` Too Low (CRITICAL)

**Current:**
```python
best, _ = big_o.big_o(
    measure_func,
    big_o.datagen.n_,
    min_n=100,
    max_n=10000,
    n_measures=10,
    n_repeats=3,  # Too low!
)
```

**Problem:** Library examples use `n_repeats=100`. With only 3 repeats, timing variance leads to unreliable complexity class detection.

**Fix:** Increase to `n_repeats=50` minimum.

#### 2. Measurement Overhead Conflation (CRITICAL)

**Current:** `test_get_claimable_task_with_satisfied_deps` constructs `WorkflowState(tasks=tasks)` inside `measure_func`.

**Problem:** O(n) state construction overhead contaminates the O(1) lookup measurement.

**Fix:** Pre-construct states at each size outside the timed function.

#### 3. Overly Permissive Assertions (MEDIUM)

**Current:**
```python
acceptable = (Constant, Logarithmic, Linear, Linearithmic)
```

**Problem:** Accepting 4 classes means O(1) can degrade to O(n log n) without failing—a 10,000x slowdown at n=10,000.

**Fix:** Tighten to expected class + one fallback maximum.

#### 4. Discarded `others` Residuals (LOW)

**Current:** `best, _ = big_o.big_o(...)` discards fit quality data.

**Fix:** Log residuals or assert fit quality threshold.

#### 5. Polynomial Fallback Too Permissive (LOW)

**Current:**
```python
if isinstance(best, big_o.complexities.Polynomial):
    assert best.exponent <= 1.5
```

**Problem:** Silently accepts O(n^1.5) when O(n) is documented.

**Fix:** Fail explicitly or document the relaxation.

#### 6. No Warm-up Phase (LOW)

**Problem:** JIT and cache effects on first run can skew measurements.

**Fix:** Add dummy call before measurement loop.

### Multithreaded Test Issues

#### 1. Missing Barrier Timeout (CRITICAL)

**Current:**
```python
barrier = threading.Barrier(num_threads)  # No timeout!
```

**Problem:** If one thread crashes before `wait()`, all others deadlock forever.

**Fix:**
```python
barrier = threading.Barrier(num_threads, timeout=30.0)
```

#### 2. Hypothesis Stateful Tests Are Single-Threaded (CRITICAL)

**Current:** `StateManagerStateMachine` in `test_freethreading.py:377-483` (~110 lines).

**Problem:** Hypothesis stateful tests run sequentially, not concurrently. They don't test thread safety—they test sequential correctness, which unit tests already cover.

**Decision:** Remove entirely. The deterministic threading tests (`test_no_double_assignment_deterministic`, `test_100_threads_5_tasks_serialization`) provide stronger concurrency coverage.

#### 3. No `sys.setswitchinterval` Fallback (MEDIUM)

**Problem:** On GIL-enabled Python, tests won't expose races.

**Fix:** Add conftest fixture:
```python
@pytest.fixture(autouse=True)
def aggressive_thread_switching():
    """Force frequent GIL release to expose races on GIL-enabled builds."""
    if hasattr(sys, 'getswitchinterval'):
        old = sys.getswitchinterval()
        sys.setswitchinterval(0.000001)
        yield
        sys.setswitchinterval(old)
    else:
        yield
```

#### 4. `time.sleep` Masks Real Behavior (MEDIUM)

**Current:**
```python
# Simulate work
time.sleep(0.001)
```

**Problem:** Sleep adds artificial delay that masks timing issues. CPU-bound work behaves differently than sleep under free-threading.

**Fix:** Replace with CPU-bound work or remove entirely.

#### 5. Lock Contention Test Non-Asserting (LOW)

**Current:**
```python
assert max_time >= avg_time  # Always true
```

**Problem:** Passes even if contention doubles—not a regression test.

**Fix:** Add threshold assertion or document as observational-only.

## Implementation Plan

### Phase 1: Critical Fixes

1. **Remove Hypothesis stateful tests** (`test_freethreading.py:377-483`)
   - Delete `StateManagerStateMachine` class
   - Delete `ExtendedStateManagerStateMachine` class
   - Delete `TestStateManagerConcurrency` and `TestStateManagerConcurrencyExtended`
   - Remove unused imports (`Bundle`, `RuleBasedStateMachine`, `invariant`, `rule`, `settings`)

2. **Add Barrier timeouts** (all threading test files)
   - Search for `threading.Barrier(` without timeout
   - Add `timeout=30.0` parameter

3. **Fix big-O `n_repeats`** (`test_complexity.py`)
   - Change `n_repeats=3` to `n_repeats=50`

4. **Fix measurement overhead** (`test_complexity.py`)
   - Refactor `measure_func` to exclude state construction from timing

### Phase 2: Medium Priority

5. **Add `sys.setswitchinterval` fixture** (`conftest.py`)

6. **Tighten complexity assertions** (`test_complexity.py`)

7. **Remove or replace `time.sleep`** (`test_freethreading.py`)

### Phase 3: Low Priority (Optional)

8. Add warm-up phase to big-O tests
9. Log `others` residuals for diagnostics
10. Add threshold to lock contention test

## Verification

After implementation:
```bash
make test-file FILE=tests/hyh/test_complexity.py
make test-file FILE=tests/hyh/test_freethreading.py
make check  # Full suite
```

## References

- [Python Free-Threading Guide](https://py-free-threading.github.io/)
- [big_O library](https://github.com/pberkes/big_O)
- [Hypothesis thread-safety](https://github.com/HypothesisWorks/hypothesis/issues/4451)
