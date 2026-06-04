## Context

memassist is a project-local memory RAG system. The current direction already treats Markdown memory artifacts as authoritative and SQLite as a rebuildable runtime index, but the content boundary is not yet as clean as the product identity requires. SQLite still mirrors memory content for search convenience, source evidence is mostly embedded in artifacts or lifecycle metadata, and retrieval is deterministic lexical/metadata ranking without a semantic channel.

The desired v2 core should stay lightweight: no external vector database, no always-on scheduler, no graph database, and no policy enforcement. The active coding agent remains responsible for interpreting retrieved memory context.

## Goals / Non-Goals

**Goals:**
- Keep Markdown artifacts as the canonical memory content, status, and provenance surface.
- Add a lightweight source ledger so prompt-derived memories can be audited and re-extracted later.
- Make SQLite an explicit derived index/cache with content hashes and rebuild behavior.
- Provide one default retrieval engine that combines FTS and optional vector search.
- Allow vector search without requiring it: retrieval must degrade to FTS-only when embeddings are unavailable.
- Preserve retrieval-first behavior and avoid hidden approval gates or tool blocking.
- Remove superseded memory storage, retrieval, and lifecycle branches instead of keeping migration residue or dead compatibility code.

**Non-Goals:**
- No Milvus, Chroma, Neo4j, Postgres, or other external storage requirement.
- No transcript-wide long-term memory by default.
- No automatic policy compilation from memory.
- No autonomous memory consolidation loop in this change.
- No chunk-level vector indexing for short memories in the first implementation.

## Decisions

### Decision: Markdown remains canonical

Memory content, source quotes, source references, status buckets, and human-editable metadata stay in Markdown artifacts under `.memassist/memories/{active,candidates,archived}`. SQLite rows are invalid if the corresponding artifact is missing or has a mismatched content hash.

Alternatives considered:
- SQLite canonical content: simpler query code, but creates drift, weakens human editability, and conflicts with the project identity.
- Dual canonical stores: rejected because reconciliation would become part of the product surface.

### Decision: Source ledger is a compact JSONL file

The first source ledger implementation uses `.memassist/sources.jsonl` with append-only records containing source id, kind, session id, source reference, source hash, compact text or summary, and timestamp. This avoids a larger DB schema while making source evidence durable and reprocessable.

Alternatives considered:
- Dedicated source tables: more queryable, but too heavy for the initial redesign.
- Store only source_quote in memory artifacts: too weak for future re-extraction and audit.

### Decision: SQLite has derived index tables only

SQLite stores artifact registry/index metadata, FTS text, optional embeddings, events, memory links, and retrieval telemetry. It may store derived index text for fast search, but not authoritative memory content. `rebuild-index` must reconstruct index state from Markdown artifacts and the source ledger where needed.

Alternatives considered:
- Remove SQLite content ownership entirely in this change: selected. Transitional schema changes may be used only as implementation steps, but final runtime behavior must not keep compatibility-only branches or SQLite-owned memory content.
- Keep current table shape: easiest, but it leaves the ownership boundary ambiguous.

### Decision: Hybrid retrieval is the default engine

The public retrieval behavior is a single engine. Internally it runs FTS top-N, vector top-N when an embedding provider and index are available, metadata/path boosts, and RRF-style fusion. If vector search is unavailable, the same engine runs FTS-only.

Alternatives considered:
- FTS-only default: too brittle for multilingual and paraphrased memory prompts.
- Vector-only default: weak for code paths, symbols, commands, error strings, and exact tags.

### Decision: One embedding per memory artifact initially

Each active or candidate memory artifact gets a single embedding based on its primary memory content plus compact metadata. Long source logs and section-level chunking stay out of scope until memories become too long for one-note embeddings.

Alternatives considered:
- Chunk every memory: unnecessary for short durable notes and adds schema and ranking complexity.
- Embed source ledger by default: higher recall, but risks turning raw episodes into prompt-time context too early.

### Decision: Events are initially unified

The lightweight schema can use one derived `events` table for lifecycle, retrieval usage, source processing, and indexing events. Event type and payload determine semantics. This keeps the first implementation simple while allowing later splitting if needed.

Alternatives considered:
- Separate lifecycle/source/usage tables from day one: clearer analytics, but heavier than necessary.

## Risks / Trade-offs

- [Risk] Optional embeddings create configuration and provider drift. → Mitigation: include provider, model, dimension, and content hash in embedding cache rows; invalidate on mismatch.
- [Risk] FTS-only fallback may look worse than full hybrid. → Mitigation: expose retrieval diagnostics so users can see whether vector search participated.
- [Risk] JSONL source ledger can grow over time. → Mitigation: keep records compact and support future pruning or archive rotation without changing memory artifacts.
- [Risk] Manual Markdown edits can leave stale index rows. → Mitigation: content hashes and `rebuild-index` reconcile stale rows; prompt retrieval ignores missing or inactive artifacts.
- [Risk] Hybrid ranking may over-inject semantically related but irrelevant memories. → Mitigation: cap results, require active Markdown artifacts, and keep existing section-aware pack limits.

## Migration Plan

1. Add source ledger writing at Stop without changing retrieval behavior.
2. Add content hash metadata to new and rewritten artifacts.
3. Add or adapt SQLite derived index rows to track artifact path, hash, index status, FTS text, optional embedding cache, and events.
4. Update `rebuild-index` to rebuild derived FTS and embedding eligibility from Markdown artifacts.
5. Implement hybrid retrieval with vector disabled by default until a provider is configured.
6. Update import/export to preserve source ids, source quotes, source refs, and content hash metadata.
7. Remove superseded SQLite-content ownership, legacy lifecycle translation, and compatibility-only runtime branches after the new artifact-authoritative path is in place.

Rollback: disable vector configuration and run `memassist memory rebuild-index` to return to FTS-only retrieval. Because Markdown artifacts remain canonical, deleting SQLite derived rows must not delete managed memories.

## Open Questions

- Which embedding provider should be supported first: OpenAI, Ollama, or a local ONNX/sentence-transformers path?
- Should `sources.jsonl` store full prompt text by default, or compact text capped similarly to the current judge input?
- Should imported memories without source ids create synthetic source records, or keep only import provenance?
