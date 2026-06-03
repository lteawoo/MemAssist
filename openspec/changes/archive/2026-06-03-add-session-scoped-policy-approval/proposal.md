## Why

`PreToolUse` currently enforces remembered protected/sensitive policy entries without considering an explicit user approval given in the same session. This means a user can say "승인" after a policy reminder, but the next protected edit is still blocked by the autonomous memory policy.

That violates memassist's role as a memory assistant: remembered policy should surface intent and friction, but should not permanently override a user's fresh, explicit instruction in the current workflow.

## What Changes

- Detect explicit approval prompts such as `승인`, `허용`, `approve`, or `go ahead` during `UserPromptSubmit`.
- Record a short-lived, session-scoped approval grant for the project's configured protected/sensitive policy paths.
- Allow the next matching protected/sensitive `PreToolUse` decision in that session and consume the grant.
- Keep dangerous-command and normal policy matching deterministic; no LLM call occurs in `PreToolUse`.
- Add regression tests for approval allow, one-use consumption, and unchanged blocking without approval.

## Impact

- Users can override remembered protected/sensitive policy for the next action after explicit approval.
- Approval is not persisted as durable memory and is not global across sessions.
- Existing protected/sensitive policy remains active when no current approval grant exists.
