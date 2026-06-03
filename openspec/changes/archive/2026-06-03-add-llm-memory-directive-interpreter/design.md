## Context

The current direct directive path runs during `UserPromptSubmit` and uses fixed term lists to decide whether a prompt is a memory directive. That works for exact phrases, but it misses multilingual variants, typos, soft wording, and domain-specific phrasing such as "리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘".

The previous change removed universal built-in enforcement defaults. This change should not reintroduce global safety rules. It should improve the interpretation layer that turns explicit user/project intent into memory and, when appropriate, project policy.

## Goals / Non-Goals

**Goals:**

- Interpret direct user memory instructions semantically using the coding tool selected during `memassist init` as the LLM-backed structured output path.
- Support multilingual, typo-tolerant, and soft-phrased directives.
- Extract structured directive fields: intent, subject/scope, enforcement, rationale, confidence, and candidate paths.
- Immediately compile high-confidence explicit directives into memory-derived `warn_policy` or `block_policy` entries.
- Preserve deterministic policy matching after policy entries exist.
- Provide deterministic fallbacks and observable traces when the initialized tool cannot provide interpretation or the LLM result is uncertain.
- Validate behavior through unit tests, hook simulations, and real initialized-tool CLI E2E coverage.

**Non-Goals:**

- Letting the LLM directly allow, warn, or block tool calls at enforcement time.
- Reintroducing universal default policies for `.env`, private keys, destructive commands, or similar generic safety concerns.
- Automatically escalating ambiguous inferred observations into blocking policy without explicit user/project instruction.
- Requiring every supported coding tool to expose identical hook semantics when the tool itself does not provide equivalent lifecycle events.

## Decisions

### 1. LLM interprets directives, deterministic policy enforces them

The LLM should run before memory creation, not during every pre-tool enforcement decision.

Flow:

```text
UserPromptSubmit
  -> DirectiveInterpreter
       -> structured DirectiveCandidate
  -> confidence and autonomy gate
  -> Memory + immediate policy path compilation for high-confidence explicit directives
  -> deterministic PolicyEngine during PreToolUse
```

This keeps runtime enforcement auditable and avoids delegating hard blocks directly to a model response.

Alternative considered: using the LLM inside `PolicyEngine.check_pre_tool`. Rejected because enforcement must be stable, explainable, fast, and derived from already-remembered project intent.

### 2. The initialized coding tool is the default interpreter backend

memassist should use the tool selected during `memassist init --tools ...` as the default LLM interpreter backend. This avoids a second provider/model configuration path and keeps the memory assistant aligned with the project user's active coding workflow.

Adapters should be capability-based:

- Codex adapter: use Codex CLI non-interactive model execution where available, with hook recursion disabled.
- Claude Code adapter: use Claude Code CLI/model execution where available, with equivalent recursion protection.
- opencode adapter: use opencode model execution where available.
- fallback adapter: deterministic interpreter only.

The adapter must set a recursion guard so an interpreter call made from a hook does not invoke memassist hooks again. It must also enforce timeout and parse-only structured output constraints.

Alternative considered: separate provider configuration such as direct OpenAI/Anthropic SDK settings. Rejected for the default path because it creates another model identity and setup burden. It may remain an explicit override later, but is not the primary behavior for this change.

### 3. Structured output is the interpreter contract

The interpreter should return a constrained object, not prose:

- `is_directive`
- `intent`
- `subject`
- `enforcement`: `none`, `remember`, `warn`, or `block`
- `scope_terms`
- `candidate_paths`
- `confidence`
- `rationale`
- `normalized_prompt`

The existing keyword path can remain only as a fallback or test double, not as the primary semantic layer.

### 4. High-confidence explicit directives compile immediately

High-confidence explicit directives should be activated directly and compiled into memory-derived project policy immediately when the scope/path is resolved. Low-confidence or ambiguous outputs should become draft/candidate memories with no policy mutation.

Suggested thresholds:

- `>= 0.85`: activate direct directive and immediately compile policy if a path/scope is resolved.
- `0.60..0.84`: store candidate/draft memory without enforcement.
- `< 0.60`: do not store, but trace interpreter uncertainty for debugging.

Exact thresholds should be tuned through tests and fixtures.

This direction intentionally favors the user's explicit memory instruction over an extra approval step. Autonomy is preserved by requiring explicit directive intent, high confidence, resolved scope/path, traceability, and rollback/deactivation support.

### 5. Scope resolution is separate from intent interpretation

The LLM may propose scope terms and candidate path hints, but final path mapping should be performed by project-aware code. That resolver can combine:

- files touched in the current session
- project file names and symbols
- existing memories
- LLM-provided subject/scope terms
- deterministic path scoring

If no path can be resolved, the directive may still be remembered, but it must not become an active path policy.

### 6. Tool compatibility is capability-based

The tool selected during `init` should be the E2E target for that project. Codex should remain the first fully verified integration because it exposes prompt, pre-tool, post-tool, and stop hooks. Claude Code and opencode should be documented/tested based on whether their integrations can provide equivalent lifecycle payloads and model invocation adapters.

Compatibility should be reported as:

- prompt memory injection supported
- pre-tool policy enforcement supported
- post-tool trace extraction supported
- stop/lifecycle memory extraction supported

## Risks / Trade-offs

- **LLM false positives could create unwanted policy.** Mitigation: require explicit directive intent, confidence gates, lifecycle records, and rollback support.
- **LLM false negatives could miss user intent.** Mitigation: keep fallback keyword behavior and add regression fixtures for multilingual/typo cases.
- **Initialized tool may not support non-interactive interpretation.** Mitigation: fallback to deterministic interpreter and report degraded mode in `doctor`.
- **Interpreter calls from hooks could recurse into hooks.** Mitigation: set an environment recursion guard and disable memassist hook execution for adapter subprocesses.
- **Path resolution could attach a directive to the wrong file.** Mitigation: separate intent confidence from path confidence; only compile policy when both are high enough.
- **Hook behavior differs across tools.** Mitigation: define compatibility by lifecycle capability, not by claiming all tools behave identically.
- **Verification memories may over-apply.** Mitigation: make verifier retrieval task-aware and test unrelated-task prompts.

## Migration Plan

1. Add the interpreter interface and deterministic fallback implementation.
2. Add initialized-tool LLM adapter implementations behind capability detection.
3. Route `UserPromptSubmit` through the interpreter.
4. Add structured lifecycle events for interpreter decisions and confidence.
5. Update policy compilation to use resolved directive candidates.
6. Add unit/integration tests for typo, multilingual, soft wording, ambiguous, and low-confidence cases.
7. Add temp-project E2E scripts for `init`, initialized-tool hook execution, memory search/pack, policy check, and verification.

Rollback: disable the initialized-tool LLM interpreter via configuration and keep the deterministic fallback path.

## Open Questions

- Should a high-confidence `warn` directive with unresolved path be injected as reminder-only memory until a path is resolved?
- Should shell commands that reference protected paths be hard-blocked by policy matching, or should that remain model-guided behavior through prompt injection?
- How should users inspect, approve, or rollback LLM-created policy memories in the CLI and GUI?
