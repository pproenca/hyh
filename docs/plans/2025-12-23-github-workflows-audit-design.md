# GitHub Workflows & Branch Protection Audit

**Date:** 2025-12-23
**Status:** Approved
**Reference:** astral-sh/ty workflow patterns

## Overview

Audit and harden GitHub workflow actions and branch protections to align with open source best practices, using astral-sh/ty as the reference implementation.

## Goals

1. **Security hardening** - Minimal permissions, credential isolation
2. **Supply chain security** - Sigstore attestations via PyPI
3. **Branch protection** - Required status checks, merge restrictions
4. **Maintainability** - Correct action version comments, manual dispatch

## Changes

### ci.yml

**Add top-level permissions deny-all:**
```yaml
permissions: {}
```

**Add job-level permissions:**
```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      # ...
```

Apply `permissions: contents: read` to: `lint`, `typecheck`, `test`, `build`

**Add persist-credentials: false to all checkouts:**
```yaml
- name: Checkout
  uses: actions/checkout@8e8c483db84b4bee98b60c0593521ed34d9990e8
  with:
    persist-credentials: false
```

**Add workflow_dispatch trigger:**
```yaml
on:
  push:
    branches: [master, main]
  pull_request:
    branches: [master, main]
  workflow_dispatch:
```

**Change coverage upload condition (master-push only):**
```yaml
- name: Upload coverage
  if: github.event_name == 'push' && github.ref == 'refs/heads/master' && matrix.os == 'ubuntu-latest' && matrix.python-version == '3.14'
  uses: codecov/codecov-action@671740ac38dd9b0130fbe1cec585b89eea48d3de
  with:
    token: ${{ secrets.CODECOV_TOKEN }}
    files: coverage.xml
    fail_ci_if_error: false
```

**Audit action version comments** - verify SHA hashes match stated versions.

### publish.yml

**Add top-level permissions deny-all:**
```yaml
permissions: {}
```

**Add job-level permissions:**
- `build`: `contents: read`
- `test`: `contents: read`
- `publish`: `contents: read`, `id-token: write` (already has id-token)

**Add persist-credentials: false to all checkouts.**

**Enable Sigstore attestations:**
```yaml
- name: Publish to PyPI
  run: uv publish --attestations
```

### zizmor.yml

**Add contents: read to job permissions:**
```yaml
jobs:
  zizmor:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write
```

Already has `persist-credentials: false` - no change needed.

### Branch Protection Rules (GitHub UI)

Configure for `master` branch:

| Setting | Value |
|---------|-------|
| Require PR before merging | Yes |
| Required approvals | 0 (solo maintainer) |
| Dismiss stale approvals | Yes |
| Require status checks | Yes |
| Required checks | `lint`, `typecheck`, `test`, `build` |
| Require branches up-to-date | Yes |
| Require conversation resolution | Yes |
| Restrict force pushes | Yes |
| Restrict deletions | Yes |

### PyPI Project Settings (Manual)

1. Navigate to https://pypi.org/manage/project/hyh/settings/publishing/
2. Enable "Generate attestations" for Sigstore support

## Verification

After implementation:

1. **Workflows:** Push a PR and verify all jobs run with correct permissions
2. **Branch protection:** Attempt direct push to master (should fail)
3. **Attestations:** After next release, verify with:
   ```bash
   gh attestation verify hyh-*.whl --owner pproenca
   ```

## Action Version Reference

| Action | SHA | Stated Version |
|--------|-----|----------------|
| actions/checkout | 8e8c483db84b4bee98b60c0593521ed34d9990e8 | Verify against releases |
| actions/upload-artifact | b7c566a772e6b6bfb58ed0dc250532a479d7789f | Verify against releases |
| actions/download-artifact | 37930b1c2abaa49bbe596cd826c3c89aef350131 | Verify against releases |
| astral-sh/setup-uv | 681c641aba71e4a1c380be3ab5e12ad51f415867 | Verify against releases |
| codecov/codecov-action | 671740ac38dd9b0130fbe1cec585b89eea48d3de | v5.5.2 |
| zizmorcore/zizmor-action | e639db99335bc9038abc0e066dfcd72e23d26fb4 | v0.3.0 |

**Note:** Audit SHA hashes against actual release tags during implementation.
