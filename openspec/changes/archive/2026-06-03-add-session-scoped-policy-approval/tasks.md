## 1. Approval Model

- [x] 1.1 Add session-scoped approval grant and consume helpers backed by trace events.
- [x] 1.2 Detect concise explicit approval prompts in Korean and English.
- [x] 1.3 Match approvals only against protected/sensitive path decisions in the same session/project.

## 2. Hook Integration

- [x] 2.1 Record approval grants from `UserPromptSubmit`.
- [x] 2.2 Apply and consume approval grants before emitting `PreToolUse` block/warn output.
- [x] 2.3 Preserve existing default behavior when no approval exists.

## 3. Verification

- [x] 3.1 Add regression tests for approved protected path edit, one-use consumption, and no-approval block.
- [x] 3.2 Add regression coverage for glob protected path approval matching.
- [x] 3.3 Run focused tests and full test suite.
- [x] 3.4 Run strict OpenSpec validation.
