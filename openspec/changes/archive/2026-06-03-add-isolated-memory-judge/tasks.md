## 1. Source Event Pipeline

- [x] 1.1 Add trace/lifecycle representation for `memory_intent_observed` source events with source role, content, session, project, and allowed hint metadata.
- [x] 1.2 Update `UserPromptSubmit` to enqueue source events instead of directly creating directive memories or compiling policy.
- [x] 1.3 Preserve current memory retrieval and `memory_injected` behavior in `UserPromptSubmit`.
- [x] 1.4 Preserve explicit session approval grant behavior and ensure approval prompts do not enqueue durable memory candidates.

## 2. Isolated Memory Judge

- [x] 2.1 Add a structured judge candidate model with `should_store`, `memory_content`, `source_quote`, `memory_type`, `enforcement`, `activation`, `candidate_paths`, `policy_compile`, `meaning_preserved`, `contamination_risk`, and `reason`.
- [x] 2.2 Implement a judge interface and initialized-tool adapter that runs with hook recursion disabled.
- [x] 2.3 Build minimal judge payloads from source events, allowed project hints, and existing memory conflicts only.
- [x] 2.4 Ensure judge payloads exclude assistant responses, retrieved memory context, system/developer prompts, current agent reasoning, and full conversation history.
- [x] 2.5 Validate judge JSON shape and store invalid/failed judge outputs as trace diagnostics without writing durable memory.

## 3. Memory Lifecycle And Activation

- [x] 3.1 Store accepted judge outputs as memory candidates or low-risk active memories according to the judge activation field.
- [x] 3.2 Store source quote/provenance with judge-created memories or lifecycle metadata.
- [x] 3.3 Prevent policy-like judge outputs from compiling project policy during `UserPromptSubmit`.
- [x] 3.4 Add an activation path that can compile a judged memory into `sensitive_paths` or `protected_paths` only after explicit activation.
- [x] 3.5 Add cleanup/reporting support for memories likely created from assistant echo or distorted semantic subjects.

## 4. Scheduling And Cost Control

- [x] 4.1 Add immediate isolated judgment for explicit memory-intent source events.
- [x] 4.2 Add batch judgment from `stop` or maintenance flow for lower-priority source events.
- [x] 4.3 Cap judge payload size and record token/cost-relevant diagnostics where available.
- [x] 4.4 Add fallback behavior when no initialized judge-capable tool is available.

## 5. Verification

- [x] 5.1 Add tests proving `UserPromptSubmit` no longer directly creates durable directive memory or policy entries.
- [x] 5.2 Add tests proving `refresh token` directives are judged from the user source event and not assistant echo.
- [x] 5.3 Add tests proving assistant echo cannot become the source for the durable directive.
- [x] 5.4 Add tests proving low-risk preferences can auto-activate while policy-like directives remain staged.
- [x] 5.5 Add tests proving explicit approval prompts create only session approval grants, not memory candidates.
- [x] 5.6 Add tests proving judge payloads exclude injected memory context and assistant responses.
- [x] 5.7 Run focused tests, full test suite, and strict OpenSpec validation.

## 6. Documentation

- [x] 6.1 Document the automatic isolated memory ingestion flow and why users do not need to operate memory explicitly.
- [x] 6.2 Document the difference between source events, judge-created candidates, active memories, session approval grants, and compiled policy.
- [x] 6.3 Document migration/cleanup guidance for distorted memories and assistant-echo memories.
