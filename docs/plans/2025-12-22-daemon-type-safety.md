# daemon.py Type Safety Refactoring

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-22-daemon-type-safety.md` to implement task-by-task.

**Goal:** Replace untyped `dict[str, Any]` RPC handling in daemon.py with typed msgspec structs for requests and responses.

**Architecture:** Use msgspec tagged unions for request dispatch (tag_field="command") and a generic `Ok[T] | Err` Result pattern for responses. All types co-located in daemon.py. Pure msgspec serialization (not backwards-compatible JSON dicts).

**Tech Stack:** msgspec (Struct, tagged unions, Meta constraints), Python 3.13+ type aliases

---

## Task Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 3 | Foundation types - must be serial (build on each other) |
| Group 2 | 4, 5 | Refactor dispatch and handlers (depends on types) |
| Group 3 | 6 | Update client (depends on new wire format) |
| Group 4 | 7 | Code Review |

---

### Task 1: Add Request Types to daemon.py

**Files:**
- Modify: `src/harness/daemon.py:1-30` (imports and new types)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write the failing test for request decoding** (2-5 min)

```python
# tests/harness/test_daemon.py - add at top of file or in new test class
import msgspec
from harness.daemon import Request, TaskClaimRequest, ExecRequest

def test_task_claim_request_decodes():
    raw = b'{"command": "task_claim", "worker_id": "worker-1"}'
    req = msgspec.json.decode(raw, type=Request)
    assert isinstance(req, TaskClaimRequest)
    assert req.worker_id == "worker-1"

def test_task_claim_request_rejects_empty_worker_id():
    raw = b'{"command": "task_claim", "worker_id": "  "}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)

def test_exec_request_rejects_negative_timeout():
    raw = b'{"command": "exec", "args": ["ls"], "timeout": -5}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)

def test_request_rejects_unknown_command():
    raw = b'{"command": "unknown_cmd"}'
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(raw, type=Request)
```

**Step 2: Run tests to verify they fail** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_task_claim_request_decodes -v
```

Expected: FAIL with `ImportError: cannot import name 'Request' from 'harness.daemon'`

**Step 3: Add imports and request types to daemon.py** (5 min)

Add after line 16 (`import msgspec`):

```python
from typing import Annotated, Literal
from msgspec import Meta, Struct, field

# -- Request Types (Tagged Union) --

class GetStateRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="get_state", tag_field="command"):
    pass

class StatusRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="status", tag_field="command"):
    event_count: int = 10

class UpdateStateRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="update_state", tag_field="command"):
    updates: dict[str, object]

class GitRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="git", tag_field="command"):
    args: list[str] = field(default_factory=list)
    cwd: str | None = None

class PingRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="ping", tag_field="command"):
    pass

class ShutdownRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="shutdown", tag_field="command"):
    pass

class TaskClaimRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="task_claim", tag_field="command"):
    worker_id: str

    def __post_init__(self) -> None:
        if not self.worker_id or not self.worker_id.strip():
            raise msgspec.ValidationError("worker_id cannot be empty or whitespace")

class TaskCompleteRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="task_complete", tag_field="command"):
    task_id: str
    worker_id: str

    def __post_init__(self) -> None:
        if not self.task_id or not self.task_id.strip():
            raise msgspec.ValidationError("task_id cannot be empty or whitespace")
        if not self.worker_id or not self.worker_id.strip():
            raise msgspec.ValidationError("worker_id cannot be empty or whitespace")

class ExecRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="exec", tag_field="command"):
    args: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: Annotated[float, Meta(gt=0)] | None = None
    exclusive: bool = False

class PlanImportRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="plan_import", tag_field="command"):
    content: str

    def __post_init__(self) -> None:
        if not self.content or not self.content.strip():
            raise msgspec.ValidationError("content cannot be empty or whitespace")

class PlanResetRequest(Struct, forbid_unknown_fields=True, frozen=True, tag="plan_reset", tag_field="command"):
    pass

type Request = (
    GetStateRequest | StatusRequest | UpdateStateRequest | GitRequest |
    PingRequest | ShutdownRequest | TaskClaimRequest | TaskCompleteRequest |
    ExecRequest | PlanImportRequest | PlanResetRequest
)
```

