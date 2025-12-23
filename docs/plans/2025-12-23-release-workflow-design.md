# Release Workflow Design

**Date:** 2025-12-23
**Status:** Approved
**Reference:** astral-sh/ty release workflow

## Overview

Replace tag-triggered publishing with manual workflow_dispatch releases, aligned with ty's approach. Creates GitHub Releases with changelog-based release notes.

## Release Process

| Step | Action |
|------|--------|
| 1 | Edit `pyproject.toml` - bump version |
| 2 | Edit `CHANGELOG.md` - move Unreleased to new version section |
| 3 | Commit and push to master |
| 4 | Go to Actions → Release → Run workflow |
| 5 | Enter version (e.g., `0.2.0`) and click "Run" |
| 6 | Workflow validates, builds, tests, publishes, creates release |

## Workflow Structure

**File:** `.github/workflows/release.yml`

```yaml
name: Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to release (e.g., 0.2.0)'
        required: true
        type: string

permissions: {}

jobs:
  validate:
    # Check version input matches pyproject.toml

  build:
    # Build sdist and wheel

  test:
    # Smoke test the built artifacts

  publish:
    # Publish to PyPI with attestations

  release:
    # Create GitHub Release with changelog
```

## Job Details

### validate

Extracts version from `pyproject.toml` and compares to input. Fails if mismatch.

```bash
PYPROJECT_VERSION=$(grep -E '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
if [[ "$PYPROJECT_VERSION" != "${{ inputs.version }}" ]]; then
  echo "Version mismatch: input=${{ inputs.version }}, pyproject.toml=$PYPROJECT_VERSION"
  exit 1
fi
```

### build

Builds sdist and wheel using `uv build --no-sources`.

### test

Smoke tests the built wheel and sdist.

### publish

Publishes to PyPI with Sigstore attestations:
- `id-token: write` for trusted publishing
- `attestations: write` for Sigstore
- `enable-cache: false` to prevent cache poisoning

### release

Creates GitHub Release with changelog section as body.

**Changelog extraction:**
```bash
sed -n '/^## \['"${VERSION}"'\]/,/^## \[/{ /^## \['"${VERSION}"'\]/d; /^## \[/d; p; }' CHANGELOG.md > release_notes.md
```

**Prerelease detection:**
```bash
if [[ "$VERSION" =~ (a|b|rc)[0-9]+ ]]; then
  PRERELEASE="--prerelease"
else
  PRERELEASE=""
fi
```

**Release creation:**
```bash
gh release create "v${VERSION}" \
  --title "v${VERSION}" \
  --notes-file release_notes.md \
  $PRERELEASE
```

## Security

- `permissions: {}` at top level (deny-all default)
- `persist-credentials: false` on all checkouts
- `id-token: write` + `attestations: write` for PyPI publish job
- `contents: write` for release job (tag + release creation)
- `enable-cache: false` in all setup-uv steps

## Files Changed

**Create:**
- `.github/workflows/release.yml`

**Delete:**
- `.github/workflows/publish.yml`

## Prerelease Examples

| Version | Type |
|---------|------|
| `0.2.0` | Stable |
| `0.2.0a1` | Prerelease (alpha) |
| `0.2.0b2` | Prerelease (beta) |
| `0.2.0rc1` | Prerelease (release candidate) |
