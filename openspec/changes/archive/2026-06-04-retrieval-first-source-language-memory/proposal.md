## Why

memassist is a memory assistant, but current behavior can turn remembered user instructions into path-based policy warnings or blocks. This weakens the product model: user memories should be stored, retrieved, and injected for the current coding agent to judge, not compiled into autonomous enforcement.

The refresh-token case exposed the gap: the memory was stored in English, Korean retrieval missed it, and a rule-based `sensitive_paths` warning became the only effective behavior.

## What Changes

- **BREAKING** Remove memory-derived policy enforcement:
  - no automatic `sensitive_paths` or `protected_paths` writes from memories
  - no `warn_policy` or `block_policy` memory states in active behavior
  - no approval grants or session approval gates for memory-derived policy
- Preserve user memory in the source language as the primary stored content.
- Store optional meaning-preserving metadata for retrieval, but do not replace the user-language memory with translated policy prose.
- Make `UserPromptSubmit` the RAG moment:
  - ingest eligible user memories
  - retrieve semantically relevant memories
  - inject them as contextual memory through `additionalContext`
- Remove `Policy reminders` from memory RAG output and use memory-oriented context only.
- Keep explicit `policy.yaml` support only for manually configured project policy, not memory-derived behavior.
- Update documentation to describe memassist as retrieval-first memory infrastructure, not an autonomous policy gate.
- Add tests for Korean source-language storage, multilingual/typo retrieval, absence of policy mutation, and no memory-derived pre-tool blocking/warning.

## Capabilities

### New Capabilities

- `retrieval-first-memory`: Source-language memory storage, semantic retrieval, and contextual RAG injection without autonomous policy compilation.

### Modified Capabilities

- `memory-derived-enforcement`: Remove memory-derived enforcement requirements and limit pre-tool policy checks to explicit manually configured policy.
- `isolated-memory-judgment`: Require source-language memory preservation and remove policy-like activation/compilation behavior.

## Impact

- Affected modules:
  - `src/memassist/cli.py`
  - `src/memassist/retrieval.py`
  - `src/memassist/memory_judge.py`
  - `src/memassist/directives.py`
  - `src/memassist/interpreter.py`
  - `src/memassist/lifecycle.py`
  - `src/memassist/storage.py`
  - `src/memassist/policy.py`
  - README and OpenSpec specs
- Affected tests:
  - memory ingestion tests
  - RAG retrieval tests
  - hook E2E tests
  - policy/pre-tool tests
- No migration is required. Existing memory DB compatibility is not a goal for this change.
