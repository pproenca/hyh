# PyPI Golden Standard Checklist

Comprehensive checklist for setting up a production-grade PyPI package following all official Python Packaging Authority best practices (2025).

---

## Table of Contents

1. [Build System Configuration](#1-build-system-configuration)
2. [Project Metadata](#2-project-metadata)
3. [Dependencies & Requirements](#3-dependencies--requirements)
4. [Entry Points & Scripts](#4-entry-points--scripts)
5. [Type Hints (PEP 561)](#5-type-hints-pep-561)
6. [Licensing (PEP 639)](#6-licensing-pep-639)
7. [README & Documentation](#7-readme--documentation)
8. [Project Structure](#8-project-structure)
9. [Version Control & Git](#9-version-control--git)
10. [CI/CD & GitHub Actions](#10-cicd--github-actions)
11. [Trusted Publishing & Security](#11-trusted-publishing--security)
12. [TestPyPI Integration](#12-testpypi-integration)
13. [Release Automation](#13-release-automation)
14. [Code Quality & Linting](#14-code-quality--linting)
15. [Testing Infrastructure](#15-testing-infrastructure)
16. [Community & Contribution](#16-community--contribution)
17. [Security Policies](#17-security-policies)
18. [Wheel & Distribution](#18-wheel--distribution)

---

## 1. Build System Configuration

### pyproject.toml [build-system]

| Item | Required | Description |
|------|----------|-------------|
| `[build-system]` table | ✅ | Declares build backend per PEP 517/518 |
| `requires` | ✅ | Build dependencies with version pins |
| `build-backend` | ✅ | Backend module path |

**Recommended backends (2025):**
- **Hatchling** (hatchling >= 1.27.0) - PEP 639 license support
- setuptools (>= 77.0.3)
- Flit (flit-core >= 3.12)
- PDM (pdm-backend >= 2.4.0)
- uv-build (>= 0.7.19)

```toml
[build-system]
requires = ["hatchling >= 1.27.0"]
build-backend = "hatchling.build"
```

**Your project status:** ✅ Configured (hatchling)

---

## 2. Project Metadata

### Core Required Fields

| Field | Required | Description | Your Status |
|-------|----------|-------------|-------------|
| `name` | ✅ | Package name (normalized) | ✅ `hyh` |
| `version` | ✅ | PEP 440 compliant | ✅ `0.1.0a1` |

### Recommended Descriptive Fields

| Field | Recommended | Description | Your Status |
|-------|-------------|-------------|-------------|
| `description` | ✅ | One-line summary | ✅ Present |
| `readme` | ✅ | Long description file | ✅ `README.md` |
| `requires-python` | ✅ | Python version requirement | ✅ `>=3.13` |
| `authors` | ✅ | Name + email | ⚠️ Missing email |
| `maintainers` | Optional | Separate from authors | ❌ Not set |
| `keywords` | ✅ | PyPI searchability | ✅ 8 keywords |
| `classifiers` | ✅ | Trove classifiers | ✅ Comprehensive |

### Project URLs (Well-Known Labels)

| URL Label | Recommended | Description | Your Status |
|-----------|-------------|-------------|-------------|
| `Homepage` | ✅ | Main project page | ✅ Present |
| `Documentation` | ✅ | Docs site | ✅ Present |
| `Repository` | ✅ | Source code | ✅ Present |
| `Issues` | ✅ | Bug tracker | ✅ Present |
| `Changelog` | ✅ | Release notes | ✅ Present |
| `Funding` | Optional | Sponsor link | ❌ Not set |
| `Release-Notes` | Optional | Per-release notes | ❌ Not set |

### Dynamic Fields

| Item | Description |
|------|-------------|
| `dynamic = ["version"]` | Mark if version sourced from code/git |

**Your project status:** ⚠️ Static version (0.1.0a1) - acceptable but consider dynamic

---

## 3. Dependencies & Requirements

### Core Dependencies

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `dependencies` | ✅ | Runtime requirements | ✅ `msgspec>=0.18` |
| Version constraints | ✅ | Lower bounds, avoid upper | ✅ Correct |

### Optional Dependencies (Extras)

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| `[project.optional-dependencies]` | Optional | Feature extras | ❌ Not using |
| PEP 735 dependency groups | Modern | Dev/test deps | ✅ Using `[dependency-groups]` |

### Best Practices

- [ ] Use lower bounds: `>=1.0` not `==1.0`
- [ ] Avoid upper bounds: `>=1.0` not `>=1.0,<2.0` (unless API breaks known)
- [ ] Use extras for optional features: `package[cli]`, `package[dev]`
- [ ] Lock files for reproducibility: `uv.lock`, `requirements.lock`

**Your project status:** ✅ Well configured with PEP 735 groups

---

## 4. Entry Points & Scripts

### Console Scripts

| Item | Required for CLI | Description | Your Status |
|------|------------------|-------------|-------------|
| `[project.scripts]` | ✅ | CLI commands | ✅ `hyh = "hyh.client:main"` |

### GUI Scripts

| Item | Required for GUI | Description | Your Status |
|------|------------------|-------------|-------------|
| `[project.gui-scripts]` | If GUI app | Windows non-console | ❌ N/A |

### Plugin Entry Points

| Item | For extensibility | Description | Your Status |
|------|-------------------|-------------|-------------|
| `[project.entry-points."group.name"]` | Optional | Plugin discovery | ❌ Not using |

### pipx Optimization

| Item | Recommended for CLI | Description | Your Status |
|------|---------------------|-------------|-------------|
| `[project.entry-points."pipx.run"]` | Recommended | pipx run optimization | ❌ Not set |

**Example for pipx:**
```toml
[project.entry-points."pipx.run"]
hyh = "hyh.client:main"
```

**Your project status:** ✅ Console script configured, consider pipx entry point

---

## 5. Type Hints (PEP 561)

### py.typed Marker

| Item | Required for typed package | Description | Your Status |
|------|---------------------------|-------------|-------------|
| `py.typed` marker file | ✅ | Signals type info available | ❌ **MISSING** |
| Include in package_data | ✅ | Ensure in wheel | ❌ |
| `Typing :: Typed` classifier | ✅ | PyPI indicator | ✅ Present |

### Type Completeness

| Item | Best Practice | Description | Your Status |
|------|---------------|-------------|-------------|
| All public APIs typed | ✅ | Functions, classes, variables | ✅ (ty checker) |
| Generic base class args | ✅ | `class Foo(Generic[T])` | ✅ |
| Return types on all functions | ✅ | Explicit annotations | ✅ |

### Implementation

```bash
# Create marker file
touch src/hyh/py.typed
```

```toml
# pyproject.toml (hatchling)
[tool.hatch.build.targets.wheel]
packages = ["src/hyh"]

# Ensure py.typed is included automatically with hatchling
```

**Your project status:** ⚠️ Has classifier but **missing py.typed file**

---

## 6. Licensing (PEP 639)

### Modern License Format (SPDX)

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `license` | ✅ | SPDX expression | ⚠️ Using legacy `{ text = "MIT" }` |
| `license-files` | ✅ | Glob patterns | ❌ Not using new format |

### Modern vs Legacy Format

**Legacy (deprecated):**
```toml
license = { text = "MIT" }
```

**Modern (PEP 639):**
```toml
license = "MIT"
license-files = ["LICENSE"]
```

### Classifiers

| Item | Status | Description | Your Status |
|------|--------|-------------|-------------|
| License classifier | Deprecated | Use `license` field | ✅ Has classifier |

**Note:** Tools may warn when combining `license` field with license classifiers.

**Your project status:** ⚠️ Using legacy format, upgrade to PEP 639

---

## 7. README & Documentation

### README Requirements

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `README.md` or `README.rst` | ✅ | Long description | ✅ Present |
| GitHub Flavored Markdown | ✅ | Wide compatibility | ✅ Using |
| No Sphinx directives | ✅ | PyPI can't render | ✅ Clean |

### Recommended README Sections

| Section | Recommended | Description | Your Status |
|---------|-------------|-------------|-------------|
| Badges | ✅ | Version, Python, License | ✅ Present |
| Description | ✅ | What it does | ✅ Present |
| Installation | ✅ | How to install | ✅ 5 methods |
| Quick Start | ✅ | Usage examples | ✅ Present |
| Architecture | Optional | For complex projects | ✅ ASCII diagram |
| Requirements | ✅ | System requirements | ✅ Present |
| Development | ✅ | Contribution setup | ✅ Present |
| License | ✅ | License reference | ✅ Present |

### Validation

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `twine check dist/*` | ✅ | Validates rendering | ✅ In CI |

**Your project status:** ✅ Excellent README

---

## 8. Project Structure

### src-Layout (Recommended)

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `src/` directory | ✅ | Package isolation | ✅ Using |
| `src/package/__init__.py` | ✅ | Package marker | ✅ Present |
| `tests/` outside src | ✅ | Separate tests | ✅ Present |

### Directory Structure

```
project/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CLAUDE.md (optional)
├── .gitignore
├── .python-version
├── src/
│   └── package/
│       ├── __init__.py
│       ├── py.typed          # ← MISSING
│       └── *.py
├── tests/
│   └── *.py
├── docs/ (optional)
├── scripts/ (optional)
└── .github/
    └── workflows/
```

**Your project status:** ✅ Correct structure, missing py.typed

---

## 9. Version Control & Git

### Essential Files

| File | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `.gitignore` | ✅ | Ignore patterns | ✅ Present |
| `.python-version` | ✅ | Pin Python version | ✅ Present |

### Recommended Files

| File | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| `.pre-commit-config.yaml` | ✅ | Pre-commit hooks | ✅ Present |
| `CHANGELOG.md` | ✅ | Release notes | ✅ Present |

### Changelog Format

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| Keep a Changelog format | ✅ | Standard structure | ✅ Using |
| Semantic Versioning | ✅ | Version scheme | ✅ Using |

**Your project status:** ✅ Complete

---

## 10. CI/CD & GitHub Actions

### CI Workflow

| Job | Required | Description | Your Status |
|-----|----------|-------------|-------------|
| Lint | ✅ | Code style check | ✅ ruff |
| Type check | ✅ | Static analysis | ✅ ty |
| Test | ✅ | Run test suite | ✅ pytest |
| Build | ✅ | Verify packaging | ✅ uv build |

### CI Best Practices

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| Matrix testing (OS) | ✅ | ubuntu + macos | ✅ Present |
| Matrix testing (Python) | ✅ | Multiple versions | ⚠️ Only 3.13 |
| Locked dependencies | ✅ | `--frozen` flag | ✅ Using |
| Concurrency controls | ✅ | Cancel in-progress | ✅ Present |
| Caching | Optional | Speed up builds | ❌ Not using |

### Publish Workflow

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| Tag trigger | ✅ | `v*` tags | ✅ Present |
| Trusted Publishing | ✅ | OIDC tokens | ✅ Configured |
| Smoke tests | ✅ | Test before publish | ✅ Present |
| Environment protection | ✅ | Manual approval | ✅ pypi env |

**Your project status:** ✅ Excellent CI/CD

---

## 11. Trusted Publishing & Security

### Trusted Publishing (OIDC)

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| PyPI Trusted Publisher | ✅ | Register on pypi.org | ✅ Configured |
| TestPyPI Trusted Publisher | ✅ | Register on test.pypi.org | ✅ Configured |
| `id-token: write` permission | ✅ | In workflow | ✅ Present |
| No stored API tokens | ✅ | Use OIDC only | ✅ |

### Attestations (PEP 740)

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| SLSA Provenance | ✅ | Source verification | ✅ Auto with TP |
| PyPI Publish attestation | ✅ | Publish proof | ✅ Auto with TP |
| Sigstore signing | ✅ | Keyless signing | ✅ Auto with TP |

### Verification

| Item | Best Practice | Description |
|------|---------------|-------------|
| Rekor inclusion proof | ✅ | Transparency log |
| Fulcio CT log | ✅ | Certificate transparency |

**Your project status:** ✅ Trusted Publishing configured (attestations automatic)

---

## 12. TestPyPI Integration

### Configuration

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| TestPyPI account | ✅ | Separate from PyPI | ✅ Configured |
| TestPyPI index in pyproject | ✅ | For testing | ✅ Present |
| `explicit = true` | ✅ | Don't mix indexes | ✅ Set |

### Workflow

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| Publish to TestPyPI first | ✅ | Validate before prod | ✅ In workflow |
| Test install from TestPyPI | ✅ | Verify installable | ⚠️ Manual |
| Smoke test | ✅ | Import package | ✅ In workflow |

**Your project status:** ✅ TestPyPI configured

---

## 13. Release Automation

### Semantic Versioning

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| SemVer compliance | ✅ | MAJOR.MINOR.PATCH | ✅ Using |
| Pre-release support | ✅ | alpha, beta, rc | ✅ scripts/release.sh |
| Version bump script | ✅ | Automated bumping | ✅ Present |

### Release Script Features

| Feature | Recommended | Description | Your Status |
|---------|-------------|-------------|-------------|
| Git safety checks | ✅ | Clean working directory | ✅ Present |
| Version preview | ✅ | Show before confirm | ✅ Present |
| Automatic tagging | ✅ | Create git tags | ✅ Present |
| Branch validation | ✅ | Only from main/master | ✅ Present |

### Makefile Targets

| Target | Recommended | Description | Your Status |
|--------|-------------|-------------|-------------|
| `make build` | ✅ | Build distributions | ✅ Present |
| `make publish-test` | ✅ | Publish to TestPyPI | ✅ Present |
| `make publish` | ✅ | Publish to PyPI | ✅ Present |
| `make release` | ✅ | Full release workflow | ✅ Present |

**Your project status:** ✅ Complete release automation

---

## 14. Code Quality & Linting

### Linting Tools

| Tool | Required | Description | Your Status |
|------|----------|-------------|-------------|
| ruff | ✅ | Fast linter + formatter | ✅ Configured |
| mypy/pyright/ty | ✅ | Type checker | ✅ ty |

### Ruff Configuration

| Rule Category | Recommended | Description | Your Status |
|---------------|-------------|-------------|-------------|
| E/W (pycodestyle) | ✅ | Style errors | ✅ Enabled |
| F (Pyflakes) | ✅ | Logic errors | ✅ Enabled |
| I (isort) | ✅ | Import sorting | ✅ Enabled |
| UP (pyupgrade) | ✅ | Modern syntax | ✅ Enabled |
| B (bugbear) | ✅ | Bug patterns | ✅ Enabled |
| C4 (comprehensions) | ✅ | Comprehension style | ✅ Enabled |
| SIM (simplify) | ✅ | Code simplification | ✅ Enabled |
| RUF (ruff-specific) | ✅ | Ruff rules | ✅ Enabled |

### Pre-commit Hooks

| Hook | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| pyupgrade | ✅ | Modernize syntax | ✅ Present |
| ruff (lint) | ✅ | Lint on commit | ❌ Not in config |
| ruff (format) | ✅ | Format on commit | ❌ Not in config |

**Your project status:** ✅ Good, could add ruff to pre-commit

---

## 15. Testing Infrastructure

### Testing Framework

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| pytest | ✅ | Test framework | ✅ Configured |
| pytest configuration | ✅ | In pyproject.toml | ✅ Present |
| Test markers | ✅ | Categorize tests | ✅ slow, benchmark, memcheck |

### Coverage

| Item | Recommended | Description | Your Status |
|------|-------------|-------------|-------------|
| pytest-cov | ✅ | Coverage reporting | ❌ Not configured |
| Coverage badge | ✅ | README indicator | ❌ Not present |
| Minimum coverage threshold | ✅ | Quality gate | ❌ Not set |

### Test Categories

| Marker | Recommended | Description | Your Status |
|--------|-------------|-------------|-------------|
| `@pytest.mark.slow` | ✅ | Long-running tests | ✅ Present |
| `@pytest.mark.integration` | ✅ | Integration tests | ❌ Not defined |
| `@pytest.mark.unit` | Optional | Unit tests | ❌ Not defined |

**Your project status:** ⚠️ Good testing, missing coverage reporting

---

## 16. Community & Contribution

### Community Files

| File | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `CONTRIBUTING.md` | ✅ | Contribution guide | ❌ **MISSING** |
| `CODE_OF_CONDUCT.md` | ✅ | Community standards | ❌ **MISSING** |
| Issue templates | ✅ | Structured issues | ❌ **MISSING** |
| PR template | ✅ | Structured PRs | ❌ **MISSING** |

### CONTRIBUTING.md Sections

| Section | Recommended | Description |
|---------|-------------|-------------|
| Development setup | ✅ | How to set up locally |
| Running tests | ✅ | Test commands |
| Code style | ✅ | Formatting rules |
| Commit messages | ✅ | Conventional commits |
| Pull request process | ✅ | Review workflow |

### Issue Templates

| Template | Recommended | Description |
|----------|-------------|-------------|
| Bug report | ✅ | Structured bug reports |
| Feature request | ✅ | Feature proposals |

**Your project status:** ⚠️ Missing community files

---

## 17. Security Policies

### Security Files

| File | Required | Description | Your Status |
|------|----------|-------------|-------------|
| `SECURITY.md` | ✅ | Vulnerability reporting | ❌ **MISSING** |

### SECURITY.md Contents

| Section | Required | Description |
|---------|----------|-------------|
| Supported versions | ✅ | Which versions get patches |
| Reporting process | ✅ | How to report privately |
| Response timeline | ✅ | Expected response time |
| Disclosure policy | ✅ | Coordinated disclosure |

### GitHub Security Features

| Feature | Recommended | Description | Your Status |
|---------|-------------|-------------|-------------|
| Dependabot alerts | ✅ | Dependency vulnerabilities | ❌ Not configured |
| Dependabot updates | ✅ | Auto-update PRs | ❌ Not configured |
| Code scanning | Optional | CodeQL analysis | ❌ Not configured |
| Secret scanning | ✅ | Exposed secrets | ✅ (GitHub default) |

**Your project status:** ⚠️ Missing security policy and Dependabot

---

## 18. Wheel & Distribution

### Build Verification

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| Both wheel + sdist | ✅ | Binary + source | ✅ Both built |
| `uv build --no-sources` | ✅ | Verify from source | ✅ In CI |
| Wheel contents check | ✅ | Verify included files | ✅ In CI |

### Wheel Contents

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| All Python modules | ✅ | Source files | ✅ Present |
| `py.typed` marker | ✅ | Type info signal | ❌ **MISSING** |
| LICENSE | ✅ | In .dist-info | ✅ Present |
| METADATA | ✅ | Package metadata | ✅ Present |
| entry_points.txt | ✅ | Console scripts | ✅ Present |
| RECORD | ✅ | File manifest | ✅ Present |

### Source Distribution

| Item | Required | Description | Your Status |
|------|----------|-------------|-------------|
| Source code | ✅ | All .py files | ✅ Present |
| Tests | ✅ | Test files | ✅ Present |
| Documentation | ✅ | README, CHANGELOG | ✅ Present |
| Config files | ✅ | pyproject.toml | ✅ Present |

**Your project status:** ✅ Good, missing py.typed in wheel

---

## Summary: Action Items for hyh

### Critical (Must Fix)

| Priority | Item | File | Action |
|----------|------|------|--------|
| 🔴 P0 | py.typed marker | `src/hyh/py.typed` | Create empty file |
| 🔴 P0 | Upgrade license format | `pyproject.toml` | PEP 639 SPDX |

### High (Should Fix)

| Priority | Item | File | Action |
|----------|------|------|--------|
| 🟠 P1 | SECURITY.md | Root | Create security policy |
| 🟠 P1 | CONTRIBUTING.md | Root | Create contribution guide |
| 🟠 P1 | CODE_OF_CONDUCT.md | Root | Adopt Contributor Covenant |
| 🟠 P1 | Author email | `pyproject.toml` | Add email to authors |
| 🟠 P1 | Dependabot config | `.github/dependabot.yml` | Enable auto-updates |

### Medium (Nice to Have)

| Priority | Item | File | Action |
|----------|------|------|--------|
| 🟡 P2 | Issue templates | `.github/ISSUE_TEMPLATE/` | Bug + feature templates |
| 🟡 P2 | PR template | `.github/PULL_REQUEST_TEMPLATE.md` | Structured PRs |
| 🟡 P2 | Coverage reporting | `pyproject.toml` | Add pytest-cov |
| 🟡 P2 | pipx entry point | `pyproject.toml` | Optimize for pipx |
| 🟡 P2 | Pre-commit ruff hooks | `.pre-commit-config.yaml` | Add ruff hooks |

### Low (Optional Enhancements)

| Priority | Item | Description |
|----------|------|-------------|
| 🟢 P3 | Funding link | Add GitHub sponsors |
| 🟢 P3 | Dynamic version | Source from git tag |
| 🟢 P3 | Coverage badge | Add to README |
| 🟢 P3 | Matrix Python versions | Test 3.13 + 3.14 |
| 🟢 P3 | Documentation site | ReadTheDocs / GitHub Pages |

---

## Sources

- [Python Packaging User Guide - Building and Publishing](https://packaging.python.org/en/latest/guides/section-build-and-publish/)
- [Writing pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [GitHub Actions Publishing](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)
- [PyPI-Friendly README](https://packaging.python.org/en/latest/guides/making-a-pypi-friendly-readme/)
- [Using TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/)
- [Creating CLI Tools](https://packaging.python.org/en/latest/guides/creating-command-line-tools/)
- [Dropping Python Versions](https://packaging.python.org/en/latest/guides/dropping-older-python-versions/)
- [Namespace Packages](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/)
- [Plugin Discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/)
- [Core Metadata Specification](https://packaging.python.org/en/latest/specifications/core-metadata/)
- [pyproject.toml Specification](https://packaging.python.org/en/latest/specifications/pyproject-toml/)
- [PEP 561 - Typed Packages](https://typing.python.org/en/latest/guides/libraries.html)
- [PyPI Attestations](https://docs.pypi.org/attestations/)
- [PyPI Sigstore Attestations GA](https://blog.sigstore.dev/pypi-attestations-ga/)
- [Packaging Tutorial](https://packaging.python.org/en/latest/tutorials/packaging-projects/)
