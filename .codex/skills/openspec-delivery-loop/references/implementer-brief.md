# Implementer Brief

The implementer changes code to satisfy the normalized OpenSpec contract.

## Rules

- Read the contract summary and relevant OpenSpec artifacts before editing.
- Keep changes scoped to pending tasks and required behavior.
- Prefer existing architecture, helpers, conventions, and test style.
- Do not introduce new dependencies unless the design requires them or the user approves.
- Do not perform unrelated cleanup, formatting churn, or opportunistic refactors.
- Preserve user or unrelated worktree changes.
- Update task checkboxes only after the related behavior is implemented and focused verification has passed.

## Implementation Evidence

Track:
- tasks completed
- files changed
- behavioral contract covered by each change
- focused verification run before handing off
- known gaps or assumptions

## Stop Conditions

Pause and report when:
- artifacts conflict
- a task is unclear
- implementation reveals a design flaw
- a required dependency or external state is missing
- a focused test cannot be made to pass after reasonable investigation

## Handoff Format

```text
Implementation handoff
- Completed tasks: ...
- Files changed: ...
- Contract coverage: ...
- Focused verification: ...
- Known gaps: ...
```
