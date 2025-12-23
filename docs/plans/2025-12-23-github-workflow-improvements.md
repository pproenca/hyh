# GitHub Workflow Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Adopt applicable UV GitHub workflow patterns to improve hyh's CI/CD, security, and contribution experience.

**Architecture:** Enhance existing workflows with security scanning, better dependency management, and improved issue/PR templates based on UV's battle-tested patterns.

**Tech Stack:** GitHub Actions, uv, ruff, ty, zizmor, Renovate

---

## UV Patterns Analysis for hyh

### Applicable Patterns (HIGH VALUE)

| UV Pattern                    | Why Applicable                                                   | Priority |
| ----------------------------- | ---------------------------------------------------------------- | -------- |
| **zizmor security scanning**  | Scans GitHub Actions for vulnerabilities - zero cost, high value | HIGH     |
| **SHA-pinned actions**        | UV pins all actions to immutable SHAs for supply-chain security  | HIGH     |
| **Renovate over Dependabot**  | More flexible, groups updates, better scheduling                 | MEDIUM   |
| **Concurrency groups**        | hyh already has this - DONE                                      | DONE     |
| **Issue template config.yml** | Add docs/Discord links, allow blank issues                       | LOW      |
| **Question issue template**   | UV has 3 templates (bug, feature, question)                      | LOW      |

### Not Applicable Patterns

| UV Pattern                   | Why Not Applicable                             |
| ---------------------------- | ---------------------------------------------- |
| **build-binaries.yml**       | UV is Rust + Python hybrid; hyh is pure Python |
| **build-docker.yml**         | hyh doesn't need Docker images                 |
| **release.yml (cargo-dist)** | Rust-specific release orchestration            |
| **publish-crates.yml**       | Rust crates.io publishing                      |
| **sync-python-releases.yml** | UV-specific Python distribution tracking       |
| **publish-docs.yml**         | hyh has no docs site yet                       |
| **setup-dev-drive.ps1**      | Windows CI optimization for Rust builds        |
| **Matrix with Windows**      | hyh targets macOS/Linux only (Unix sockets)    |

---

## Task 1: Add zizmor Security Scanning

**Files:**

- Create: `.github/workflows/zizmor.yml`

**Step 1: Create zizmor workflow file**

```yaml
# Security scanning for GitHub Actions workflows
# See: https://github.com/woodruffw/zizmor
name: Security

on:
  push:
    branches: [master, main]
  pull_request:

permissions: {}

jobs:
  zizmor:
    runs-on: ubuntu-latest
    permissions:
      security-events: write
    steps:
      - name: Checkout
        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
        with:
          persist-credentials: false

      - name: Run zizmor
        uses: woodruffw/zizmor-action@00b7a42dde4bcbc3863e5ab5d086d21d7398fcad # v0.1.2
```

**Step 2: Verify the workflow is valid YAML**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/zizmor.yml'))"`
Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add .github/workflows/zizmor.yml
git commit -m "ci: add zizmor security scanning for GitHub Actions"
```

---

## Task 2: Pin Actions to SHA for Supply-Chain Security

**Files:**

- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/publish.yml`

**Step 1: Update ci.yml with SHA-pinned actions**

Replace action versions with SHA pins:

```yaml
# In ci.yml, replace:
# uses: actions/checkout@v4
# With:
uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2

# Replace:
# uses: astral-sh/setup-uv@v4
# With:
uses: astral-sh/setup-uv@f0ec1fc3b38f5e7cd731bb6ce540c5af426746bb # v6.8.0
```

**Step 2: Update publish.yml with SHA-pinned actions**

Same replacements plus:

```yaml
# Replace:
# uses: actions/upload-artifact@v4
# With:
uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2

# Replace:
# uses: actions/download-artifact@v4
# With:
uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
```

**Step 3: Verify workflows are valid YAML**

Run: `python -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{f}')) for f in ['ci.yml', 'publish.yml']]"`
Expected: No output (valid YAML)

**Step 4: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/publish.yml
git commit -m "ci: pin GitHub Actions to immutable SHAs for supply-chain security"
```

---

## Task 3: Migrate from Dependabot to Renovate

**Files:**

- Delete: `.github/dependabot.yml`
- Create: `.github/renovate.json5`

**Step 1: Create Renovate configuration**

```json5
// Renovate configuration for hyh
// See: https://docs.renovatebot.com/configuration-options/
{
  $schema: "https://docs.renovatebot.com/renovate-schema.json",
  extends: ["config:recommended", ":semanticCommitTypeAll(chore)"],
  // Run Monday mornings UTC
  schedule: ["before 3am on Monday"],
  // Disable semantic commits (we use conventional commits manually)
  semanticCommits: "disabled",
  // GitHub Actions: pin to SHA with semver comments
  packageRules: [
    {
      matchManagers: ["github-actions"],
      pinDigests: true,
      semanticCommitType: "ci",
    },
    // Group all dev dependencies
    {
      matchDepTypes: ["devDependencies"],
      groupName: "dev dependencies",
    },
    // Group Python dependencies
    {
      matchManagers: ["pip_requirements", "pep621"],
      groupName: "python dependencies",
    },
  ],
  // Enable dependency dashboard
  dependencyDashboard: true,
}
```

**Step 2: Delete dependabot.yml**

Run: `rm .github/dependabot.yml`

**Step 3: Verify Renovate config is valid JSON5**

Run: `python -c "import json5; json5.load(open('.github/renovate.json5'))"`
Note: Requires `pip install json5` or just verify manually

**Step 4: Commit**

```bash
git add .github/renovate.json5
git rm .github/dependabot.yml
git commit -m "ci: migrate from Dependabot to Renovate for dependency management"
```

---

## Task 4: Add Issue Template Config

**Files:**

- Create: `.github/ISSUE_TEMPLATE/config.yml`

**Step 1: Create issue template config**

```yaml
# Issue template configuration
# Provides helpful links and allows blank issues
blank_issues_enabled: true
contact_links:
  - name: Documentation
    url: https://github.com/pproenca/hyh#readme
    about: Please consult the README before creating an issue.
  - name: Discussions
    url: https://github.com/pproenca/hyh/discussions
    about: Ask questions and discuss ideas in GitHub Discussions.
