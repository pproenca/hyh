# Lease Renewal Bug Fix Implementation Plan

**Goal:** Fix the lease renewal bug in `claim_task` where idempotent retries don't renew `started_at`, causing race conditions with task stealing.

**Architecture:** The fix modifies `StateManager.claim_task()` to always renew `started_at` when returning an existing task to the same worker. This ensures that legitimate retry attempts refresh the lease, preventing other workers from stealing a task that appears timed out but is actually still being worked on.

---

## Background

When a worker crashes and retries `claim_task(same_worker_id)`:
1. Current code checks `if task.claimed_by != worker_id` - this is FALSE for retries
2. `started_at` is never updated on the retry path
3. Other workers see an apparently timed-out task and steal it
4. Two workers execute the same task - data corruption

## Tasks

### Task 1: Write failing test for lease renewal bug

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_state.py` (add new test after line 491)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_claim_task_renews_lease_on_retry(tmp_path):
       """claim_task should renew started_at on idempotent retry to prevent task stealing."""
       from datetime import timedelta

       manager = StateManager(tmp_path)
       old_time = datetime.now() - timedelta(minutes=5)
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Task 1",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   claimed_by="worker-1",
                   started_at=old_time,
               ),
           }
       )
       manager.save(state)

       # Retry claim with same worker
       before_claim = datetime.now()
       task = manager.claim_task("worker-1")

       assert task is not None
       assert task.id == "task-1"
       assert task.claimed_by == "worker-1"
       # Critical: started_at must be renewed
       assert task.started_at >= before_claim, "Lease was not renewed on retry"
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_state.py::test_claim_task_renews_lease_on_retry -v
   ```
   Expected: `AssertionError: Lease was not renewed on retry`

3. **Do NOT implement yet** - proceed to Task 2

4. **Commit:**
   ```bash
   git add -A && git commit -m "test(state): add failing test for lease renewal bug"
   ```

---

### Task 2: Fix claim_task to renew lease on retry

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/state.py:179-213`

**TDD Instructions (MANDATORY):**

1. **Implement the fix** in `claim_task()`:

   The fix restructures the logic:
   - ALWAYS renew `started_at = datetime.now()` when returning a task
   - Only set `status` and `claimed_by` on NEW claims
   - Persist on both new claims AND retries (lease renewal requires persistence)

   ```python
   def claim_task(self, worker_id: str) -> Task | None:
       """Atomically claim a task for worker (find + update + save in one critical section)."""
       with self._lock:
           # Auto-load if state not in memory
           if not self._state:
               if self.state_file.exists():
                   content = self.state_file.read_text()
                   data = json.loads(content)
                   self._state = WorkflowState(**data)
               if not self._state:
                   raise ValueError("No state loaded and no state file exists")

           # Check if worker already has a task (idempotency)
           task = self._state.get_task_for_worker(worker_id)
           if not task:
               return None

           is_new_claim = task.claimed_by != worker_id

           # ALWAYS renew the lease (prevents task stealing on retry)
           now = datetime.now()
           task.started_at = now

           if is_new_claim:
               # New claim: set ownership
               task.status = TaskStatus.RUNNING
               task.claimed_by = worker_id

           # Update state and save (both new claims AND renewals)
           self._state.tasks[task.id] = task
           content = self._state.model_dump_json(indent=2)
           temp_file = self.state_file.with_suffix(".tmp")
           with open(temp_file, "w") as f:
               f.write(content)
               f.flush()
               os.fsync(f.fileno())
           temp_file.rename(self.state_file)

           return task
   ```

2. **Run test, verify PASS:**
   ```bash
   pytest tests/harness/test_state.py::test_claim_task_renews_lease_on_retry -v
   ```

3. **Run ALL state tests to ensure no regressions:**
   ```bash
   pytest tests/harness/test_state.py -v
   ```

4. **Commit:**
   ```bash
   git add -A && git commit -m "fix(state): renew lease on idempotent claim_task retry"
   ```

---

### Task 3: Write integration test for race condition scenario

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `tests/harness/test_state.py` (add new test)

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_claim_task_lease_renewal_prevents_stealing(tmp_path):
       """Verify that lease renewal prevents another worker from stealing a task."""
       from datetime import timedelta

       manager = StateManager(tmp_path)
       # Task with nearly-expired lease (9 minutes old, 10 min timeout)
       nearly_expired = datetime.now() - timedelta(minutes=9)
       state = WorkflowState(
           tasks={
               "task-1": Task(
                   id="task-1",
                   description="Task 1",
                   status=TaskStatus.RUNNING,
                   dependencies=[],
                   claimed_by="worker-A",
                   started_at=nearly_expired,
                   timeout_seconds=600,  # 10 minutes
               ),
           }
       )
       manager.save(state)

       # Worker A retries (simulating crash recovery)
       task_a = manager.claim_task("worker-A")
       assert task_a is not None
       assert task_a.started_at > nearly_expired, "Lease must be renewed"

       # Worker B tries to claim - should get None (no claimable tasks)
       task_b = manager.claim_task("worker-B")
       assert task_b is None, "Worker B should not steal task after lease renewal"
   ```

2. **Run test, verify PASS** (should pass with Task 2's fix):
   ```bash
   pytest tests/harness/test_state.py::test_claim_task_lease_renewal_prevents_stealing -v
   ```

3. **Commit:**
   ```bash
   git add -A && git commit -m "test(state): add integration test for lease renewal preventing theft"
   ```

---

### Task 4: Run full test suite and verify no regressions

**Effort:** simple (3-10 tool calls)

**Files:**
- None (verification only)

**TDD Instructions (MANDATORY):**

1. **Run full test suite:**
   ```bash
   pytest tests/ -v
   ```

2. **Verify all tests pass**

3. **If any failures:** Debug and fix before proceeding

4. **No commit** (verification task only)

---

### Task 5: Code Review

**Effort:** standard (10-15 tool calls)

**Files:**
- Review: `src/harness/state.py:179-213`
- Review: `tests/harness/test_state.py` (new tests)

**Review Checklist:**

1. **Thread Safety:** Lease renewal happens under `self._lock`
2. **Atomic Persistence:** Uses tmp-fsync-rename pattern
3. **Idempotency:** Same worker gets same task with renewed lease
4. **No Over-engineering:** Minimal change, no unnecessary abstractions
5. **Lock Hierarchy:** Release-then-log pattern followed (no logging under lock)
6. **Monotonic Time:** Uses `datetime.now()` (acceptable per codebase convention)

---

## Parallel Execution

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Write failing test |
| Group 2 | 2 | Implement fix (depends on Group 1) |
| Group 3 | 3 | Additional integration test (depends on Group 2) |
| Group 4 | 4 | Full test suite (depends on Group 3) |
| Group 5 | 5 | Code review (depends on Group 4) |

Note: This bug fix is inherently sequential - each task depends on the previous.

---

## Accepted Constraints

As noted in the feedback:
- **Recursion Limits:** `validate_dag` uses recursion. For v2.0, deeply nested plans (>1000 steps) will cause stack overflow. This is accepted.
