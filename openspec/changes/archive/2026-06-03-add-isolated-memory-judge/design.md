## Context

memassist currently handles direct memory directives inside `UserPromptSubmit`. That gives low latency, but it also lets the hot-path hook and the current session agent turn user language into durable memory or policy before an isolated memory decision has happened.

This is risky for a memory assistant because the current session can be contaminated by retrieved memory, assistant echo, partial plans, system instructions, and recent tool context. The product goal remains automatic memory ingestion and automatic RAG for users who do not know the memory system exists, but durable writes must preserve the user's meaning.

## Goals / Non-Goals

**Goals:**

- Preserve automatic memory ingestion and RAG without requiring users to explicitly manage memory.
- Move durable directive memory creation and policy compilation out of the `UserPromptSubmit` hot path.
- Add an isolated memory judge that sees only minimal source events and allowed project hints.
- Prevent assistant echo and retrieved context from becoming durable memory source material.
- Keep explicit session approval grants fast and separate from durable memory.
- Reduce token use by judging only memory-intent events immediately and using batch judgment for lower-priority events.

**Non-Goals:**

- Do not add rule-based semantic validation as the primary judge.
- Do not require user-facing confirmation for every low-risk memory.
- Do not store every raw prompt as a durable memory.
- Do not remove deterministic policy matching for already activated project policy.
- Do not call an LLM during `PreToolUse` enforcement.

## Decisions

### Decision: `UserPromptSubmit` records source events, not durable directive memory

`UserPromptSubmit` SHALL keep memory retrieval and explicit approval grants, but direct durable directive writes move to a source-event pipeline. The hook records a `memory_intent_observed` trace event when a user prompt may contain durable memory intent.

Alternative considered: keep direct writes but add stronger prompts. Rejected because the hot path still sees contaminated session context and can compile policy before isolated review.

### Decision: The isolated judge is the only automatic durable memory writer for prompt-derived directives

The judge receives a compact payload with the user source event, safe project hints such as recent file paths, and relevant existing memory conflicts. It does not receive assistant responses, injected RAG context, system/developer prompts, current agent reasoning, or full conversation history.

Alternative considered: current session AI creates proposals. Rejected because the current agent already carries contaminated context and can overfit to its own plan or assistant echo.

### Decision: Judge output is structured and staged

The judge returns JSON with `should_store`, `memory_content`, `source_quote`, `memory_type`, `enforcement`, `activation`, `candidate_paths`, `policy_compile`, `meaning_preserved`, `contamination_risk`, and `reason`.

Low-risk preferences can become `active`. Policy-like directives default to `candidate` or active non-policy reminders. Project policy compilation requires explicit activation by a later workflow, not immediate `UserPromptSubmit` compilation.

Alternative considered: allow high-confidence judge outputs to compile policy immediately. Rejected because the project must not lose autonomy through automatic blocking/warning policy creation from a single prompt.

### Decision: Cost control uses event selection and batching, not rule-based meaning checks

Immediate judge execution is limited to explicit memory-intent source events. Other possible memories are batched at `stop` or maintenance time. This keeps token use bounded without using fixed keyword rules as the semantic authority.

The lightweight detector that decides whether to enqueue an event is not a semantic validator. It only controls whether the isolated judge should be asked now, later, or never.

### Decision: Existing session approvals remain hot path

Prompts such as `승인` or `proceed` continue to create short-lived session approval grants. They are not durable memories and do not go through the memory judge.

## Risks / Trade-offs

- [Risk] Immediate RAG will not include a brand-new directive until judgment completes. → Use immediate judge only for explicit memory-intent events and inject accepted results as session-local hints when available.
- [Risk] More LLM calls can increase cost. → Batch non-urgent events and cap judge payloads to source event plus compact hints.
- [Risk] Judge can still make semantic mistakes. → Store source quotes with candidates, prefer candidate status for policy-like directives, and keep activation inspectable/rollbackable.
- [Risk] Existing distorted memories may remain. → Add cleanup guidance and candidate review tooling for memories with assistant-echo wording or missing source quotes.
- [Risk] Tool adapter availability differs across Codex, Claude Code, and opencode. → Reuse initialized-tool capability reporting and provide fallback behavior that records candidates for later judgment rather than writing durable memory.

## Migration Plan

1. Add storage/trace support for memory source events and judge decisions.
2. Change `UserPromptSubmit` to enqueue source events instead of calling durable directive compilation.
3. Add the isolated judge adapter and structured output validation.
4. Add lifecycle transitions for judge-created candidates and active memories.
5. Disable hot-path policy compilation for direct prompts.
6. Add cleanup/reporting for existing memories likely created from assistant echo or semantic drift.
7. Document the new automatic ingestion model and activation behavior.
