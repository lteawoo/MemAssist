# isolated-memory-judgment Specification

## Purpose
Define how memassist evaluates prompt-derived durable memories with an isolated judge, preserves user meaning, avoids contaminated context, and stages policy-like memories until explicit activation.
## Requirements
### Requirement: memassist SHALL judge prompt-derived durable memories in isolation

memassist SHALL use an isolated memory judge for automatic durable memories derived from user prompts. The judge input MUST be limited to source events and allowed compact project hints, and MUST exclude assistant responses, injected memory context, system/developer prompts, current agent reasoning, and full conversation history.

#### Scenario: User directive is judged from source event only

- **WHEN** a user prompt says `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **THEN** memassist SHALL create or enqueue a source event containing the user prompt
- **AND** the isolated judge input SHALL include that source event
- **AND** the isolated judge input SHALL NOT include assistant echo or retrieved memory context

#### Scenario: Assistant echo is not a memory source

- **WHEN** an assistant response says `앞으로 페이지/브라우저/서버 등 리프레시에 해당하는 동작은 실행 전에 먼저 승인 요청하겠습니다`
- **THEN** memassist SHALL NOT use that assistant response as the source for a durable memory derived from the user's preference

### Requirement: memassist SHALL keep automatic memory ingestion invisible to users

Users SHALL NOT need to know about memory commands or explicitly request memory activation for normal low-risk automatic memory ingestion.

#### Scenario: Low-risk preference is automatically stored

- **WHEN** the isolated judge determines that a user prompt contains a low-risk durable preference
- **THEN** memassist SHALL store the memory without requiring the user to run a memory command
- **AND** future RAG SHALL be able to retrieve that memory

#### Scenario: Memory system knowledge is not required

- **WHEN** a user states a durable preference in natural language
- **THEN** memassist SHALL evaluate it for memory ingestion without requiring the user to mention memassist, memory, RAG, or storage

### Requirement: memassist SHALL stage policy-like memory separately from policy compilation

When the isolated judge identifies a directive that affects approval, warning, blocking, sensitive paths, or protected paths, memassist SHALL store it as a candidate or non-policy active reminder unless an activation workflow explicitly compiles it into project policy.

#### Scenario: Approval-before-edit directive becomes memory candidate

- **WHEN** the isolated judge accepts `앞으로 refresh token 쪽은 고치기 전에 나한테 먼저 물어봐`
- **THEN** memassist SHALL store a directive memory or candidate preserving the `refresh token` subject
- **AND** memassist SHALL NOT immediately add `src/auth/refresh-token-policy.ts` to `protected_paths` during `UserPromptSubmit`

#### Scenario: Judge output preserves source quote

- **WHEN** memassist stores a memory created by the isolated judge
- **THEN** the stored record or lifecycle metadata SHALL include the source quote used by the judge

### Requirement: memassist SHALL control judge token cost

memassist SHALL record every non-empty user prompt as a pending source event without keyword-based pre-filtering, and SHALL run the isolated judge over pending source events at turn end (the Stop lifecycle event) using a low-cost model, rather than blocking the prompt with inline judgment. Whether a prompt yields durable memory SHALL be decided by the isolated judge, not by a keyword rule.

#### Scenario: Directive without trigger keywords is still judged

- **WHEN** a user prompt expresses a durable preference but matches no predefined keyword list
- **THEN** memassist SHALL record it as a pending source event
- **AND** memassist SHALL submit it to the isolated judge at turn end

#### Scenario: Ordinary task is recorded but not inline-judged

- **WHEN** a user prompt asks for a one-off task such as `refresh token TTL을 15분으로 바꿔줘`
- **THEN** memassist SHALL NOT run the isolated judge during `UserPromptSubmit`
- **AND** the isolated judge at turn end MAY determine that no durable memory should be stored

#### Scenario: UserPromptSubmit only reads memory

- **WHEN** a user submits a prompt
- **THEN** memassist SHALL retrieve and inject relevant memory during `UserPromptSubmit`
- **AND** memassist SHALL NOT create durable memory during `UserPromptSubmit`

### Requirement: memassist SHALL keep session approval separate from durable memory judgment

Explicit approval prompts SHALL continue to create short-lived session approval grants and SHALL NOT create durable memory candidates through the isolated judge.

#### Scenario: Approval prompt creates grant only

- **WHEN** a user prompt says `승인`
- **THEN** memassist SHALL create a session-scoped approval grant when applicable
- **AND** memassist SHALL NOT create a durable memory candidate from that approval prompt

### Requirement: memassist SHALL select the isolated judge backend from initialized tools

memassist SHALL choose the isolated memory judge backend from the tools initialized for the project, rather than from a single hardcoded tool. When more than one initialized tool provides a judge adapter, memassist SHALL select deterministically by a fixed preference order. When no initialized tool provides a judge adapter, memassist SHALL return an unavailable judge and SHALL NOT write durable prompt-derived memory.

#### Scenario: Claude-only project selects the Claude judge

- **WHEN** a project has initialized the `claude` tool and not the `codex` tool
- **THEN** memassist SHALL select the Claude judge backend for prompt-derived durable memory
- **AND** memassist SHALL NOT report the judge as unavailable solely because `codex` is absent

#### Scenario: Codex remains the backend when initialized

- **WHEN** a project has initialized the `codex` tool
- **THEN** memassist SHALL select the Codex judge backend
- **AND** Codex-backed judgment behavior SHALL be unchanged

#### Scenario: No initialized judge tool

- **WHEN** a project has not initialized any tool that provides a judge adapter
- **THEN** memassist SHALL return an unavailable judge
- **AND** memassist SHALL NOT write durable prompt-derived memory

### Requirement: memassist SHALL support a Claude Code isolated judge backend

memassist SHALL provide a Claude Code judge adapter that evaluates a source event in a separate `claude` process and returns a structured judgment. The adapter MUST locate the judge JSON within the Claude CLI result envelope, MUST set recursion-guard environment variables on the subprocess, and MUST record diagnostics without writing durable memory when the process fails or returns invalid output.

#### Scenario: Claude judge JSON is parsed from the result envelope

- **WHEN** the Claude judge process returns an envelope whose `result` field contains the judge JSON object
- **THEN** memassist SHALL parse the judgment from the `result` field
- **AND** memassist SHALL apply the same storage rules as any other judge backend, including durable/transient separation and meaning preservation

#### Scenario: Nested Claude hook does not re-run the judge

- **WHEN** memassist launches the Claude judge subprocess with the recursion-guard environment set
- **THEN** any memassist hook invoked by that child Claude session SHALL short-circuit without recording trace, retrieving memory, or running the judge again

#### Scenario: Claude judge failure records diagnostics only

- **WHEN** the Claude judge process times out, errors, or returns output without a valid judgment object
- **THEN** memassist SHALL record judge diagnostics in the trace
- **AND** memassist SHALL NOT write a durable memory for that source event

### Requirement: memassist SHALL report isolated judge backend readiness in diagnostics

memassist diagnostics (`doctor`) SHALL report whether an isolated judge backend is available for the project and which initialized tool backs it, instead of reporting a separate directive interpreter. The report MUST NOT hardcode a single tool: a project whose only judge-capable tool is Claude SHALL be reported as judge-ready.

#### Scenario: Claude-only project reports judge ready

- **WHEN** a project has initialized the `claude` tool and `doctor` runs
- **THEN** the diagnostic SHALL report that an isolated judge backend is available
- **AND** the diagnostic SHALL identify `claude` as the backing tool
- **AND** the diagnostic SHALL NOT report that a directive interpreter is missing

#### Scenario: Codex project reports judge ready

- **WHEN** a project has initialized the `codex` tool and `doctor` runs
- **THEN** the diagnostic SHALL report that an isolated judge backend is available
- **AND** the diagnostic SHALL identify `codex` as the backing tool

#### Scenario: No judge-capable tool reports degraded judgment

- **WHEN** a project has not initialized any tool that provides a judge adapter and `doctor` runs
- **THEN** the diagnostic SHALL report that isolated judgment is unavailable
- **AND** the diagnostic SHALL NOT claim a working LLM directive interpreter

### Requirement: memassist SHALL tolerate code-fenced or prose-wrapped judge JSON

memassist SHALL extract the judge JSON object from backend output even when the object is wrapped in a markdown code fence or surrounded by prose. Extraction MUST apply to the inner string fields of a CLI result envelope (such as `result`, `text`, or `item.text`), not only to the outermost output, and MUST prefer an object containing `should_store`.

#### Scenario: Fenced JSON in a result envelope is parsed

- **WHEN** a judge backend returns an envelope whose `result` field is a string containing a ```json fenced judgment object
- **THEN** memassist SHALL parse the inner judgment object
- **AND** memassist SHALL NOT return the outer envelope as the judgment

#### Scenario: Clean and JSONL outputs still parse

- **WHEN** a judge backend returns a clean JSON object, or a JSONL stream whose item text holds the object
- **THEN** memassist SHALL parse the judgment object as before

### Requirement: memassist SHALL run the Claude judge with a configurable low-cost model

memassist SHALL invoke the Claude judge backend with a low-cost model by default, and SHALL allow the model to be overridden by environment configuration. The Codex backend SHALL be unaffected.

#### Scenario: Default low-cost model

- **WHEN** memassist invokes the Claude judge without a model override configured
- **THEN** the judge process SHALL be launched with a low-cost default model

#### Scenario: Model override is honored

- **WHEN** a model override is configured via the judge model environment variable
- **THEN** the Claude judge process SHALL be launched with the configured model

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

