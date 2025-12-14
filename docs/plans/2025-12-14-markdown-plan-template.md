# Markdown Plan Template Implementation

**Goal:** Change `harness plan template` to output a Markdown template with example structure and a complete filled-in example, replacing the current JSON schema output for better LLM comprehension.

**Architecture:** Replace `get_plan_schema()` with `get_plan_template()` in `plan.py` that generates a Markdown document containing: (1) a template structure with placeholders, and (2) a realistic complete example. The client command handler simply prints this Markdown string. The JSON fenced-code block format remains the internal plan representation that agents submit.

---

### Task 1: Replace schema function with Markdown template generator

**Effort:** standard (10-15 tool calls)

**Files:**
- Modify: `src/harness/plan.py:100-108`
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_get_plan_template_returns_markdown():
       """Template generation produces Markdown with structure and example."""
       from harness.plan import get_plan_template

       template = get_plan_template()

       # Verify it's Markdown with expected sections
       assert "# Plan Template" in template
       assert "## Template Structure" in template
       assert "## Complete Example" in template
       # Verify JSON blocks are present
       assert "```json" in template
       assert '"goal":' in template
       assert '"tasks":' in template
       assert '"description":' in template
       assert '"dependencies":' in template

   def test_get_plan_template_includes_all_fields():
       """Template documents all PlanTaskDefinition fields."""
       from harness.plan import get_plan_template

       template = get_plan_template()

       # All task fields should be mentioned
       assert "timeout_seconds" in template
       assert "instructions" in template
       assert "role" in template
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py::test_get_plan_template_returns_markdown -v
   ```

3. **Implement MINIMAL code:**
   ```python
   def get_plan_template() -> str:
       """Generate Markdown template for plan format.

       Provides LLM-friendly documentation with:
       1. Template structure showing all fields
       2. Complete realistic example
       """
       return '''\
   # Plan Template

   Submit plans as a JSON block inside markdown fences.

   ## Template Structure

   ```json
   {
     "goal": "<one-sentence description of what this plan achieves>",
     "tasks": {
       "<task_id>": {
         "description": "<what this task does>",
         "dependencies": ["<task_id>", "..."],
         "timeout_seconds": 600,
         "instructions": "<detailed step-by-step instructions or null>",
         "role": "<specialist role like 'backend' or 'frontend' or null>"
       }
     }
   }
   ```

   **Field Reference:**
   - `goal`: High-level objective (required)
   - `tasks`: Dictionary keyed by unique task IDs (required)
   - `description`: Brief task summary (required)
   - `dependencies`: List of task IDs that must complete first (default: [])
   - `timeout_seconds`: Max execution time in seconds (default: 600)
   - `instructions`: Detailed guidance for the agent (optional)
   - `role`: Specialist designation for task routing (optional)

   ## Complete Example

   ```json
   {
     "goal": "Add user authentication with JWT tokens",
     "tasks": {
       "models": {
         "description": "Create User model with password hashing",
         "dependencies": [],
         "timeout_seconds": 300,
         "instructions": "Use bcrypt for password hashing. Include email, hashed_password, created_at fields.",
         "role": "backend"
       },
       "auth-endpoints": {
         "description": "Implement /login and /register endpoints",
         "dependencies": ["models"],
         "timeout_seconds": 600,
         "instructions": "Return JWT on successful login. Validate email format on register.",
         "role": "backend"
       },
       "auth-tests": {
         "description": "Write integration tests for auth flow",
         "dependencies": ["auth-endpoints"],
         "timeout_seconds": 300,
         "instructions": "Test: valid login, invalid password, duplicate registration, token expiry.",
         "role": "backend"
       }
     }
   }
   ```
   '''
   ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): replace JSON schema with Markdown template"
   ```

---

### Task 2: Update client command to use new template function

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/client.py:662-667`
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_plan_template_outputs_markdown():
       """harness plan template prints Markdown with template and example."""
       import subprocess
       import sys

       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "template"],
           capture_output=True,
           text=True,
       )

       assert result.returncode == 0
       assert "# Plan Template" in result.stdout
       assert "## Template Structure" in result.stdout
       assert "## Complete Example" in result.stdout
       assert "```json" in result.stdout
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_template_outputs_markdown -v
   ```

3. **Implement MINIMAL code:**

   Update import and handler in `client.py`:
   ```python
   # Change import (lazy, inside function)
   def _cmd_plan_template() -> None:
       """Print plan Markdown template for LLM consumption."""
       from harness.plan import get_plan_template
       print(get_plan_template())
   ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): update template command for Markdown output"
   ```

---

### Task 3: Remove deprecated get_plan_schema function

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/plan.py`
- Modify: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Search for usages:**
   ```bash
   grep -r "get_plan_schema" src/ tests/
   ```

2. **Update tests to use new function:**
   - Remove `test_get_plan_schema_returns_valid_json`
   - Remove `test_get_plan_schema_includes_task_fields`
   - (Already replaced by Task 1 tests)

3. **Remove deprecated function from plan.py:**
   Delete `get_plan_schema()` function

4. **Verify no broken imports:**
   ```bash
   pytest tests/harness/test_plan.py tests/harness/test_client.py -v
   ```

5. **Commit:**
   ```bash
   git add -A && git commit -m "refactor(plan): remove deprecated get_plan_schema"
   ```

---

### Task 4: Update daemon error message to reference template

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:337-339`
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_plan_import_error_references_markdown_template(tmp_path):
       """Plan import errors mention the template command for guidance."""
       # (Test already exists but verify message still correct)
       # Error message should reference 'harness plan template'
   ```

2. **Verify existing test still passes:**
   ```bash
   pytest tests/harness/test_daemon.py::test_plan_import_legacy_markdown_gives_helpful_error -v
   ```

3. **No code change needed if message already says "Run 'harness plan template'"**

4. **Commit if any cleanup needed:**
   ```bash
   git add -A && git commit -m "docs(daemon): verify template reference in error messages"
   ```

---

### Task 5: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- Review: `src/harness/plan.py`
- Review: `src/harness/client.py`
- Review: `tests/harness/test_plan.py`
- Review: `tests/harness/test_client.py`

**Instructions:**

1. **Run full test suite:**
   ```bash
   pytest tests/harness/ -v
   ```

2. **Verify client startup time still <50ms:**
   ```bash
   pytest tests/harness/test_client.py::test_client_startup_time -v
   ```

3. **Verify lazy import is preserved:**
   The `get_plan_template` import must remain inside `_cmd_plan_template()` function to avoid loading Pydantic at client startup.

4. **Review for CLAUDE.md compliance:**
   - No `Any` types added
   - No asyncio introduced
   - Client still stdlib-only at module load time

5. **Final commit if needed:**
   ```bash
   git add -A && git commit -m "chore: final review cleanup for markdown template"
   ```

---

## Parallel Execution Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Core template function - foundation for other tasks |
| Group 2 | 2, 3, 4 | Independent: client handler, test cleanup, daemon check - no file overlap after Task 1 |
| Group 3 | 5 | Final review after all implementation |
