---
description: Show hyh commands and current workflow state
---

# hyh Help

Display available commands and current state:

1. Run: `hyh workflow status`
2. Show this help:

## Commands

| Command | Description |
|---------|-------------|
| `/hyh specify <idea>` | Start new feature - creates worktree, generates spec |
| `/hyh plan` | Generate design artifacts and tasks from spec |
| `/hyh implement` | Execute tasks with daemon coordination |
| `/hyh status` | Show current workflow phase and progress |

## Workflow

```text
specify → plan → implement → merge
   ↓        ↓         ↓
spec.md  tasks.md  working code
```

## Worktree Commands

| Command | Description |
|---------|-------------|
| `hyh worktree create <slug>` | Create new feature worktree |
| `hyh worktree list` | List all feature worktrees |
| `hyh worktree switch <slug>` | Show path to switch to worktree |
