## 1. Profile Configuration

- [x] 1.1 Add an embedding profile model that captures profile id, provider, model, quantization, dimension, normalization, prefixes, runtime hints, and cache settings.
- [x] 1.2 Add `.memassist/embedding-profiles.yaml` path helpers and project-local load/save functions.
- [x] 1.3 Update `memassist init` to create or prepare project-local embedding profile configuration without adopting parent or user-global profile files.
- [x] 1.4 Seed a `none` profile for explicit vector disablement and at least one real local default candidate profile.
- [x] 1.5 Add validation errors for missing active profile, unknown providers, invalid dimensions, invalid quantization values, and malformed YAML.

## 2. Provider Registry

- [x] 2.1 Replace environment-only provider selection with a provider registry keyed by profile provider name.
- [x] 2.2 Implement lazy provider instantiation so optional dependencies are imported only when a profile is used.
- [x] 2.3 Add a `model2vec` provider adapter for local static embeddings when the dependency is installed.
- [x] 2.3.1 Add package dependency metadata so the default local embedding runtime is installed with memassist.
- [x] 2.4 Remove hash pseudo-vector from product profile candidates and provider comparison paths.
- [x] 2.5 Add test-only fake provider support without exposing it as a product profile.
- [x] 2.6 Add provider error types for disabled, missing dependency, missing model, encode failure, and incompatible dimension.

## 3. Profile-Aware Embedding Cache

- [x] 3.1 Extend the embedding cache schema with `profile_id`, `profile_fingerprint`, quantization, and profile metadata needed for cache identity.
- [x] 3.2 Generate a stable profile fingerprint from provider, model, quantization, dimension, normalization, prefixes, and runtime settings.
- [x] 3.3 Update embedding cache writes to include profile identity and fingerprint.
- [x] 3.4 Update embedding cache reads to require matching profile id, fingerprint, provider, model, dimension, memory id, and current content hash.
- [x] 3.5 Ensure cache rows for different profiles can coexist for the same Markdown memory artifact.
- [x] 3.6 Add profile cache cleanup that deletes derived rows for a selected profile without deleting Markdown memory artifacts.

## 4. Mandatory Hybrid Retrieval Diagnostics

- [x] 4.1 Update retrieval to load the active embedding profile and attempt vector retrieval unless the active profile explicitly disables it.
- [x] 4.2 Add vector diagnostics with status values such as `ok`, `disabled`, `missing_dependency`, `missing_cache`, `stale_cache`, and `encode_error`.
- [x] 4.3 Preserve lexical, metadata, verifier, path, and link retrieval when vector retrieval degrades.
- [x] 4.4 Include profile id, provider, model, dimension, and vector status in memory pack JSON diagnostics.
- [x] 4.5 Add tests proving profile diagnostics never produce PreToolUse warnings, blocks, or verification config mutations.

## 5. CLI Commands

- [x] 5.1 Add `memassist embedding profiles` to list profiles and show the active profile.
- [x] 5.2 Add `memassist embedding activate <profile>` to switch the active project-local profile.
- [x] 5.3 Add `memassist embedding build --profile <profile>` to build vector cache rows from active Markdown-backed memories.
- [x] 5.4 Add `memassist embedding cleanup --profile <profile>` to remove derived cache rows for one profile.
- [x] 5.5 Update `memassist memory rebuild-index` to rebuild embedding cache eligibility for the active or requested profile.

## 6. Evaluation Matrix

- [x] 6.1 Update retrieval eval to accept a profile id without permanently changing the active project profile.
- [x] 6.2 Update RAG eval to include profile identity, vector status, and channel contribution diagnostics.
- [x] 6.3 Add `memassist eval compare --profiles <ids>` or equivalent comparison output for multiple profiles.
- [x] 6.4 Report quality metrics for multilingual paraphrase recall, source quote recall, exact-code recall, and forbidden leak rate.
- [x] 6.5 Report operational metrics such as elapsed time, query latency when available, cache row count, and cache byte size when available.
- [x] 6.6 Add eval fixtures covering Korean-to-English, English-to-Korean, path/symbol/command exact recall, candidate exclusion, and policy leak avoidance.

## 7. Model Candidate Documentation

- [x] 7.1 Document that product candidates use real local embedding providers and that hash pseudo-vectors are not semantic comparison candidates.
- [x] 7.2 Document the initial quantized/local candidate set, including `model2vec + minishlab/potion-multilingual-128M`, EmbeddingGemma QAT/Q4, Qwen3-Embedding quantized, and BGE-M3 dense-only as configurable evaluation profiles.
- [x] 7.3 Document ordinary-PC expectations, missing dependency behavior, explicit vector disablement, and profile cache cleanup.
- [x] 7.4 Update README language so hybrid retrieval is mandatory while vector degradation is explicit and auditable.

## 8. Verification

- [x] 8.1 Add unit tests for profile config load/save, validation, fingerprint stability, and project-local initialization.
- [x] 8.2 Add unit tests for provider registry lazy imports and missing dependency diagnostics.
- [x] 8.3 Add unit tests for profile-specific cache coexistence, stale content hash rejection, stale fingerprint rejection, and cleanup.
- [x] 8.4 Add retrieval tests proving vector status diagnostics for ok, disabled, missing dependency, and missing cache.
- [x] 8.5 Run existing memory, retrieval, RAG, GUI, and hook tests to ensure memory remains context-only.
- [x] 8.6 Run OpenSpec validation for `add-local-embedding-profile-mvp`.
