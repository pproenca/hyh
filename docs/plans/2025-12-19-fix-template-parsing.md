# Fix Template Parsing Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-19-fix-template-parsing.md` to implement task-by-task.

**Goal:** Make `parse_plan_content()` more forgiving of common LLM output variations while maintaining strict JSON schema validation.

**Architecture:** Simple multi-step extraction: try fenced JSON first, fallback to raw JSON detection, clean trailing commas, then validate with Pydantic. No complex regex - just layered simple patterns.

**Tech Stack:** Python regex, json module, Pydantic validation

---

### Task 1: Add failing tests for edge cases

**Effort:** simple (5 tool calls)

**Files:**
- Modify: `tests/harness/test_plan.py`

**Step 1: Write failing test for raw JSON without fences** (2-5 min)

Add this test after `test_parse_plan_content_extracts_json`:

```python
def test_parse_plan_content_raw_json():
    """parse_plan_content should accept raw JSON without markdown fences."""
    content = '''
{
  "goal": "Test raw JSON",
  "tasks": {
    "task-1": {"description": "First task"}
  }
}
'''
    plan = parse_plan_content(content)
    assert plan.goal == "Test raw JSON"
    assert len(plan.tasks) == 1
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_raw_json -v
```

Expected: FAIL with `ValueError: No JSON plan block found`

**Step 3: Write failing test for trailing comma cleanup** (2-5 min)

```python
def test_parse_plan_content_trailing_comma():
    """parse_plan_content should tolerate trailing commas (common LLM mistake)."""
    content = '''
```json
{
  "goal": "Test trailing comma",
  "tasks": {
    "task-1": {"description": "First task",},
  },
}
```
'''
    plan = parse_plan_content(content)
    assert plan.goal == "Test trailing comma"
```

**Step 4: Run test to verify it fails** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_trailing_comma -v
```

Expected: FAIL with `ValueError: Invalid JSON: Illegal trailing comma`

**Step 5: Commit failing tests** (30 sec)

```bash
git add tests/harness/test_plan.py
git commit -m "test(plan): add failing tests for raw JSON and trailing comma"
```

---

### Task 2: Implement forgiving JSON extraction

**Effort:** standard (10-12 tool calls)

**Files:**
- Modify: `src/harness/plan.py:64-83`

**Step 1: Add helper function for trailing comma cleanup** (2-5 min)

Add before `parse_plan_content` function:

```python
def _clean_json_string(json_str: str) -> str:
    """Remove trailing commas before ] or } (common LLM mistake)."""
    # Pattern: comma followed by optional whitespace then closing bracket
    cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
    return cleaned
```

**Step 2: Refactor parse_plan_content to use multi-step extraction** (3-5 min)

Replace the current `parse_plan_content` function body:

```python
def parse_plan_content(content: str) -> PlanDefinition:
    """Extract JSON plan from LLM output.

    Extraction priority:
    1. Fenced JSON block (```json or ```)
    2. Raw JSON object (first { to matching })

    Tolerates trailing commas (common LLM mistake).
    """
    json_str: str | None = None

    # Try fenced block first (most explicit)
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Fallback: find first JSON object
        # Find first { and match to its closing }
        start = content.find("{")
        if start != -1:
            depth = 0
            in_string = False
            escape_next = False
            end = start

            for i, char in enumerate(content[start:], start):
                if escape_next:
                    escape_next = False
                    continue
                if char == "\\":
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break

            if depth == 0:
                json_str = content[start : end + 1]

    if not json_str:
        raise ValueError("No JSON plan block found")

    # Clean trailing commas
    json_str = _clean_json_string(json_str)

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    plan = PlanDefinition(**data)
    plan.validate_dag()
    return plan
```

**Step 3: Run tests to verify they pass** (30 sec)

```bash
pytest tests/harness/test_plan.py -v
```

Expected: All tests PASS

**Step 4: Run full test suite** (30 sec)

```bash
make test
```

Expected: All tests pass

**Step 5: Commit implementation** (30 sec)

```bash
git add src/harness/plan.py
git commit -m "feat(plan): forgiving JSON extraction with trailing comma cleanup"
```

---

### Task 3: Improve error messages

**Effort:** simple (5 tool calls)

**Files:**
- Modify: `src/harness/plan.py`

**Step 1: Write test for helpful error on bad structure** (2-5 min)

```python
def test_parse_plan_content_missing_goal():
    """parse_plan_content should give helpful error for missing required field."""
    content = '''
{
  "tasks": {"t1": {"description": "Task 1"}}
}
'''
    with pytest.raises(ValueError, match="goal"):
        parse_plan_content(content)
