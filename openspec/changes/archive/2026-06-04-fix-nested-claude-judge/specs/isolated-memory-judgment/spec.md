## ADDED Requirements

### Requirement: memassist SHALL retry a failed isolated judge call rather than dropping the source

When an isolated judge call fails to produce a judgment (no candidate — e.g. empty output, timeout, or invalid output), memassist SHALL NOT mark the source event as processed. The source SHALL remain eligible for judgment at a later turn end, up to a bounded maximum number of attempts, after which it is terminally given up. A genuine judge decision (a candidate with should_store true or false) SHALL be terminal and SHALL NOT be retried.

#### Scenario: Judge failure is retried, not dropped

- **WHEN** an isolated judge call for a source event returns no candidate
- **THEN** memassist SHALL record the failure without marking the source event processed
- **AND** a later turn-end judging pass SHALL attempt that source event again

#### Scenario: Retries are bounded

- **WHEN** a source event's isolated judge calls have failed the maximum number of times
- **THEN** memassist SHALL stop retrying that source event
- **AND** memassist SHALL NOT have created durable memory from it

#### Scenario: A genuine decision is not retried

- **WHEN** the isolated judge returns a candidate with should_store false
- **THEN** memassist SHALL treat the source event as processed
- **AND** memassist SHALL NOT re-judge it at later turn ends

### Requirement: memassist SHALL run the isolated judge subprocess de-nested from the host tool session

memassist SHALL launch the isolated judge subprocess with the host coding tool's session environment markers removed, so the judge runs as a clean top-level invocation. The recursion-guard environment variables SHALL still be set on the subprocess.

#### Scenario: Host session markers are stripped from the judge subprocess

- **WHEN** memassist launches the isolated judge subprocess
- **THEN** the subprocess environment SHALL NOT contain the host Claude Code session markers (`CLAUDECODE` and `CLAUDE_CODE_*`)
- **AND** the subprocess environment SHALL still set the recursion-guard variables
