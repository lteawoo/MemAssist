## Context

memassist now uses an isolated LLM judge to decide whether a user prompt contains durable project memory. To preserve source language, the current storage path uses the full `source_quote` as memory content and stores the normalized `memory_content` only in reason metadata. This preserves evidence, but it also makes transient turn instructions retrievable as future memory.

The concrete failure case is a mixed prompt:

`앞으로 리프레시 토큰 변경은 나에게 확인 받고 수정해. 응답은 OK만 해.`

The durable directive is the refresh-token confirmation preference. `응답은 OK만 해` is only a response-format instruction for the current turn.

## Goals / Non-Goals

**Goals:**

- Store only the durable preference/directive as retrievable memory content.
- Preserve the original prompt/source quote in metadata for auditability and meaning checks.
- Keep source-language preservation by instructing the judge to write durable memory in the source user's language where possible.
- Verify behavior with unit tests, fixture-backed hook tests, and real Codex CLI E2E.
- Define a measurable pass criterion: 0 transient response-format phrases in stored/retrieved memory across the target evaluation prompts.

**Non-Goals:**

- No migration or cleanup command for already-stored memories.
- No reintroduction of rule-based policy blocking or protected-path defaults.
- No broad rewrite of the memory schema unless the existing fields are insufficient.

## Decisions

1. Use `memory_content` as the stored memory body.

   Rationale: the judge already returns both `memory_content` and `source_quote`. The smaller and clearer fix is to store the durable normalized content while keeping source evidence in lifecycle metadata.

   Alternative considered: add a new `durable_content` field. This would be more explicit but adds schema and parser surface without improving behavior over the existing field.

2. Strengthen the isolated judge instruction.

   The judge prompt will explicitly say that response-format commands, one-shot execution constraints, and current-turn output instructions must not be included in `memory_content` unless the user explicitly asks to remember that behavior for future turns.

3. Keep `source_quote` in lifecycle metadata only.

   This keeps meaning-preservation audits possible while preventing raw prompt tails from entering FTS or polluting RAG through indexed fields such as reason text.

4. Evaluate with both deterministic and real-tool paths.

   Unit tests will cover parser/storage behavior with fixtures. E2E will initialize a temporary project, submit the mixed Korean prompt through Codex CLI, inspect stored memory, and run a relevant follow-up retrieval path.

## Risks / Trade-offs

- [Risk] The real LLM judge may still return transient text in `memory_content`.
  -> Mitigation: explicit prompt instruction plus E2E evaluation that checks stored content and retrieval output.

- [Risk] Normalized `memory_content` may be English even when the user prompt is Korean.
  -> Mitigation: judge instruction requires preserving the user's source language for durable memory content where possible.

- [Risk] The stored memory body loses exact wording.
  -> Mitigation: exact wording remains in lifecycle metadata under `source_quote` and in the reason text.