```

**Step 2: Run test to verify current behavior** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_missing_goal -v
```

Expected: Should already pass (Pydantic catches this). If not, update exception handling.

**Step 3: Improve error wrapping in parse_plan_content** (2-5 min)

Update the exception handling at the end of `parse_plan_content`:

```python
    try:
        plan = PlanDefinition(**data)
    except ValidationError as e:
        # Extract the first error message for cleaner output
        errors = e.errors()
        if errors:
            field = ".".join(str(x) for x in errors[0].get("loc", []))
            msg = errors[0].get("msg", "validation error")
            raise ValueError(f"Invalid plan structure: {field} - {msg}") from e
        raise ValueError(f"Invalid plan structure: {e}") from e

    plan.validate_dag()
    return plan
```

Add the import at top of file:

```python
from pydantic import ValidationError
```

**Step 4: Run full test suite** (30 sec)

```bash
make test
```

**Step 5: Commit** (30 sec)

```bash
git add src/harness/plan.py tests/harness/test_plan.py
git commit -m "feat(plan): improve error messages for invalid plan structure"
```

---

### Task 4: Add test for nested JSON in content

**Effort:** simple (4 tool calls)

**Files:**
- Modify: `tests/harness/test_plan.py`

**Step 1: Write test for content with multiple JSON objects** (2-5 min)

```python
def test_parse_plan_content_multiple_json_objects():
    """parse_plan_content should extract first valid plan from content with multiple JSON blocks."""
    content = '''
Here's some config: {"debug": true}

And here's the actual plan:
```json
{
  "goal": "The real plan",
  "tasks": {"t1": {"description": "Real task"}}
}
```
'''
    plan = parse_plan_content(content)
    assert plan.goal == "The real plan"
```

**Step 2: Run test** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_multiple_json_objects -v
```

Expected: PASS (fenced block takes priority)

**Step 3: Write test for raw JSON priority** (2-5 min)

```python
def test_parse_plan_content_raw_json_finds_plan():
    """parse_plan_content should find plan even without fences, ignoring non-plan JSON."""
    content = '''
Config: {"debug": true}

{
  "goal": "Found without fences",
  "tasks": {"t1": {"description": "Task"}}
}
'''
    plan = parse_plan_content(content)
    assert plan.goal == "Found without fences"
```

**Step 4: Run test and commit** (30 sec)

```bash
pytest tests/harness/test_plan.py::test_parse_plan_content_raw_json_finds_plan -v
git add tests/harness/test_plan.py
git commit -m "test(plan): add edge case tests for multiple JSON objects"
```

---

### Task 5: Run linting and type checks

**Effort:** simple (3 tool calls)

**Files:**
- Modify: `src/harness/plan.py` (if needed)

**Step 1: Run lint** (30 sec)

```bash
make lint
```

**Step 2: Run typecheck** (30 sec)

```bash
make typecheck
```

**Step 3: Fix any issues and commit** (2-5 min)

If any issues found, fix them and commit:

```bash
git add src/harness/plan.py
git commit -m "style(plan): fix linting and type issues"
```

---

### Task 6: Code Review

**Effort:** simple (3 tool calls)

**Files:**
- Review: `src/harness/plan.py`
- Review: `tests/harness/test_plan.py`

**Step 1: Review implementation against requirements** (2-5 min)

Verify:
- [ ] Raw JSON without fences is accepted
- [ ] Trailing commas are cleaned
- [ ] Fenced JSON still works
- [ ] Error messages are helpful
- [ ] All existing tests still pass

**Step 2: Run full check suite** (30 sec)

```bash
make check
```

**Step 3: Final commit if needed** (30 sec)

If any final adjustments:

```bash
git add .
git commit -m "refactor(plan): final cleanup for forgiving parser"
```

---

## Parallel Execution Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Write failing tests first (TDD) |
| Group 2 | 2 | Implement solution (depends on tests) |
| Group 3 | 3, 4 | Both are independent test improvements |
| Group 4 | 5 | Linting depends on implementation |
| Group 5 | 6 | Final review after all changes |
