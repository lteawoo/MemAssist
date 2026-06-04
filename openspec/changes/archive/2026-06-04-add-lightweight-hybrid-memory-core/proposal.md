## Why

memassist currently stores authoritative Markdown memories, but prompt-derived source evidence, SQLite index ownership, and semantic retrieval are not yet cleanly separated into a lightweight memory core. This change keeps memassist small while making memory management auditable, rebuildable, and effective for both exact code terms and semantic prompts.

## What Changes

- Add a lightweight project-local source ledger that records raw memory source evidence separately from curated memory artifacts.
- Make Markdown memory artifacts the only authoritative memory content and provenance store, with SQLite treated as a derived index/cache.
- Add content-hash based indexing so Markdown edits can be detected and reindexed deterministically.
- Introduce default hybrid retrieval as one retrieval engine combining SQLite FTS with optional vector search when an embedding provider is configured.
- Keep vector search lightweight by embedding each memory artifact as a single note initially, with FTS-only fallback when embeddings are unavailable.
- Preserve memassist's retrieval-first behavior: stored memory is injected as context and never compiled into hidden approval gates, tool blocks, or permission decisions.

## Capabilities

### New Capabilities
- `memory-source-ledger`: Project-local immutable source evidence records for memory judgment and future re-extraction.

### Modified Capabilities
- `memory-index-boundary`: SQLite index rows and embedding rows become explicitly derived from Markdown artifacts and content hashes, not authoritative memory content.
- `retrieval-first-memory`: Prompt retrieval uses a default hybrid retrieval engine that merges exact FTS and optional vector results while injecting only active Markdown-backed memories.
- `isolated-memory-judgment`: Judge outputs are stored through the source-ledger-backed candidate flow and source evidence remains auditable.
- `project-local-storage`: Project initialization and local storage include the lightweight source ledger alongside Markdown memories and the derived SQLite index.

## Impact

- Affected modules: `src/memassist/storage.py`, `src/memassist/memory_artifacts.py`, `src/memassist/memory_judge.py`, `src/memassist/retrieval.py`, `src/memassist/hooks.py`, `src/memassist/cli.py`, `src/memassist/sync.py`.
- Affected data: `.memassist/memories/**`, new project-local source ledger file(s), SQLite index tables, FTS index, and optional embedding cache.
- Affected commands: `memassist init`, `memassist memory rebuild-index`, `memassist memory search`, `memassist memory pack`, memory import/export, and hook-time `UserPromptSubmit`/`Stop`.
- Dependencies: no required external vector database. Embedding support is optional and must degrade to FTS-only retrieval when unavailable.