**Step 4: Run tests to verify they pass** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_task_claim_request_decodes tests/harness/test_daemon.py::test_task_claim_request_rejects_empty_worker_id tests/harness/test_daemon.py::test_exec_request_rejects_negative_timeout tests/harness/test_daemon.py::test_request_rejects_unknown_command -v
```

Expected: PASS (4 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "feat(daemon): add typed request structs with validation"
```

---

### Task 2: Add Response Types to daemon.py

**Files:**
- Modify: `src/harness/daemon.py` (after Request types)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write the failing test for response types** (2-5 min)

```python
from harness.daemon import Ok, Err, PingData, Result
import msgspec

def test_ok_response_serializes():
    response: Result[PingData] = Ok(data=PingData(running=True, pid=1234))
    encoded = msgspec.json.encode(response)
    assert b'"status":"ok"' in encoded
    assert b'"running":true' in encoded

def test_err_response_serializes():
    response: Result[PingData] = Err(message="Something failed")
    encoded = msgspec.json.encode(response)
    assert b'"status":"error"' in encoded
    assert b'"message":"Something failed"' in encoded
```

**Step 2: Run tests to verify they fail** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_ok_response_serializes -v
```

Expected: FAIL with `ImportError: cannot import name 'Ok' from 'harness.daemon'`

**Step 3: Add response types after request types** (5 min)

```python
# -- Response Types (Result ADT) --

class Ok[T](Struct, frozen=True, tag="ok", tag_field="status"):
    data: T

class Err(Struct, frozen=True, tag="error", tag_field="status"):
    message: str

type Result[T] = Ok[T] | Err

# -- Response Data Types --

class PingData(Struct, frozen=True):
    running: Literal[True]
    pid: int

class ShutdownData(Struct, frozen=True):
    shutdown: Literal[True]

class StatusSummary(Struct, frozen=True):
    total: int
    completed: int
    running: int
    pending: int
    failed: int

class StatusData(Struct, frozen=True):
    active: bool
    summary: StatusSummary
    tasks: dict[str, object]
    events: list[dict[str, object]]
    active_workers: list[str]

class GetStateData(Struct, frozen=True):
    state: object  # WorkflowState as builtins or None

class UpdateStateData(Struct, frozen=True):
    state: object  # Updated WorkflowState as builtins

class GitData(Struct, frozen=True):
    returncode: int
    stdout: str
    stderr: str

class ExecData(Struct, frozen=True):
    returncode: int
    stdout: str
    stderr: str
    signal_name: str | None = None

class TaskClaimData(Struct, frozen=True):
    task: object  # Task as builtins or None
    is_retry: bool = False
    is_reclaim: bool = False

class TaskCompleteData(Struct, frozen=True):
    task_id: str

class PlanImportData(Struct, frozen=True):
    goal: str
    task_count: int

class PlanResetData(Struct, frozen=True):
    message: str
```

**Step 4: Run tests to verify they pass** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_ok_response_serializes tests/harness/test_daemon.py::test_err_response_serializes -v
```

