# Test Suite P0 Cleanup Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate the highest-impact test anti-patterns identified in the test suite audit.

**Architecture:** Consolidate duplicated test helpers into `conftest.py`, remove dead code (skipped placeholders), and replace flaky `time.sleep()` calls with condition-based waiting.

**Tech Stack:** pytest, Python 3.14, threading

---

## Task 1: Remove Duplicate `send_command` from test_daemon.py

**Files:**
- Modify: `tests/hyh/test_daemon.py:89-106`
- Reference: `tests/hyh/conftest.py:167` (existing `send_command`)

**Step 1: Verify conftest.py has the helper**

Run: `grep -n "def send_command" tests/hyh/conftest.py`
Expected: Line 167 shows `send_command` definition

**Step 2: Check test_daemon.py imports**

Run: `head -20 tests/hyh/test_daemon.py`
Expected: See current imports, likely missing conftest import

**Step 3: Remove duplicate send_command from test_daemon.py**

Delete lines 89-106 in `tests/hyh/test_daemon.py` (the `send_command` function definition).

The function to remove looks like:
```python
def send_command(socket_path: str, command: dict, timeout: float = 5.0) -> dict:
    """Send command to daemon and get response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
        sock.sendall(json.dumps(command).encode() + b"\n")
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        return json.loads(response.decode().strip())
    finally:
        sock.close()
```

**Step 4: Add import from conftest**

Add to imports section of `tests/hyh/test_daemon.py`:
```python
from tests.hyh.conftest import send_command
```

**Step 5: Run affected tests**

Run: `uv run pytest tests/hyh/test_daemon.py -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add tests/hyh/test_daemon.py
git commit -m "refactor(tests): use shared send_command from conftest in test_daemon"
```

---

## Task 2: Remove Duplicate `send_command` from test_integration_council.py

**Files:**
- Modify: `tests/hyh/test_integration_council.py:14-31`

**Step 1: Check current state**

Run: `head -40 tests/hyh/test_integration_council.py`
Expected: See duplicate `send_command` definition at line 14

**Step 2: Remove duplicate send_command**

Delete the `send_command` function (lines 14-31).

**Step 3: Add import from conftest**

Add to imports:
```python
from tests.hyh.conftest import send_command
```

**Step 4: Run affected tests**

Run: `uv run pytest tests/hyh/test_integration_council.py -v --tb=short`
Expected: All tests pass

**Step 5: Commit**

```bash
git add tests/hyh/test_integration_council.py
git commit -m "refactor(tests): use shared send_command from conftest in test_integration_council"
```

---

## Task 3: Refactor Inline send_command in test_integration.py Fixtures

**Files:**
- Modify: `tests/hyh/test_integration.py`
- Reference: `tests/hyh/conftest.py:187` (existing `send_command_with_retry`)

The fixtures `workflow_with_tasks`, `workflow_with_short_timeout`, and `workflow_with_parallel_tasks` each define their own `send_command`. These should use `send_command_with_retry` from conftest.

**Step 1: Examine first inline fixture**

Run: `sed -n '393,475p' tests/hyh/test_integration.py`
Expected: See `workflow_with_tasks` fixture with inline `send_command`

**Step 2: Add import at top of file**

Add to imports section (around line 19):
```python
from tests.hyh.conftest import send_command_with_retry
```

**Step 3: Refactor workflow_with_tasks fixture**

In the `workflow_with_tasks` fixture (starting line 393):

1. Remove the inline `send_command` function definition (lines 436-460)
2. Change the yield to use `send_command_with_retry`:

Replace:
```python
    yield {
        "worktree": worktree,
        "socket": socket_path,
        "manager": manager,
        "daemon": daemon,
        "send_command": send_command,
    }
```

With:
```python
    def send_cmd(cmd, max_retries=3):
        return send_command_with_retry(socket_path, cmd, max_retries)

    yield {
        "worktree": worktree,
        "socket": socket_path,
        "manager": manager,
        "daemon": daemon,
        "send_command": send_cmd,
    }
```

**Step 4: Refactor workflow_with_short_timeout fixture**

Apply same pattern to `workflow_with_short_timeout` (line 615):
- Remove inline `send_command` (lines 647-670)
- Use wrapper that calls `send_command_with_retry`

**Step 5: Refactor workflow_with_parallel_tasks fixture**

Apply same pattern to `workflow_with_parallel_tasks` (line 720):
- Remove inline `send_command` (lines 763-786)
- Use wrapper that calls `send_command_with_retry`

