# Versioning System Implementation Plan

> **Execution:** Use `/dev-workflow:execute-plan docs/plans/2025-12-22-versioning-implementation.md` to implement task-by-task.

**Goal:** Implement single-source versioning with dynamic runtime access and automated release workflow.

**Architecture:** Version defined only in `pyproject.toml`, accessed at runtime via `importlib.metadata`. Release automation via bash script using `uv version --bump`.

**Tech Stack:** Python 3.13+, uv, bash, importlib.metadata

---

## Task 1: Dynamic Version in `__init__.py`

**Files:**
- Modify: `src/hyh/__init__.py`
- Create: `tests/hyh/test_version.py`

**Step 1: Write failing test for version access** (2-5 min)

Create `tests/hyh/test_version.py`:

```python
"""Tests for version management."""

import re
import subprocess
import sys


def test_version_importable():
    """Verify __version__ is accessible from package."""
    from hyh import __version__

    assert __version__ is not None
    assert isinstance(__version__, str)


def test_version_format_pep440():
    """Verify version follows PEP 440 format."""
    from hyh import __version__

    # PEP 440: N[.N]+[{a|b|rc}N][.postN][.devN]
    pep440_pattern = r"^\d+\.\d+\.\d+(a|b|rc)?\d*(\.post\d+)?(\.dev\d+)?(\+.+)?$"
    assert re.match(pep440_pattern, __version__), f"Version '{__version__}' is not PEP 440 compliant"


def test_version_matches_metadata():
    """Verify __version__ matches installed package metadata."""
    from importlib.metadata import version

    from hyh import __version__

    installed_version = version("hyh")
    assert __version__ == installed_version


def test_version_cli_accessible():
    """Verify version can be accessed via CLI import."""
    result = subprocess.run(
        [sys.executable, "-c", "from hyh import __version__; print(__version__)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() != ""
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
uv run pytest tests/hyh/test_version.py -v
```

Expected: FAIL (current hardcoded version doesn't match metadata pattern expectations)

**Step 3: Update `__init__.py` with dynamic version** (2-5 min)

Replace contents of `src/hyh/__init__.py`:

```python
"""Harness - Autonomous Research Kernel with Thread-Safe Pull Engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hyh")
except PackageNotFoundError:
    # Running from source without install
    __version__ = "0.0.0+dev"
```

**Step 4: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/hyh/test_version.py -v
```

Expected: PASS (4 passed)

**Step 5: Commit** (30 sec)

```bash
git add src/hyh/__init__.py tests/hyh/test_version.py
git commit -m "feat(version): use importlib.metadata for dynamic versioning"
```

---

## Task 2: Update `pyproject.toml` to `0.1.0a1`

**Files:**
- Modify: `pyproject.toml:3`

**Step 1: Write failing test for alpha version format** (2-5 min)

Add to `tests/hyh/test_version.py`:

```python
def test_version_is_alpha():
    """Verify current version is alpha release."""
    from hyh import __version__

    assert "a" in __version__ or "+dev" in __version__, f"Expected alpha version, got '{__version__}'"
```

**Step 2: Run test to verify it fails** (30 sec)

```bash
uv run pytest tests/hyh/test_version.py::test_version_is_alpha -v
```

Expected: FAIL (current version is `2.0.0`, not alpha)

**Step 3: Update version in pyproject.toml** (2-5 min)

Edit `pyproject.toml` line 3:

```toml
version = "0.1.0a1"
```

**Step 4: Sync lock file** (30 sec)

```bash
uv sync
```

**Step 5: Run test to verify it passes** (30 sec)

```bash
uv run pytest tests/hyh/test_version.py::test_version_is_alpha -v
```

Expected: PASS

**Step 6: Commit** (30 sec)

```bash
git add pyproject.toml uv.lock tests/hyh/test_version.py
git commit -m "chore(version): set initial alpha version 0.1.0a1"
```

---

## Task 3: Create `CHANGELOG.md`

**Files:**
- Create: `CHANGELOG.md`

**Step 1: Create initial changelog** (2-5 min)

Create `CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0a1] - 2025-12-22

### Added

- Initial alpha release
- Task state management with dependency-aware execution (DAG validation)
- Thread-safe operations for concurrent task handling
- Client-daemon architecture via Unix sockets
- Command execution runtimes (local and Docker)
- Git integration for safe operations
- Dynamic versioning via `importlib.metadata`
- Automated release workflow script

[Unreleased]: https://github.com/pproenca/hyh/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/pproenca/hyh/releases/tag/v0.1.0a1
```

**Step 2: Verify changelog format** (30 sec)

```bash
head -30 CHANGELOG.md
```

Expected: Shows formatted changelog with proper sections

**Step 3: Commit** (30 sec)

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md following Keep a Changelog format"
```

---

