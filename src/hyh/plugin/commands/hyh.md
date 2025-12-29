---
description: Spec-driven development workflow - specify, plan, implement
argument-hint: [specify|plan|implement|status] [args]
allowed-tools: Bash(hyh:*), Bash(git:*), Read, Write, Edit, Glob, Grep
---

# hyh - Spec-Driven Development

Route based on $ARGUMENTS:

## If $ARGUMENTS starts with "specify"

Extract the feature description after "specify". Then:

1. Generate a slug from the description (2-4 words, kebab-case)
2. Get next feature number: `hyh workflow status --json` and increment
3. Create worktree: `hyh worktree create {N}-{slug}`
4. Load spec template and fill with user's description
5. Ask up to 5 clarifying questions (one at a time) for [NEEDS CLARIFICATION] markers
6. Write finalized spec to `specs/spec.md`
7. Report: "Spec complete. Run `/hyh plan` to continue."

## If $ARGUMENTS starts with "plan"

1. Verify `specs/spec.md` exists
2. Load spec and constitution (if `.hyh/constitution.md` exists)
3. Generate `specs/research.md` (resolve technical unknowns)
4. Generate `specs/plan.md` (architecture, tech stack)
5. Generate `specs/data-model.md` if entities involved
6. Generate `specs/tasks.md` in speckit checkbox format
7. Generate `specs/checklists/requirements.md`
8. Run consistency analysis
9. Import tasks: `hyh plan import --file specs/tasks.md`
10. Report: "Plan complete. Run `/hyh implement` to continue."

## If $ARGUMENTS starts with "implement"

1. Run: `hyh workflow status` to verify tasks exist
2. Check checklists pass (or ask to proceed)
3. Loop:
   a. `hyh task claim` → get next task
   b. If no task: done
   c. Execute task per instructions
   d. `hyh task complete --id {id}`
   e. Update specs/tasks.md with [x]
4. Report completion

## If $ARGUMENTS is empty or "status"

Run: `hyh workflow status`

Based on result, suggest next action:
- No spec? → "Start with: /hyh specify <your feature idea>"
- Has spec, no plan? → "Continue with: /hyh plan"
- Has tasks? → "Continue with: /hyh implement"
- All complete? → "All done! Ready to merge."