Expected: PASS (2 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "feat(daemon): add typed response structs with Result ADT"
```

---

### Task 3: Refactor HarnessHandler.handle() and dispatch()

**Files:**
- Modify: `src/harness/daemon.py:29-76` (HarnessHandler.handle and dispatch)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write the failing test for typed dispatch** (2-5 min)

```python
def test_dispatch_returns_typed_error_for_invalid_json():
    # Create a mock handler to test dispatch directly
    handler = HarnessHandler.__new__(HarnessHandler)
    handler.server = None  # Will set up properly in integration

    # Invalid JSON should return Err
    result = handler.dispatch(b'not valid json')
    decoded = msgspec.json.decode(result)
    assert decoded["status"] == "error"
    assert "message" in decoded
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_dispatch_returns_typed_error_for_invalid_json -v
```

Expected: FAIL (dispatch signature mismatch or different behavior)

**Step 3: Refactor handle() and dispatch() methods** (5 min)

Replace the existing `handle()` and `dispatch()` methods:

```python
class HarnessHandler(socketserver.StreamRequestHandler):
    server: HarnessDaemon

    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return
            response = self.dispatch(line)
            self.wfile.write(response + b"\n")
        except Exception as e:
            error_response = msgspec.json.encode(Err(message=str(e)))
            self.wfile.write(error_response + b"\n")

    def dispatch(self, raw: bytes) -> bytes:
        try:
            request = msgspec.json.decode(raw, type=Request)
        except msgspec.ValidationError as e:
            return msgspec.json.encode(Err(message=str(e)))
        except msgspec.DecodeError as e:
            return msgspec.json.encode(Err(message=f"Invalid JSON: {e}"))

        server = self.server

        match request:
            case GetStateRequest():
                return msgspec.json.encode(self._handle_get_state(request, server))
            case StatusRequest():
                return msgspec.json.encode(self._handle_status(request, server))
            case UpdateStateRequest():
                return msgspec.json.encode(self._handle_update_state(request, server))
            case GitRequest():
                return msgspec.json.encode(self._handle_git(request, server))
            case PingRequest():
                return msgspec.json.encode(self._handle_ping(request, server))
            case ShutdownRequest():
                return msgspec.json.encode(self._handle_shutdown(request, server))
            case TaskClaimRequest():
                return msgspec.json.encode(self._handle_task_claim(request, server))
            case TaskCompleteRequest():
                return msgspec.json.encode(self._handle_task_complete(request, server))
            case ExecRequest():
                return msgspec.json.encode(self._handle_exec(request, server))
            case PlanImportRequest():
                return msgspec.json.encode(self._handle_plan_import(request, server))
            case PlanResetRequest():
                return msgspec.json.encode(self._handle_plan_reset(request, server))
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_dispatch_returns_typed_error_for_invalid_json -v
```

Expected: PASS

**Step 5: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "refactor(daemon): type-safe dispatch with pattern matching"
```

---

### Task 4: Refactor All Handler Methods (Part 1: Simple Handlers)

**Files:**
- Modify: `src/harness/daemon.py` (handler methods)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write tests for simple handlers** (2-5 min)

```python
def test_ping_handler_returns_typed_response(daemon_server):
    # daemon_server is a pytest fixture providing running daemon
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(daemon_server.socket_path)
    sock.sendall(b'{"command": "ping"}\n')
    response = sock.recv(4096)
    sock.close()

    decoded = msgspec.json.decode(response)
    assert decoded["status"] == "ok"
    assert decoded["data"]["running"] is True
    assert isinstance(decoded["data"]["pid"], int)
```

**Step 2: Run test to verify behavior** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_ping_handler_returns_typed_response -v
```

**Step 3: Refactor simple handler methods** (5 min)

```python
def _handle_get_state(self, _request: GetStateRequest, server: HarnessDaemon) -> Result[GetStateData]:
    state = server.state_manager.load()
    if state is None:
        return Ok(data=GetStateData(state=None))
    return Ok(data=GetStateData(state=msgspec.to_builtins(state)))

def _handle_ping(self, _request: PingRequest, _server: HarnessDaemon) -> Result[PingData]:
    return Ok(data=PingData(running=True, pid=os.getpid()))

def _handle_shutdown(self, _request: ShutdownRequest, server: HarnessDaemon) -> Result[ShutdownData]:
    threading.Thread(target=server.shutdown, daemon=True).start()
    return Ok(data=ShutdownData(shutdown=True))

def _handle_git(self, request: GitRequest, server: HarnessDaemon) -> Result[GitData]:
    cwd = request.cwd or str(server.worktree_root)
    result = safe_git_exec(request.args, cwd)
    return Ok(data=GitData(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    ))

def _handle_plan_reset(self, _request: PlanResetRequest, server: HarnessDaemon) -> Result[PlanResetData]:
    server.state_manager.reset()
    server.trajectory_logger.log({"event_type": "plan_reset"})
    if server.acp_emitter:
        server.acp_emitter.emit({"event_type": "plan_reset"})
    return Ok(data=PlanResetData(message="Workflow state cleared"))
```

**Step 4: Run tests to verify** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py -k "ping" -v
```

Expected: PASS

**Step 5: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "refactor(daemon): typed handlers for simple commands"
```

---

### Task 5: Refactor All Handler Methods (Part 2: Complex Handlers)

**Files:**
- Modify: `src/harness/daemon.py` (remaining handlers)
- Test: `tests/harness/test_daemon.py`

**Step 1: Write tests for complex handlers** (2-5 min)

```python
def test_task_claim_returns_typed_response(daemon_with_plan):
    # daemon_with_plan has an imported plan with tasks
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(daemon_with_plan.socket_path)
    sock.sendall(b'{"command": "task_claim", "worker_id": "test-worker"}\n')
    response = sock.recv(4096)
    sock.close()

    decoded = msgspec.json.decode(response)
    assert decoded["status"] == "ok"
    assert "task" in decoded["data"]
    assert "is_retry" in decoded["data"]
    assert "is_reclaim" in decoded["data"]
```

**Step 2: Run test to verify** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py::test_task_claim_returns_typed_response -v
```

**Step 3: Refactor complex handlers** (5 min)

```python
def _handle_status(self, request: StatusRequest, server: HarnessDaemon) -> Result[StatusData]:
    from .state import TaskStatus

    state = server.state_manager.load()

    if state is None:
        return Ok(data=StatusData(
            active=False,
            summary=StatusSummary(total=0, completed=0, running=0, pending=0, failed=0),
            tasks={},
            events=[],
            active_workers=[],
        ))

    tasks = state.tasks
    completed = running = pending = failed = 0
    active_workers: set[str] = set()

    for task in tasks.values():
        match task.status:
            case TaskStatus.COMPLETED:
                completed += 1
            case TaskStatus.RUNNING:
                running += 1
                if task.claimed_by:
                    active_workers.add(task.claimed_by)
            case TaskStatus.PENDING:
                pending += 1
            case TaskStatus.FAILED:
                failed += 1

    events = server.trajectory_logger.tail(n=request.event_count)

    return Ok(data=StatusData(
        active=True,
        summary=StatusSummary(
            total=len(tasks),
            completed=completed,
            running=running,
            pending=pending,
            failed=failed,
        ),
        tasks={tid: msgspec.to_builtins(t) for tid, t in tasks.items()},
        events=events,
        active_workers=list(active_workers),
    ))

def _handle_update_state(self, request: UpdateStateRequest, server: HarnessDaemon) -> Result[UpdateStateData]:
    if not request.updates:
        return Err(message="No updates provided")
    try:
        updated = server.state_manager.update(**request.updates)
        return Ok(data=UpdateStateData(state=msgspec.to_builtins(updated)))
    except Exception as e:
        return Err(message=str(e))

def _handle_task_claim(self, request: TaskClaimRequest, server: HarnessDaemon) -> Result[TaskClaimData]:
    try:
        claim_result = server.state_manager.claim_task(request.worker_id)

        if not claim_result.task:
            return Ok(data=TaskClaimData(task=None))

        task = claim_result.task

        server.trajectory_logger.log({
            "event_type": "task_claim",
            "task_id": task.id,
            "worker_id": request.worker_id,
            "is_retry": claim_result.is_retry,
            "is_reclaim": claim_result.is_reclaim,
        })
        if server.acp_emitter:
            server.acp_emitter.emit({
                "event_type": "task_claim",
                "task_id": task.id,
                "worker_id": request.worker_id,
            })

        return Ok(data=TaskClaimData(
            task=msgspec.to_builtins(task),
            is_retry=claim_result.is_retry,
            is_reclaim=claim_result.is_reclaim,
        ))
    except Exception as e:
        return Err(message=str(e))

def _handle_task_complete(self, request: TaskCompleteRequest, server: HarnessDaemon) -> Result[TaskCompleteData]:
    try:
        server.state_manager.complete_task(request.task_id, request.worker_id)

        server.trajectory_logger.log({
            "event_type": "task_complete",
            "task_id": request.task_id,
            "worker_id": request.worker_id,
        })
        if server.acp_emitter:
            server.acp_emitter.emit({
                "event_type": "task_complete",
                "task_id": request.task_id,
            })

        return Ok(data=TaskCompleteData(task_id=request.task_id))
    except Exception as e:
        return Err(message=str(e))

def _handle_exec(self, request: ExecRequest, server: HarnessDaemon) -> Result[ExecData]:
    try:
        start_time = time.monotonic()
        result = server.runtime.execute(
            command=request.args,
            cwd=request.cwd,
            env=request.env,
            timeout=request.timeout,
            exclusive=request.exclusive,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)

        signal_name = decode_signal(result.returncode) if result.returncode < 0 else None

        server.trajectory_logger.log({
            "event_type": "exec",
            "args": request.args,
            "returncode": result.returncode,
            "signal_name": signal_name,
            "stdout": result.stdout[:TRUNCATE_LIMIT] if result.stdout else "",
            "stderr": result.stderr[:TRUNCATE_LIMIT] if result.stderr else "",
            "duration_ms": duration_ms,
        })

        return Ok(data=ExecData(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            signal_name=signal_name,
        ))
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        signal_name = "SIGTERM"
        server.trajectory_logger.log({
            "event_type": "exec",
            "args": request.args,
            "returncode": -15,
            "signal_name": signal_name,
            "timeout": True,
            "duration_ms": duration_ms,
        })
        return Ok(data=ExecData(
            returncode=-15,
            stdout=e.stdout.decode() if e.stdout else "",
            stderr=e.stderr.decode() if e.stderr else "",
            signal_name=signal_name,
        ))
    except Exception as e:
        return Err(message=str(e))

def _handle_plan_import(self, request: PlanImportRequest, server: HarnessDaemon) -> Result[PlanImportData]:
    try:
        plan = parse_plan_content(request.content)
        state = plan.to_workflow_state()
        server.state_manager.save(state)

        server.trajectory_logger.log({
            "event_type": "plan_import",
            "goal": plan.goal,
            "task_count": len(plan.tasks),
        })
        if server.acp_emitter:
            server.acp_emitter.emit({
                "event_type": "plan_import",
                "goal": plan.goal,
                "task_count": len(plan.tasks),
            })

        return Ok(data=PlanImportData(goal=plan.goal, task_count=len(plan.tasks)))
    except ValueError as e:
        msg = str(e)
        if "No valid plan found" in msg:
            msg += ". Run 'harness plan template' to see the required format."
        return Err(message=msg)
```

**Step 4: Run all daemon tests** (30 sec)

```bash
uv run pytest tests/harness/test_daemon.py -v
```

Expected: PASS

**Step 5: Commit** (30 sec)

```bash
git add src/harness/daemon.py tests/harness/test_daemon.py
git commit -m "refactor(daemon): typed handlers for all commands"
```

---

### Task 6: Update client.py for New Wire Format

**Files:**
- Modify: `src/harness/client.py`
- Test: `tests/harness/test_client.py` (if exists)

**Step 1: Review client's send_rpc function** (2 min)

Read `src/harness/client.py` to understand current response handling.

**Step 2: Update send_rpc to use msgspec decoding** (5 min)

The client should decode responses and handle `Ok`/`Err` pattern. Update the response handling in each `_cmd_*` function to work with the new typed format.

**Step 3: Run integration tests** (30 sec)

```bash
uv run pytest tests/harness/ -v
```

**Step 4: Commit** (30 sec)

```bash
git add src/harness/client.py
git commit -m "refactor(client): decode typed responses"
```

---

### Task 7: Code Review

Final review of all changes before merge.