## Task 4: Create `scripts/release.sh`

**Files:**
- Create: `scripts/release.sh`

**Step 1: Create release script with validation** (5 min)

Create `scripts/release.sh`:

```bash
#!/usr/bin/env bash
#
# Release automation script for hyh
# Usage: ./scripts/release.sh [major|minor|patch|alpha|beta|rc|stable]
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Valid bump types
VALID_TYPES="major minor patch alpha beta rc stable"

usage() {
    echo "Usage: $0 <bump-type>"
    echo ""
    echo "Bump types:"
    echo "  alpha   - Increment alpha version (0.1.0a1 -> 0.1.0a2)"
    echo "  beta    - Move to beta (0.1.0a2 -> 0.1.0b1)"
    echo "  rc      - Move to release candidate (0.1.0b1 -> 0.1.0rc1)"
    echo "  stable  - Move to stable (0.1.0rc1 -> 0.1.0)"
    echo "  patch   - Increment patch (0.1.0 -> 0.1.1)"
    echo "  minor   - Increment minor (0.1.0 -> 0.2.0)"
    echo "  major   - Increment major (0.1.0 -> 1.0.0)"
    exit 1
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Check arguments
if [[ $# -ne 1 ]]; then
    usage
fi

BUMP_TYPE="$1"

# Validate bump type
if [[ ! " $VALID_TYPES " =~ " $BUMP_TYPE " ]]; then
    log_error "Invalid bump type: $BUMP_TYPE"
    usage
fi

# Check git is clean
if [[ -n $(git status --porcelain) ]]; then
    log_error "Working directory is not clean. Commit or stash changes first."
fi

# Check we're on a valid branch
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "master" && "$CURRENT_BRANCH" != "main" ]]; then
    log_warning "Not on master/main branch (current: $CURRENT_BRANCH)"
    read -rp "Continue anyway? [y/N] " response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Get current version
CURRENT_VERSION=$(uv version --short 2>/dev/null || echo "unknown")
log_info "Current version: $CURRENT_VERSION"

# Preview version bump
log_info "Previewing version bump..."
NEW_VERSION=$(uv version --bump "$BUMP_TYPE" --dry-run 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+[a-z0-9]*' | tail -1)
echo ""
echo -e "  ${YELLOW}$CURRENT_VERSION${NC} -> ${GREEN}$NEW_VERSION${NC}"
echo ""

# Confirm
read -rp "Proceed with release? [y/N] " response
if [[ ! "$response" =~ ^[Yy]$ ]]; then
    log_info "Release cancelled."
    exit 0
fi

# Bump version
log_info "Bumping version..."
uv version --bump "$BUMP_TYPE"

# Get the actual new version
NEW_VERSION=$(uv version --short)
log_success "Version bumped to $NEW_VERSION"

# Generate changelog entry from commits
log_info "Generating changelog entry..."
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
DATE=$(date +%Y-%m-%d)

# Create temporary changelog entry
TEMP_CHANGELOG=$(mktemp)
{
    echo "## [$NEW_VERSION] - $DATE"
    echo ""

    # Group commits by type
    if [[ -n "$LAST_TAG" ]]; then
        COMMIT_RANGE="$LAST_TAG..HEAD"
    else
        COMMIT_RANGE="HEAD"
    fi

    # Added (feat:)
    ADDED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^feat" 2>/dev/null || true)
    if [[ -n "$ADDED" ]]; then
        echo "### Added"
        echo ""
        echo "$ADDED"
        echo ""
    fi

    # Fixed (fix:)
    FIXED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^fix" 2>/dev/null || true)
    if [[ -n "$FIXED" ]]; then
        echo "### Fixed"
        echo ""
        echo "$FIXED"
        echo ""
    fi

    # Changed (refactor:, perf:, chore:)
    CHANGED=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^refactor\|^perf\|^chore" 2>/dev/null || true)
    if [[ -n "$CHANGED" ]]; then
        echo "### Changed"
        echo ""
        echo "$CHANGED"
        echo ""
    fi

    # Documentation (docs:)
    DOCS=$(git log "$COMMIT_RANGE" --pretty=format:"- %s" --grep="^docs" 2>/dev/null || true)
    if [[ -n "$DOCS" ]]; then
        echo "### Documentation"
        echo ""
        echo "$DOCS"
        echo ""
    fi
} > "$TEMP_CHANGELOG"

# Update CHANGELOG.md
if [[ -f "CHANGELOG.md" ]]; then
    log_info "Updating CHANGELOG.md..."
    # Insert after "## [Unreleased]" line
    sed -i.bak "/^## \[Unreleased\]/r $TEMP_CHANGELOG" CHANGELOG.md
    rm -f CHANGELOG.md.bak

    # Update comparison links at bottom
    if grep -q "\[Unreleased\]:" CHANGELOG.md; then
        # Update unreleased link
        sed -i.bak "s|\[Unreleased\]:.*|\[Unreleased\]: https://github.com/pproenca/hyh/compare/v$NEW_VERSION...HEAD|" CHANGELOG.md
        # Add new version link if not exists
        if ! grep -q "\[$NEW_VERSION\]:" CHANGELOG.md; then
            if [[ -n "$LAST_TAG" ]]; then
                echo "[$NEW_VERSION]: https://github.com/pproenca/hyh/compare/$LAST_TAG...v$NEW_VERSION" >> CHANGELOG.md
            else
                echo "[$NEW_VERSION]: https://github.com/pproenca/hyh/releases/tag/v$NEW_VERSION" >> CHANGELOG.md
            fi
        fi
        rm -f CHANGELOG.md.bak
    fi
fi
rm -f "$TEMP_CHANGELOG"

# Commit
log_info "Committing changes..."
git add -A
git commit -m "chore: release v$NEW_VERSION"

# Tag
log_info "Creating tag v$NEW_VERSION..."
git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"

# Build
log_info "Building package..."
uv build --no-sources

log_success "Build complete! Artifacts in dist/"

# Push
read -rp "Push to remote? [y/N] " response
if [[ "$response" =~ ^[Yy]$ ]]; then
    log_info "Pushing to remote..."
    git push
    git push --tags
    log_success "Pushed to remote"
fi

# Publish
read -rp "Publish to PyPI? [y/N] " response
if [[ "$response" =~ ^[Yy]$ ]]; then
    log_info "Publishing to PyPI..."
    uv publish
    log_success "Published to PyPI!"
else
    log_info "Skipping publish. Run 'uv publish' manually when ready."
fi

echo ""
log_success "Release v$NEW_VERSION complete!"
echo ""
echo "Next steps:"
echo "  - Verify the release on PyPI: https://pypi.org/project/hyh/"
echo "  - Create GitHub release: https://github.com/pproenca/hyh/releases/new?tag=v$NEW_VERSION"
```

