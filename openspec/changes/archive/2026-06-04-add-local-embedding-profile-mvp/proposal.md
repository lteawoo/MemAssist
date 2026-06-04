## Why

memassist now has a hybrid retrieval engine, but vector participation still depends on a single environment-selected provider and cannot be evaluated across real local models. To make hybrid retrieval a product invariant rather than a wiring detail, memassist needs a lightweight local embedding profile system that can test quantized/local providers and choose a default by evidence.

## What Changes

- Add project-local embedding profiles that separate provider, model, quantization, dimension, text prefixes, normalization, and cache identity.
- Make hybrid retrieval always attempt an active vector profile unless vector retrieval is explicitly disabled.
- Replace silent vector absence with explicit diagnostics such as `ok`, `disabled`, `missing_dependency`, `missing_cache`, or `stale_cache`.
- Add a real local default candidate profile for quantized/local embeddings instead of using a hash pseudo-vector as a product fallback.
- Support profile-specific embedding cache rows so multiple provider/model candidates can coexist during evaluation.
- Add CLI flows to list profiles, build profile caches, switch the active profile, and compare retrieval evaluation results across profiles.
- Keep stored memory as retrieval context only; embedding profiles and retrieval diagnostics SHALL NOT create hidden approval gates, tool blocks, or permission decisions.

## Capabilities

### New Capabilities
- `embedding-profile-management`: Project-local profile configuration and provider registry for local embedding providers, models, quantization settings, cache identity, and active profile selection.
- `embedding-evaluation-matrix`: Profile-aware retrieval and RAG evaluation workflows that compare recall, exact-code preservation, policy leak avoidance, latency, and cache size across candidate local models.

### Modified Capabilities
- `retrieval-first-memory`: Hybrid retrieval must always run through one default engine and must explicitly report vector participation or degradation for the active embedding profile.
- `memory-index-boundary`: Embedding cache identity must include profile metadata so caches for different local providers/models do not collide and remain rebuildable derived state.
- `project-local-storage`: Project initialization must prepare local embedding profile configuration and keep model/cache artifacts project-local or user-cache-local without making external services required.

## Impact

- Affected modules: `src/memassist/embeddings.py`, `src/memassist/retrieval.py`, `src/memassist/storage.py`, `src/memassist/cli.py`, `src/memassist/rag_eval.py`, retrieval eval helpers, and README.
- Affected data: `.memassist/embedding-profiles.yaml`, SQLite `memory_embeddings` schema, embedding cache metadata, retrieval diagnostics JSON, and eval result artifacts.
- Affected commands: `memassist init`, `memassist memory rebuild-index`, `memassist memory pack`, retrieval/RAG eval commands, and new embedding profile management commands.
- Dependencies: the lightweight default local embedding runtime is installed with memassist so `local-default` can run after install; heavier comparison providers remain Python extras and lazy imports. MVP must support at least one real local quantized/default candidate path and must degrade explicitly when dependencies or model artifacts are unavailable.
