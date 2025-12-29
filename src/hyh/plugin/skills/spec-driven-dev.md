---
name: spec-driven-development
description: Use when implementing features - follow the specify → plan → implement workflow
---

# Spec-Driven Development

When implementing any non-trivial feature, use the hyh workflow:

## 1. Specify First

Before writing code, create a specification:
- Run `/hyh specify <your idea>`
- Answer clarifying questions
- Review the generated spec.md

## 2. Plan Before Implementing

Generate design artifacts:
- Run `/hyh plan`
- Review tasks.md for the work breakdown
- Check checklists pass

## 3. Implement with Tracking

Execute tasks systematically:
- Run `/hyh implement`
- Tasks are tracked via daemon
- Progress is visible with `/hyh status`

## Why This Matters

- Specs catch misunderstandings early
- Plans break work into manageable pieces
- Tracking ensures nothing is forgotten
- Worktrees keep main branch clean