**Step 2: Make script executable** (30 sec)

```bash
chmod +x scripts/release.sh
```

**Step 3: Verify script syntax** (30 sec)

```bash
bash -n scripts/release.sh && echo "Syntax OK"
```

Expected: "Syntax OK"

**Step 4: Test help output** (30 sec)

```bash
./scripts/release.sh 2>&1 | head -15
```

Expected: Shows usage help with bump types

**Step 5: Commit** (30 sec)

```bash
git add scripts/release.sh
git commit -m "feat(release): add automated release script"
```

---

## Task 5: Update Makefile with release target

**Files:**
- Modify: `Makefile`

**Step 1: Add release target to Makefile** (2-5 min)

Add after the `publish` target (around line 131):

```makefile
# Release automation
.PHONY: release
release:
	@if [ -z "$(TYPE)" ]; then \
		echo "Usage: make release TYPE=[major|minor|patch|alpha|beta|rc|stable]"; \
		exit 1; \
	fi
	./scripts/release.sh $(TYPE)

.PHONY: release-alpha
release-alpha:
	./scripts/release.sh alpha

.PHONY: release-patch
release-patch:
	./scripts/release.sh patch

.PHONY: release-minor
release-minor:
	./scripts/release.sh minor
```

**Step 2: Verify Makefile syntax** (30 sec)

```bash
make -n release TYPE=patch 2>&1 | head -5
```

Expected: Shows the release.sh command that would run

**Step 3: Commit** (30 sec)

```bash
git add Makefile
git commit -m "build: add release automation targets to Makefile"
```

---

## Task 6: Code Review

**Files:**
- All modified files from tasks 1-5

**Step 1: Run full test suite** (2-5 min)

```bash
uv run pytest tests/hyh/test_version.py -v
uv run pytest --timeout=60
```

Expected: All tests pass

**Step 2: Run linting and type checks** (30 sec)

```bash
make lint
make typecheck
```

Expected: No errors

**Step 3: Verify version is accessible** (30 sec)

```bash
uv run python -c "from hyh import __version__; print(f'Version: {__version__}')"
```

Expected: `Version: 0.1.0a1`

**Step 4: Dry-run release script** (30 sec)

```bash
uv version --bump alpha --dry-run
```

Expected: Shows `0.1.0a1 => 0.1.0a2`

---

## Parallel Execution Groups

| Task Group | Tasks | Rationale |
|------------|-------|-----------|
| Group 1 | 1, 3 | Independent: `__init__.py` + tests vs `CHANGELOG.md` |
| Group 2 | 2 | Depends on Task 1 (tests reference version) |
| Group 3 | 4 | Independent: `scripts/release.sh` |
| Group 4 | 5 | Depends on Task 4 (references release.sh) |
| Group 5 | 6 | Final verification, depends on all |
