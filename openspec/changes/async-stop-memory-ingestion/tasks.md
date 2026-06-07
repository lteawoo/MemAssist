## 1. Async Ingestion Control

- [x] 1.1 Add Stop ingestion mode parsing with async as the default and explicit sync/off modes.
- [x] 1.2 Add project-local async worker lock with TTL and bounded batch configuration.
- [x] 1.3 Add detached one-shot worker spawn that preserves project-local MEMASSIST_HOME and does not wait in Stop hooks.

## 2. Hook And Worker Behavior

- [x] 2.1 Change Stop hook to record trace/source evidence and return quickly in async mode.
- [x] 2.2 Extend `memassist daemon once` to process pending source records before lifecycle cleanup.
- [x] 2.3 Ensure trace mode records Stop trace data without enqueueing or spawning memory ingestion.
- [x] 2.4 Preserve sync mode for debugging and existing deterministic test flows.

## 3. Diagnostics

- [x] 3.1 Add diagnostics for async ingestion mode, pending source count, worker lock state, and recent worker failures.
- [x] 3.2 Ensure worker failures are recorded as memassist trace diagnostics instead of host hook timeouts.

## 4. Verification

- [x] 4.1 Add tests proving Stop async mode does not synchronously invoke judge work.
- [x] 4.2 Add tests proving `daemon once` processes pending source records into retrievable memories.
- [x] 4.3 Add tests for trace/off/sync mode behavior and duplicate worker lock behavior.
- [x] 4.4 Run focused hook/daemon tests, full memassist unit/E2E tests, and strict OpenSpec validation.
- [x] 4.5 Run separate-agent QA review and iterate until score is at least 90/100 with no critical/high findings.
