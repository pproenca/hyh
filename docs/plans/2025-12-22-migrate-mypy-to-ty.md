# Migrate from mypy to ty Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-22-migrate-mypy-to-ty.md` to implement task-by-task.

**Goal:** Replace mypy with ty (Astral's new type checker) for faster type checking while maintaining the same level of type safety.

**Architecture:** ty is a standalone tool from Astral (creators of uv/ruff) that's 10-100x faster than mypy. It uses `[tool.ty]` configuration in `pyproject.toml` and is invoked via `ty check` instead of `mypy`. The main code change needed is adding `from __future__ import annotations` to handle forward references.

**Tech Stack:** ty (Astral's type checker), uv, Python 3.13+

---

### Task 1: Fix forward reference errors in daemon.py

**Files:**
- Modify: `src/harness/daemon.py:1`

**Step 1: Add future annotations import** (2 min)

Add `from __future__ import annotations` at the very top of the file (line 1, before all other imports). This enables PEP 563 deferred evaluation of annotations, which resolves forward reference issues where `HarnessHandler` references `HarnessDaemon` before it's defined.

```python
from __future__ import annotations

import contextlib
import fcntl
# ... rest of existing imports
```

**Why:** ty (unlike mypy) strictly enforces that all type annotations must be resolvable at the point of definition. The `HarnessHandler` class (line 29) has `server: HarnessDaemon` but `HarnessDaemon` is defined at line 364. The `__future__` import makes all annotations strings that are evaluated lazily.

**Step 2: Verify the fix with ty** (30 sec)

```bash
uvx ty check src/harness/daemon.py
```

Expected: No errors (0 diagnostics)

**Step 3: Verify mypy still passes** (30 sec)

```bash
uv run mypy src/
```

Expected: `Success: no issues found in 11 source files`

**Step 4: Commit** (30 sec)

```bash
git add src/harness/daemon.py
git commit -m "fix(daemon): add future annotations for forward reference compatibility"
```

---

### Task 2: Replace mypy with ty in dependencies

**Files:**
- Modify: `pyproject.toml:21`

**Step 1: Update dev dependencies** (2 min)

In the `[dependency-groups]` section, replace `"mypy>=1.0",` with `"ty",`:

```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-timeout>=2.0",
    "ruff>=0.8",
    "pre-commit>=4.0",
    "pyupgrade>=3.19",
    "pytest-cov>=7.0.0",
    "ty",
    "big-o>=0.11.0",
    "hypothesis>=6.100.0",
    "time-machine>=2.10.0",
    "pytest-benchmark>=4.0",
    "pytest-memray>=1.7",
]
```

**Step 2: Sync dependencies** (30 sec)

```bash
uv sync
```

Expected: ty installed, mypy removed from lockfile

**Step 3: Verify ty is available** (30 sec)

```bash
uv run ty --version
```

Expected: Version output like `ty 0.x.x`

**Step 4: Commit** (30 sec)

```bash
git add pyproject.toml uv.lock
git commit -m "build: replace mypy with ty in dev dependencies"
```

---

### Task 3: Add ty configuration to pyproject.toml

**Files:**
- Modify: `pyproject.toml` (append after `[tool.ruff.lint.per-file-ignores]` section)

**Step 1: Add ty configuration** (3 min)

Add the following configuration after the ruff sections (around line 95):

```toml
[tool.ty]
# Target Python version
[tool.ty.environment]
python-version = "3.13"

[tool.ty.src]
# Include source and tests
include = ["src"]

[tool.ty.rules]
# Match mypy's default strictness
# All rules enabled by default, customize only if needed
```

**Why:** This configuration:
- Sets Python 3.13 as target (matches `requires-python = ">=3.13"`)
- Limits checking to `src/` (same as current mypy invocation)
- Uses default rule strictness (similar to mypy defaults)

**Step 2: Verify ty works with config** (30 sec)

```bash
uv run ty check
```

Expected: No errors (ty uses pyproject.toml config automatically)

**Step 3: Commit** (30 sec)

```bash
git add pyproject.toml
git commit -m "build: add ty type checker configuration"
```

---

### Task 4: Update Makefile typecheck target

**Files:**
- Modify: `Makefile:16,103-104`

**Step 1: Replace MYPY variable with TY** (2 min)

Change line 16 from:
```makefile
MYPY := $(UV) run mypy
```
to:
```makefile
TY := $(UV) run ty
```

**Step 2: Update typecheck target** (2 min)

Change lines 102-104 from:
```makefile
.PHONY: typecheck
typecheck:  ## Run type checking with mypy
	$(MYPY) $(SRC_DIR)
```
to:
```makefile
.PHONY: typecheck
typecheck:  ## Run type checking with ty
	$(TY) check $(SRC_DIR)
```

**Step 3: Update clean target** (1 min)

Change line 123 from:
```makefile
	$(RM) -r .pytest_cache .ruff_cache .mypy_cache .benchmarks
```
to:
```makefile
	$(RM) -r .pytest_cache .ruff_cache .ty_cache .benchmarks
```

**Step 4: Verify Makefile target works** (30 sec)

```bash
make typecheck
```

Expected: ty runs and passes with no errors

**Step 5: Commit** (30 sec)

```bash
git add Makefile
git commit -m "build: update Makefile to use ty instead of mypy"
```

---

### Task 5: Update CI workflow

**Files:**
- Modify: `.github/workflows/ci.yml:52-53`

**Step 1: Update typecheck step** (2 min)

Change lines 52-53 from:
```yaml
      - name: Type check
        run: uv run mypy src/
```
to:
```yaml
      - name: Type check
        run: uv run ty check src/
```

**Step 2: Commit** (30 sec)

```bash
git add .github/workflows/ci.yml
git commit -m "ci: update type checking to use ty instead of mypy"
```

---

### Task 6: Update documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.serena/memories/suggested_commands.md`
- Modify: `.serena/memories/project_overview.md`
- Modify: `.serena/memories/task_completion.md`

**Step 1: Update CLAUDE.md** (2 min)

Find and replace `mypy` with `ty` in the Commands section. The `make typecheck` command stays the same but the comment should reflect ty.

**Step 2: Update Serena memories** (3 min)

Search for `mypy` references in `.serena/memories/` files and update to `ty`.

**Step 3: Commit** (30 sec)

```bash
git add CLAUDE.md .serena/memories/
git commit -m "docs: update documentation to reflect ty migration"
```

---

### Task 7: Run full verification

**Files:** None (verification only)

**Step 1: Run all checks** (2 min)

```bash
make check
```

Expected: All checks pass (lint + typecheck + test)

**Step 2: Verify clean build** (30 sec)

```bash
make clean && make install && make check
```

Expected: Fresh install and all checks pass

**Step 3: Final commit if any cleanup needed** (30 sec)

If any issues found, fix and commit with appropriate message.

---

### Task 8: Code Review

**Files:** All modified files

Review changes for:
- Correct ty configuration
- No regressions in type safety
- CI workflow correctness
- Documentation accuracy

---

## Parallel Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1 | Fix code before changing tooling |
| Group 2 | 2, 3, 4, 5 | Independent config files, no overlap |
| Group 3 | 6 | Documentation depends on earlier tasks |
| Group 4 | 7 | Verification requires all changes |
| Group 5 | 8 | Code review |
