# Versioning System Design for Harness CLI

## Overview

Implement a versioning system following Astral (uv/ruff) conventions and official Python packaging best practices for PyPI distribution.

## Starting Version

```
0.1.0a1
```

PEP 440 pre-release progression: `0.1.0a1` → `0.1.0a2` → `0.1.0b1` → `0.1.0rc1` → `0.1.0`

## Components

### 1. Single Source of Truth: `pyproject.toml`

Version lives **only** in `pyproject.toml`:

```toml
[project]
name = "hyh"
version = "0.1.0a1"
```

### 2. Dynamic Version Access: `src/hyh/__init__.py`

Use `importlib.metadata` to read version at runtime:

```python
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("hyh")
except PackageNotFoundError:
    # Running from source without install
    __version__ = "0.0.0+dev"
```

**Benefits:**
- Version always in sync with `pyproject.toml`
- Works with editable installs (`uv pip install -e .`)
- No manual synchronization needed

### 3. Release Script: `scripts/release.sh`

Automated release workflow script.

**Supported bump types:**

| Type | Example |
|------|---------|
| `alpha` | 0.1.0a1 → 0.1.0a2 |
| `beta` | 0.1.0a2 → 0.1.0b1 |
| `rc` | 0.1.0b1 → 0.1.0rc1 |
| `stable` | 0.1.0rc1 → 0.1.0 |
| `patch` | 0.1.0 → 0.1.1 |
| `minor` | 0.1.0 → 0.2.0 |
| `major` | 0.1.0 → 1.0.0 |

**Usage:**

```bash
./scripts/release.sh alpha    # Bump alpha version
./scripts/release.sh minor    # Bump minor version
./scripts/release.sh patch    # Bump patch version
```

**Workflow:**

1. **Validate**: Git is clean, valid bump type provided
2. **Preview**: `uv version --bump {type} --dry-run`
3. **Confirm**: User confirmation before proceeding
4. **Bump**: `uv version --bump {type}`
5. **Changelog**: Auto-generate from conventional commits since last tag
6. **Commit**: `git add -A && git commit -m "chore: release v{version}"`
7. **Tag**: `git tag -a v{version} -m "Release v{version}"`
8. **Build**: `uv build`
9. **Push**: `git push && git push --tags`
10. **Publish**: `uv publish` (optional, with confirmation)

### 4. Changelog: `CHANGELOG.md`

Following [Keep a Changelog](https://keepachangelog.com/) format with auto-generation from conventional commits.

**Format:**

```markdown
# Changelog

## [0.1.0a2] - 2025-12-22

### Added
- feat: new feature description

### Changed
- refactor: change description

### Fixed
- fix: bug fix description
```

**Commit type mapping:**

| Commit prefix | Changelog section |
|---------------|-------------------|
| `feat:` | Added |
| `fix:` | Fixed |
| `refactor:`, `perf:`, `chore:` | Changed |
| `docs:` | Documentation |
| `BREAKING CHANGE:` | Breaking Changes |

## Version Progression Example

```
0.1.0a1  →  Initial alpha release (current)
0.1.0a2  →  Alpha iteration
0.1.0b1  →  First beta
0.1.0rc1 →  Release candidate
0.1.0    →  Stable release
0.1.1    →  Patch fix
0.2.0    →  Minor feature release
1.0.0    →  Major stable release
```

## References

- [Python Packaging: Single Source Version](https://packaging.python.org/en/latest/discussions/single-source-version/)
- [PEP 440: Version Identification](https://peps.python.org/pep-0440/)
- [uv version command](https://docs.astral.sh/uv/guides/package/)
- [Keep a Changelog](https://keepachangelog.com/)
- [Dynamic Versioning in uv Projects](https://slhck.info/software/2025/10/01/dynamic-versioning-uv-projects.html)