**Step 6: Run all integration tests**

Run: `uv run pytest tests/hyh/test_integration.py -v --tb=short`
Expected: All tests pass

**Step 7: Commit**

```bash
git add tests/hyh/test_integration.py
git commit -m "refactor(tests): use shared send_command_with_retry in integration fixtures"
```

---

## Task 4: Remove Skipped Placeholder Tests

**Files:**
- Modify: `tests/hyh/test_client_edge_cases.py:131-143`

**Step 1: Examine the skipped tests**

Run: `sed -n '131,145p' tests/hyh/test_client_edge_cases.py`
Expected: See two test methods with `pytest.skip()`

**Step 2: Remove placeholder tests**

Delete the two placeholder test methods:

```python
def test_spawn_creates_socket(self) -> None:
    """Spawning daemon should create socket file."""
    # This is an integration test - skip if no daemon available
    pytest.skip("Integration test - requires daemon infrastructure")

def test_daemon_crash_during_spawn(self) -> None:
    """Daemon crash during spawn should be handled gracefully."""
    # This is an integration test
    pytest.skip("Integration test - requires process control")
```

**Step 3: Check if class is now empty**

If `TestDaemonSpawning` class has no remaining tests, remove the entire class.

**Step 4: Run affected tests**

Run: `uv run pytest tests/hyh/test_client_edge_cases.py -v --tb=short`
Expected: All remaining tests pass, no skipped tests

**Step 5: Commit**

```bash
git add tests/hyh/test_client_edge_cases.py
git commit -m "refactor(tests): remove placeholder skipped tests from test_client_edge_cases"
```

---

## Task 5: Replace time.sleep in test_runtime.py with Condition-Based Waiting

**Files:**
- Modify: `tests/hyh/test_runtime.py:264, 419, 437`
- Reference: `tests/hyh/conftest.py:26` (existing `wait_until`)

**Step 1: Add wait_until import**

Add to imports in `tests/hyh/test_runtime.py`:
```python
from tests.hyh.conftest import wait_until
```

**Step 2: Replace first time.sleep (line 264)**

Context: This is in a test checking lock blocking behavior.

Replace:
```python
# Give thread a moment to start and block on lock
time.sleep(0.1)

# Thread should be blocked waiting for lock
assert thread.is_alive(), "Command should block waiting for lock"
```

With:
```python
# Wait for thread to start and block on lock
wait_until(
    lambda: thread.is_alive(),
    timeout=1.0,
    message="Thread should start and block on lock"
)

# Verify thread is still blocked (hasn't completed)
assert thread.is_alive(), "Command should block waiting for lock"
```

**Step 3: Find and replace second time.sleep (line 419)**

Run: `sed -n '410,430p' tests/hyh/test_runtime.py`
Examine context and apply similar pattern.

**Step 4: Find and replace third time.sleep (line 437)**

Run: `sed -n '430,445p' tests/hyh/test_runtime.py`
Examine context and apply similar pattern.

**Step 5: Run affected tests**

Run: `uv run pytest tests/hyh/test_runtime.py -v --tb=short`
Expected: All tests pass

**Step 6: Commit**

```bash
git add tests/hyh/test_runtime.py
git commit -m "refactor(tests): replace time.sleep with wait_until in test_runtime"
```

---

## Task 6: Final Verification

**Step 1: Run full test suite**

Run: `uv run pytest -x -q`
Expected: All tests pass (463+)

**Step 2: Verify no remaining issues**

Run: `grep -rn "time\.sleep" tests/hyh/*.py | grep -v conftest | grep -v "# "`
Expected: No results (all time.sleep either in conftest utilities or commented)

Run: `grep -rn "pytest.skip" tests/`
Expected: No results (all placeholder skips removed)

**Step 3: Count send_command definitions**

Run: `grep -rn "def send_command" tests/`
Expected: Only 2 results (both in conftest.py)

**Step 4: Commit summary**

Create a summary commit if needed, or verify all individual commits are clean.

---

## Verification Checklist

After completing all tasks:

- [ ] `send_command` defined only in `conftest.py` (2 variants)
- [ ] No `pytest.skip()` placeholder tests remain
- [ ] No raw `time.sleep()` in test files (except conftest utilities)
- [ ] All 463+ tests still pass
- [ ] Each task has its own atomic commit

---

## Rollback Plan

If issues arise:
```bash
git log --oneline -10  # Find commit before changes
git reset --hard <commit-sha>
```
