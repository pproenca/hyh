# Plan Template Command Implementation

**Goal:** Implement `harness plan template` command that outputs the JSON schema for plan files, enabling agents to self-heal when encountering legacy markdown plans.

**Architecture:** Add `get_plan_schema()` function to `plan.py` that generates JSON schema from Pydantic models. Expose it via `harness plan template` CLI command (client-side only, no RPC needed). Improve error messages in daemon to guide agents toward using the template.

---

### Task 1: Add schema generation to plan.py

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/plan.py:96` (after `parse_plan_content`)
- Test: `tests/harness/test_plan.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_get_plan_schema_returns_valid_json():
       """Schema generation produces parseable JSON with expected keys."""
       from harness.plan import get_plan_schema
       import json

       schema = get_plan_schema()
       data = json.loads(schema)

       assert "properties" in data
       assert "goal" in data["properties"]
       assert "tasks" in data["properties"]

   def test_get_plan_schema_includes_task_fields():
       """Schema includes all PlanTaskDefinition fields."""
       from harness.plan import get_plan_schema
       import json

       schema = get_plan_schema()
       data = json.loads(schema)

       # Navigate to task definition
       task_schema = data["$defs"]["PlanTaskDefinition"]["properties"]
       assert "description" in task_schema
       assert "dependencies" in task_schema
       assert "timeout_seconds" in task_schema
       assert "instructions" in task_schema
       assert "role" in task_schema
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_plan.py::test_get_plan_schema_returns_valid_json -v
   ```

3. **Implement MINIMAL code:**
   ```python
   def get_plan_schema() -> str:
       """Generate JSON schema for plan format.

       Provides the 'instruction manual' for agents to understand
       how to structure valid JSON plans.
       """
       schema = PlanDefinition.model_json_schema()
       return json.dumps(schema, indent=2)
   ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(plan): add schema generation for self-healing agents"
   ```

---

### Task 2: Add template subcommand to client.py

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/client.py:311-314` (plan subparser section)
- Modify: `src/harness/client.py:378` (plan command routing)
- Test: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_plan_template_outputs_schema(capsys):
       """harness plan template prints valid JSON schema."""
       import subprocess
       import json
       import sys

       result = subprocess.run(
           [sys.executable, "-m", "harness.client", "plan", "template"],
           capture_output=True,
           text=True,
       )

       assert result.returncode == 0
       data = json.loads(result.stdout)
       assert "properties" in data
       assert "goal" in data["properties"]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_client.py::test_plan_template_outputs_schema -v
   ```

3. **Implement MINIMAL code:**

   Add import at top of client.py:
   ```python
   from harness.plan import get_plan_schema
   ```

   Add subparser (after import subparser):
   ```python
   plan_sub.add_parser("template", help="Print JSON schema for plan format")
   ```

   Add routing (in plan command section):
   ```python
   elif args.plan_command == "template":
       _cmd_plan_template()
   ```

   Add handler function:
   ```python
   def _cmd_plan_template() -> None:
       """Print plan JSON schema for agent self-healing."""
       print(get_plan_schema())
   ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "feat(client): add 'plan template' command for schema output"
   ```

---

### Task 3: Improve error message for legacy plans

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `src/harness/daemon.py:337-339` (_handle_plan_import error handling)
- Test: `tests/harness/test_daemon.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_plan_import_legacy_markdown_gives_helpful_error(tmp_path):
       """Legacy markdown plans get actionable error message."""
       from harness.daemon import HarnessHandler, HarnessDaemon

       legacy_content = """# My Plan

       ## Task 1: Do something
       - [ ] Step one
       - [ ] Step two
       """

       handler = HarnessHandler(None, None, None)
       # Mock minimal server
       class MockServer:
           pass
       server = MockServer()

       result = handler._handle_plan_import(
           {"content": legacy_content},
           server
       )

       assert result["status"] == "error"
       assert "No JSON plan block found" in result["message"]
       assert "harness plan template" in result["message"]
   ```

2. **Run test, verify FAILURE:**
   ```bash
   pytest tests/harness/test_daemon.py::test_plan_import_legacy_markdown_gives_helpful_error -v
   ```

3. **Implement MINIMAL code:**
   ```python
   except ValueError as e:
       msg = str(e)
       if "No JSON plan block" in msg:
           msg += ". Run 'harness plan template' to see the required JSON schema."
       return {"status": "error", "message": msg}
   ```

4. **Run test, verify PASS**

5. **Commit:**
   ```bash
   git add -A && git commit -m "fix(daemon): guide agents to template on legacy plan import"
   ```

---

### Task 4: Client import constraint verification

**Effort:** simple (3-10 tool calls)

**Files:**
- Modify: `tests/harness/test_client.py`

**TDD Instructions (MANDATORY):**

1. **Write test FIRST:**
   ```python
   def test_client_plan_template_does_not_break_import_constraints():
       """Adding plan import doesn't violate stdlib-only rule.

       The get_plan_schema import is allowed because it's from harness.plan,
       not from pydantic directly.
       """
       import ast
       from pathlib import Path

       client_source = Path("src/harness/client.py").read_text()
       tree = ast.parse(client_source)

       imports = []
       for node in ast.walk(tree):
           if isinstance(node, ast.Import):
               for alias in node.names:
                   imports.append(alias.name)
           elif isinstance(node, ast.ImportFrom):
               if node.module:
                   imports.append(node.module)

       # harness.plan is allowed (it's our code)
       # pydantic direct import is NOT allowed
       assert "pydantic" not in imports
       assert "harness.plan" in imports
   ```

2. **Run test, verify PASS** (should pass if Task 2 done correctly)

3. **Commit:**
   ```bash
   git add -A && git commit -m "test(client): verify plan import doesn't violate stdlib constraint"
   ```

---

### Task 5: Code Review

**Effort:** simple (3-10 tool calls)

**Files:**
- Review: `src/harness/plan.py`
- Review: `src/harness/client.py`
- Review: `src/harness/daemon.py`

**Instructions:**

1. **Run full test suite:**
   ```bash
   pytest tests/harness/test_plan.py tests/harness/test_client.py tests/harness/test_daemon.py -v
   ```

2. **Verify client startup time still <50ms:**
   ```bash
   pytest tests/harness/test_client.py::test_client_startup_time -v
   ```

3. **Review changes for CLAUDE.md compliance:**
   - No `Any` types added
   - No asyncio introduced
   - Client still stdlib-only at runtime (import is lazy via function call)

4. **Final commit if needed:**
   ```bash
   git add -A && git commit -m "chore: final review cleanup"
   ```

---

## Parallel Execution Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 2, 3 | Independent: plan.py, client.py, daemon.py have no file overlap |
| Group 2 | 4 | Depends on Task 2 completion |
| Group 3 | 5 | Final review after all implementation |
