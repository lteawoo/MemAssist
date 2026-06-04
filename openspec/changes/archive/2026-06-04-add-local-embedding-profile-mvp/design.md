## Context

memassist is a project-local memory RAG system. The current lightweight hybrid core makes Markdown artifacts authoritative and lets SQLite store derived FTS, telemetry, and optional vector cache rows. That core is not yet enough for selecting a real local embedding model because provider/model settings are not first-class, vector participation can disappear silently, and evaluation cannot compare quantized local candidates under the same retrieval contract.

The next MVP should keep the product lightweight and local-first while making hybrid retrieval measurable. The active coding agent remains responsible for interpreting retrieved memory context; embedding profiles and diagnostics are retrieval infrastructure, not enforcement controls.

## Goals / Non-Goals

**Goals:**
- Make the default retrieval path a stable hybrid engine that always attempts the active vector profile unless explicitly disabled.
- Introduce project-local embedding profiles for provider, model, quantization, dimension, text prefixes, normalization, and runtime settings.
- Support profile-specific vector caches so multiple local model candidates can be built and compared without cache collisions.
- Add a provider registry with lazy imports so provider packages are not imported during init and heavier optional local dependencies do not break FTS retrieval.
- Add profile-aware eval and compare commands for recall, exact-code preservation, forbidden leak avoidance, latency, and cache size.
- Remove the hash pseudo-vector from product candidate comparisons; test doubles may stay test-only.

**Non-Goals:**
- No external API embedding provider in this change.
- No external vector database.
- No autonomous policy compilation, hidden approval gate, or tool blocking from memory retrieval.
- No ColBERT multi-vector schema in the MVP, even if BGE-M3 is evaluated dense-only.
- No transcript/source-ledger embedding by default; embeddings are for curated memory artifacts.

## Decisions

### Decision: Profiles are the unit of embedding configuration

Embedding configuration will move from a single provider environment variable to `.memassist/embedding-profiles.yaml`. Each profile defines `provider`, `model`, `quantization`, `dimension`, `normalize`, optional query/document prefixes, device/runtime hints, and cache settings. One profile is active for normal retrieval, while build/eval commands can target any named profile.

Alternatives considered:
- Environment-only selection: too weak for repeatable model comparison and profile-specific cache identity.
- One global user config: conflicts with project-local memory behavior and makes project evals harder to reproduce.

### Decision: Hybrid retrieval records vector degradation explicitly

The retrieval engine continues to fuse lexical, vector, metadata, verifier, and link/path signals. It must attempt the active vector profile unless `provider: none` or a disable flag is set. If vector retrieval cannot participate, diagnostics must say why instead of making FTS-only look like full hybrid.

Alternatives considered:
- Fail prompt retrieval when vector is unavailable: too brittle for hooks and project initialization.
- Silent FTS-only fallback: hides the main product invariant and makes model selection unverifiable.

### Decision: Real local providers only in product profiles

The product profile registry should include real local embedding providers such as `model2vec`, `sentence-transformers`, and later ONNX/FlagEmbedding runtimes. Hash pseudo-vectors should not be a product fallback or evaluation candidate because they do not measure semantic retrieval quality. Tests can use an in-test fake provider.

Alternatives considered:
- Keep hash as fallback: deterministic but misleading for semantic evaluation.
- Make every embedding provider a default dependency: simpler profile switching, but too heavy for ordinary office PCs and unnecessary before comparison evals.

### Decision: MVP default candidate is quantized local, not final default by assertion

The MVP should seed a `local-default` profile around a quantized/local candidate such as `model2vec + minishlab/potion-multilingual-128M` and install the lightweight default runtime with memassist, but it should not claim final superiority until evals compare it against stronger local candidates. `google/embeddinggemma-300m` QAT/Q4, `Qwen3-Embedding-0.6B` quantized, and `BAAI/bge-m3` dense-only can be configured as comparison profiles when their runtimes are installed.

Alternatives considered:
- Default directly to BGE-M3: strong quality baseline, but heavy for ordinary office PCs and oversized for short Markdown notes.
- Default directly to EmbeddingGemma/Qwen3: promising, but runtime and quantized artifact stability need project evals first.

### Decision: Cache identity includes profile fingerprint

Embedding cache rows will include `profile_id` and a stable `profile_fingerprint` derived from provider, model, quantization, dimension, text formatting, and normalization settings. This lets multiple profiles coexist for the same memory and prevents stale rows from one model influencing another profile's ranking.

Alternatives considered:
- Key by provider/model/dimension only: insufficient when quantization, prefixes, normalization, or runtime settings change.
- Delete all caches on profile switch: simpler, but makes model comparison slow and hard to reproduce.

## Risks / Trade-offs

- [Risk] Optional provider dependencies can fail at runtime. -> Mitigation: use lazy imports, explicit diagnostics, and keep lexical retrieval operational.
- [Risk] Profile YAML can become too complex. -> Mitigation: seed a small default schema and validate unknown/invalid fields with clear errors.
- [Risk] Stronger models may improve semantic recall while hurting exact-code recall. -> Mitigation: preserve lexical/path channels and require exact-code eval cases in every compare run.
- [Risk] Quantized artifacts differ by runtime or community source. -> Mitigation: record provider, model, quantization, revision if available, and profile fingerprint in eval output.
- [Risk] Cache size can grow with multiple profiles. -> Mitigation: add cleanup by profile and report cache size in eval comparison.

## Migration Plan

1. Add embedding profile loading, validation, and initialization without changing existing retrieval behavior.
2. Replace environment-only provider selection with profile-based provider registry and explicit vector diagnostics.
3. Extend embedding cache schema with profile id and fingerprint while preserving rebuildability from Markdown artifacts.
4. Add profile build, list, activate, and cleanup CLI commands.
5. Add profile-aware retrieval/RAG eval and compare output.
6. Seed real local profile candidates and remove hash from product candidate flows.
7. Update README and OpenSpec docs to state that hybrid retrieval is mandatory while vector degradation is explicit.

Rollback: set the active profile to `none` or remove profile cache rows and run `memassist memory rebuild-index`. Markdown memories remain authoritative and are not deleted by embedding profile changes.

## Open Questions

- Which quantized local profile should become the shipped default after MVP eval: `model2vec+potion`, EmbeddingGemma QAT/Q4, Qwen3-Embedding quantized, or BGE-M3 dense-only?
- Should model downloads live in a project-local cache, a user cache, or follow the provider runtime default while only derived vector rows remain project-local?
- Should profile compare persist historical eval results under `.memassist/evals/` or only print JSON for callers to store?
