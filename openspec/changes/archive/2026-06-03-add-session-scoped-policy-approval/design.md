## Decision

Add a session-scoped approval layer around deterministic policy decisions instead of weakening policy compilation or removing protected/sensitive entries.

The deterministic policy engine still answers: "does this tool call match remembered project policy?" The hook layer then answers: "has the user explicitly approved the next matching action in this session?"

## Approval Lifecycle

1. `UserPromptSubmit` receives a short explicit approval prompt.
2. memassist records an `approval_granted` trace event with:
   - `session_id`
   - `project_id`
   - eligible protected/sensitive policy patterns
   - expiry timestamp
3. `PreToolUse` computes the deterministic policy decision.
4. If the decision is `block` or `warn` for a protected/sensitive path, memassist looks for the newest unconsumed, unexpired approval grant in the same session and project.
5. If the grant matches the target path, memassist records `approval_consumed` and changes the final hook decision to `allow`.

## Non-Goals

- Do not turn approval into durable memory.
- Do not call an LLM during `PreToolUse`.
- Do not allow approval grants to cross session or project boundaries.
- Do not remove explicit policy entries after approval.

## Tradeoffs

Granting approval for all configured protected/sensitive paths in the current project is intentionally simple and conservative because the grant is single-use, session-scoped, and short-lived. A future enhancement can narrow grants to the most recently retrieved memory or pending blocked action when tool runtimes expose richer approval context.