```

**Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/ISSUE_TEMPLATE/config.yml'))"`
Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/config.yml
git commit -m "docs: add issue template config with helpful links"
```

---

## Task 5: Add Question Issue Template

**Files:**

- Create: `.github/ISSUE_TEMPLATE/question.yml`

**Step 1: Create question template**

```yaml
name: Question
description: Ask a question about hyh
labels: ["question"]
body:
  - type: markdown
    attributes:
      value: |
        Have a question about hyh? Check the [README](https://github.com/pproenca/hyh#readme) first.
  - type: textarea
    id: question
    attributes:
      label: Question
      description: Describe your question in detail.
    validations:
      required: true
  - type: input
    id: version
    attributes:
      label: Version
      description: "What version of hyh are you using? (run: hyh --version)"
      placeholder: "e.g., 0.1.0a1"
  - type: input
    id: platform
    attributes:
      label: Platform
      description: Your OS and architecture
      placeholder: "e.g., macOS 14 arm64, Ubuntu 24.04 amd64"
```

**Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/ISSUE_TEMPLATE/question.yml'))"`
Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/question.yml
git commit -m "docs: add question issue template"
```

---

## Task 6: Enhance Bug Report Template (UV-style)

**Files:**

- Modify: `.github/ISSUE_TEMPLATE/bug_report.yml`

**Step 1: Update bug report with version command hints**

````yaml
name: Bug Report
description: File a bug report to help us improve hyh
labels: ["bug"]
body:
  - type: markdown
    attributes:
      value: |
        Thanks for taking the time to fill out this bug report!
        Please include a minimal reproducible example if possible.
  - type: textarea
    id: description
    attributes:
      label: Summary
      description: A clear and concise description of the bug, including a minimal reproducible example.
      placeholder: |
        When I run `hyh start`, I get the following error:
        ```
        Error: ...
        ```
    validations:
      required: true
  - type: textarea
    id: reproduction
    attributes:
      label: Steps to Reproduce
      description: Steps to reproduce the behavior.
      placeholder: |
        1. Run `hyh daemon start`
        2. Run `hyh task add "test"`
        3. See error
    validations:
      required: true
  - type: textarea
    id: expected
    attributes:
      label: Expected Behavior
      description: A clear and concise description of what you expected to happen.
    validations:
      required: true
  - type: input
    id: platform
    attributes:
      label: Platform
      description: "Your OS and architecture (run: uname -orsm)"
      placeholder: "e.g., macOS 14 arm64, Ubuntu 24.04 amd64"
    validations:
      required: true
  - type: input
    id: version
    attributes:
      label: hyh Version
      description: "What version of hyh are you using? (run: hyh --version)"
      placeholder: "e.g., 0.1.0a1"
    validations:
      required: true
  - type: input
    id: python
    attributes:
      label: Python Version
      description: "What Python version are you using? (run: python --version)"
      placeholder: "e.g., Python 3.13.1"
````

**Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('.github/ISSUE_TEMPLATE/bug_report.yml'))"`
Expected: No output (valid YAML)

**Step 3: Commit**

```bash
git add .github/ISSUE_TEMPLATE/bug_report.yml
git commit -m "docs: enhance bug report template with version hints"
```

---

## Summary

After completing all tasks, hyh will have:

1. **Security scanning** via zizmor for GitHub Actions vulnerabilities
2. **Supply-chain security** via SHA-pinned actions
3. **Better dependency management** via Renovate (grouped updates, scheduled)
4. **Improved contributor experience** via issue template config and question template
5. **Better bug reports** with explicit version/platform fields

### Final Directory Structure

```
.github/
├── ISSUE_TEMPLATE/
│   ├── bug_report.yml      (enhanced)
│   ├── config.yml          (new)
│   ├── feature_request.yml (unchanged)
│   └── question.yml        (new)
├── workflows/
│   ├── ci.yml              (SHA-pinned)
│   ├── publish.yml         (SHA-pinned)
│   └── zizmor.yml          (new)
├── PULL_REQUEST_TEMPLATE.md (unchanged)
└── renovate.json5          (new, replaces dependabot.yml)
```
