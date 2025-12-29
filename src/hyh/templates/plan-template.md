# Implementation Plan: [Feature]

**Branch**: `[###-feature-name]` | **Date**: [Date] | **Specification**: [Link]
**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for execution workflow.

## Overview

[Extract from feature specification: Key requirements + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the contents of this section with your project's technical details.
  The structure shown here is provided as advisory guidance to help the iterative process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]
**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]
**Storage**: [If applicable, e.g., PostgreSQL, CoreData, File or N/A]
**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]
**Target Platform**: [e.g., Linux Server, iOS 15+, WASM or NEEDS CLARIFICATION]
**Project Type**: [single/web/mobile - determines source structure]
**Performance Goals**: [Domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]
**Constraints**: [Domain-specific, e.g., \<200ms p95, \<100MB memory, offline support or NEEDS CLARIFICATION]
**Scale/Scope**: [Domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*Gate: Must pass before Phase 0 research. Re-verify after Phase 1 design.*

**Reference**: Verify the following based on the 5 principles in `.specify/memory/constitution.md`

### I. Code Quality Principle

- [ ] Are readability and documentation requirements met?
- [ ] Are naming conventions clearly defined?
- [ ] Is code complexity within reasonable bounds?

### II. Test-Driven Development

- [ ] Is a test-first development process planned?
- [ ] Is there a plan for contract tests, integration tests, and unit tests?
- [ ] Is a test coverage target (80% or more) set?

### III. UX Consistency

- [ ] Are consistent UI patterns defined?
- [ ] Is error message clarity ensured?
- [ ] Is accessibility considered?

### IV. Performance Standards

- [ ] Are API response time targets (p95 < 200ms) considered?
- [ ] Is database optimization planned?
- [ ] Are frontend load time targets set (if applicable)?

### V. Maintainability and Extensibility

- [ ] Is modular, loosely-coupled design adopted?
- [ ] Is the configuration management policy clear?
- [ ] Is a versioning strategy defined?

**Violation Justification**: Record in the "Complexity Tracking" table in this section

## Project Structure

### Documentation (for this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (output of /speckit.plan command)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - not created by /speckit.plan)
```

### Source Code (repository root)

<!--
  ACTION REQUIRED: Replace the placeholder tree below with the specific layout for this feature.
  Delete unused options and expand the chosen structure with actual paths (e.g., apps/admin, packages/something).
  The provided plan should not include option labels.
-->

```text
# [Delete if unused] Option 1: Single Project (default)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [Delete if unused] Option 2: Web Application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [Delete if unused] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document chosen structure and reference actual directories captured above]

## Complexity Tracking

> **Only fill in if there are violations requiring justification in the Constitution Check**

| Violation                  | Reason Needed      | Why Simpler Alternative Was Rejected   |
| -------------------------- | ------------------ | -------------------------------------- |
| [e.g., 4th project]        | [Current need]     | [Why 3 projects are insufficient]      |
| [e.g., Repository pattern] | [Specific problem] | [Why direct DB access is insufficient] |
