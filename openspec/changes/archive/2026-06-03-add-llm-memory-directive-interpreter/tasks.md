## 1. Interpreter Interface

- [x] 1.1 Add a structured `DirectiveCandidate` model with fields for directive detection, intent, subject, enforcement, scope terms, candidate paths, confidence, rationale, and normalized prompt.
- [x] 1.2 Add a `DirectiveInterpreter` interface and deterministic fallback implementation.
- [x] 1.3 Add initialized-tool interpreter adapter selection based on the tool configured by `memassist init`.
- [x] 1.4 Add timeout, hook recursion guard, and diagnostics for interpreter availability and fallback mode.
- [x] 1.5 Add trace/lifecycle records for interpreter decisions, adapter name, confidence, fallback usage, and invalid outputs.

## 2. LLM Directive Interpretation

- [x] 2.1 Implement the initialized-tool LLM interpreter adapter with constrained structured output parsing.
- [x] 2.2 Validate interpreter output and reject invalid, incomplete, or out-of-range values safely.
- [x] 2.3 Add prompt fixtures for multilingual, typo-tolerant, soft-phrased, non-directive, and ambiguous cases.
- [x] 2.4 Add tests proving the typo phrase `리프레쉬 토큰 쪽은 담부터 고치기 전에 꼭 나한테 먼저 말해줘` becomes a `warn` directive candidate.
- [x] 2.5 Add tests proving interpreter subprocesses do not recursively trigger memassist hooks.

## 3. Memory And Policy Compilation

- [x] 3.1 Route `UserPromptSubmit` directive handling through the interpreter.
- [x] 3.2 Implement confidence gates for immediate active policy compilation, draft/candidate memory, and no-store outcomes.
- [x] 3.3 Separate semantic scope interpretation from project path resolution.
- [x] 3.4 Immediately compile high-confidence explicit directives with resolved paths into `sensitive_paths` or `protected_paths`.
- [x] 3.5 Preserve reminder-only memory behavior when path resolution is missing.
- [x] 3.6 Ensure pre-tool enforcement remains deterministic and does not call the LLM.

## 4. Tool Compatibility And E2E

- [x] 4.1 Add a temp-project E2E harness that runs `memassist init --tools <tool>` and exercises the initialized tool's hook lifecycle events.
- [x] 4.2 Test initialized-tool hook behavior for memory creation, memory injection, immediate policy compilation, and protected operation handling.
- [x] 4.3 Test and document `codex exec` lifecycle behavior, including any unsupported hook capabilities.
- [x] 4.4 Add compatibility reporting for Codex, Claude Code, and opencode lifecycle support where integrations exist.

## 5. Verification And Regression Coverage

- [x] 5.1 Add unit tests for interpreter validation, fallback behavior, and confidence gates.
- [x] 5.2 Add integration tests proving fresh projects still have no universal `.env` or destructive-command policy defaults.
- [x] 5.3 Add tests proving high-confidence LLM-derived directives immediately compile into deterministic project policy.
- [x] 5.4 Add tests for low-confidence and non-directive prompts proving they do not mutate policy.
- [x] 5.5 Fix or account for shell command path extraction gaps discovered during E2E testing.
- [x] 5.6 Run the full test suite and OpenSpec validation.

## 6. Documentation

- [x] 6.1 Document LLM directive interpretation behavior, initialized-tool adapter selection, fallback mode, and configuration.
- [x] 6.2 Document that LLM interpretation creates memory candidates, while deterministic policy matching performs enforcement.
- [x] 6.3 Document tool compatibility by lifecycle capability rather than claiming universal support.
- [x] 6.4 Document how users inspect, deactivate, or roll back LLM-created policy memories.
