## Context

memassist currently mixes two responsibilities:

- memory ingestion/retrieval through `UserPromptSubmit`
- policy enforcement through `PreToolUse`

That coupling caused user memories to be converted into `sensitive_paths`, `protected_paths`, `warn_policy`, and `block_policy`. It also hid the real product gap: retrieval is still too token/keyword-oriented, so a Korean prompt like `리프레시 토큰 15분으로 변경해줘` can miss an English-normalized memory about `refresh token`.

There are no existing users to migrate. The implementation can remove old memory-derived policy behavior instead of preserving compatibility.

## Goals / Non-Goals

**Goals:**

- Preserve the user's source-language memory as the primary stored memory content.
- Keep optional normalized metadata for retrieval, without replacing or translating the user's memory.
- Make `UserPromptSubmit` the only RAG injection point.
- Inject retrieved memories as contextual memory for the active coding agent.
- Remove memory-derived policy compilation and enforcement from activation, lifecycle, and pre-tool hooks.
- Keep manual `policy.yaml` checks available only as explicit project configuration.
- Update tests and docs so success means relevant memory retrieval, not autonomous blocking.

**Non-Goals:**

- No migration of old `warn_policy`, `block_policy`, or policy-mutated databases.
- No guarantee that old exported memory files preserve compatibility.
- No new remote embedding service dependency in this change.
- No attempt to make memassist decide whether a current task is approved. That judgment belongs to the active agent receiving RAG context.

## Decisions

### Store source-language content as primary memory

The stored `Memory.content` SHALL preserve the user's language and phrasing when the memory is derived from a user prompt. LLM/judge output may provide retrieval metadata such as tags, scope terms, candidate paths, or normalized subject, but it SHALL NOT replace the memory with translated policy prose.

Alternative considered: store normalized English memory text. This improves English keyword search but loses user meaning, creates translation drift, and breaks multilingual trust.

### RAG output is memory-only

`render_prompt_context` SHALL emit memory-oriented sections. `Policy reminders` SHALL be removed because it frames retrieved memories as memassist-enforced policy. Directive memories can still appear, but as ordinary relevant memories.

Alternative considered: keep policy and verifier sections but rename them. This keeps old coupling and makes future behavior harder to reason about.

### Remove memory activation as policy compilation

`memory activate` SHALL only activate the memory record. It SHALL NOT append to `sensitive_paths` or `protected_paths`, and SHALL NOT convert memory status to `warn_policy` or `block_policy`.

Alternative considered: keep explicit activation as a policy compiler. The user rejected this project direction; memassist is not a policy-authoring tool by default.

### PreToolUse does not consult memories

`PreToolUse` MAY continue checking explicit `policy.yaml`, but SHALL NOT apply memory-derived approvals or policy decisions. Default project initialization still creates empty policy lists.

Alternative considered: infer approval from retrieved memories at tool time. That duplicates agent judgment and reintroduces hidden policy behavior.

### Multilingual retrieval without new infrastructure

This change SHALL improve local retrieval by indexing source content plus meaning-preserving metadata. Tests SHALL cover Korean/English refresh-token variants and common typo forms. A future change may add embeddings or LLM retrieval reranking.

Alternative considered: add embeddings now. That is broader than the enforcement-removal change and introduces provider/runtime decisions that should be designed separately.

## Risks / Trade-offs

- [Risk] Removing memory-derived blocking may let tools edit areas the user expected the agent to handle carefully. → Mitigation: retrieve the relevant memory earlier through `UserPromptSubmit` so the active agent sees it before acting.
- [Risk] Local semantic retrieval remains imperfect without embeddings. → Mitigation: use source-language content, tags, paths, and normalized metadata in the index; add regression tests for the known Korean/English refresh-token miss.
- [Risk] Existing tests encode policy activation behavior. → Mitigation: update them to assert retrieval injection and no policy mutation.
- [Risk] Manual policy users may still expect `policy.yaml` checks. → Mitigation: keep explicit policy checks intact and document them as manual project policy, not memory behavior.
