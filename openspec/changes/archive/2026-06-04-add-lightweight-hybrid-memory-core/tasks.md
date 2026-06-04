## 1. Source Ledger

- [x] 1.1 Add a project-local source ledger module that appends compact JSONL records under the project `.memassist` home.
- [x] 1.2 Generate stable source ids, source hashes, timestamps, source kind, session id, source reference, and compact text or summary for each source record.
- [x] 1.3 Update Stop hook processing to write source ledger records before isolated memory judgment.
- [x] 1.4 Link judged memory candidates and stored memory artifacts to source ids when source ledger evidence exists.
- [x] 1.5 Add tests proving project-scoped hooks write source records to the project `.memassist` home, not a parent or global home.

## 2. Markdown Artifact Authority

- [x] 2.1 Add content hash and source id metadata to newly written Markdown memory artifacts while preserving compatibility with existing artifacts.
- [x] 2.2 Ensure source quote, source reference, and source ids remain preserved when artifacts move between active, candidates, and archived buckets.
- [x] 2.3 Update memory import/export to preserve source evidence fields instead of dropping `source_quote` or source ids.
- [x] 2.4 Add tests for manual Markdown edits followed by `memory rebuild-index`.

## 3. Derived SQLite Index

- [x] 3.1 Introduce or adapt SQLite index metadata so memory index rows include artifact path, status, type, project id, content hash, and indexed timestamp as derived state.
- [x] 3.2 Update index rebuild to refresh rows whose content hash changed and prune rows whose Markdown artifact no longer exists.
- [x] 3.3 Ensure management commands list, update, activate, archive, export, and cleanup memories from Markdown artifacts rather than SQLite-only rows.
- [x] 3.4 Keep retrieval telemetry in SQLite as non-authoritative state that can reset on rebuild without losing memory content or evidence.
- [x] 3.5 Delete superseded SQLite-owned memory content paths, legacy lifecycle translation, and compatibility-only runtime branches after the Markdown-authoritative index path is implemented.
- [x] 3.6 Add tests or static checks proving stale SQLite-only rows and legacy statuses cannot drive managed memory behavior.

## 4. Optional Embedding Cache

- [x] 4.1 Add an embedding provider abstraction with an unavailable/no-op default.
- [x] 4.2 Add a project-local embedding cache keyed by memory id, content hash, provider, model, and dimension.
- [x] 4.3 Implement embedding cache invalidation when artifact content hash changes.
- [x] 4.4 Ensure missing or failed embeddings never prevent FTS retrieval, project initialization, or memory management.
- [x] 4.5 Add tests for stale embedding rows being ignored after memory content changes.

## 5. Hybrid Retrieval

- [x] 5.1 Refactor retrieval so the default engine collects FTS candidates, optional vector candidates, metadata/path/link candidates, and fuses them into one ranked pack.
- [x] 5.2 Preserve exact recall for paths, commands, symbols, tags, and error strings when vector candidates are available.
- [x] 5.3 Ensure hybrid retrieval injects only active Markdown-backed memories and ignores candidate, archived, stale SQLite-only, or stale vector-only hits.
- [x] 5.4 Add JSON retrieval diagnostics showing which retrieval signals contributed to memory pack results.
- [x] 5.5 Add multilingual/paraphrase retrieval eval cases proving vector-enabled hybrid improves semantic recall while FTS-only fallback still passes exact-match cases.

## 6. Lifecycle And Judgment Integration

- [x] 6.1 Build isolated judge payloads from source-ledger-backed evidence when available.
- [x] 6.2 Store judge accept/reject outcomes as lifecycle or event records with source ids and source references.
- [x] 6.3 Keep judge activation hints non-authoritative; deterministic lifecycle checks still decide active, candidate, or archived status.
- [x] 6.4 Add tests proving judged memories remain retrieval context and do not mutate verification config or create PreToolUse warnings or blocks.

## 7. Verification

- [x] 7.1 Run focused unit tests for source ledger, artifact authority, index rebuild, embedding cache, hybrid retrieval, and isolated judgment.
- [x] 7.2 Run existing memory, retrieval, RAG, and E2E tests with project-local `MEMASSIST_HOME`.
- [x] 7.3 Add or update OpenSpec-aligned eval fixtures for source evidence preservation, hybrid retrieval fallback, vector cache invalidation, and Markdown-authoritative management.
- [x] 7.4 Audit the final diff for migration residue, dormant compatibility branches, legacy status behavior, and SQLite-owned memory content fallbacks; delete any such code before marking the change complete.
- [x] 7.5 Document the lightweight memory core behavior in README without presenting memory as an enforcement or policy engine.
