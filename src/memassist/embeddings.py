from __future__ import annotations

import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol

from .chunks import chunk_embedding_text
from .embedding_profiles import EmbeddingProfile, get_embedding_profile
from .models import Memory, MemoryChunk
from .storage import Store


class EmbeddingProviderError(RuntimeError):
    status = "provider_error"


class EmbeddingDisabledError(EmbeddingProviderError):
    status = "disabled"


class EmbeddingMissingDependencyError(EmbeddingProviderError):
    status = "missing_dependency"


class EmbeddingMissingModelError(EmbeddingProviderError):
    status = "missing_model"


class EmbeddingEncodeError(EmbeddingProviderError):
    status = "encode_error"


class EmbeddingDimensionError(EmbeddingProviderError):
    status = "incompatible_dimension"


@dataclass(frozen=True)
class EmbeddingResult:
    profile_id: str
    profile_fingerprint: str
    provider: str
    model: str
    quantization: str
    dimension: int
    vector: list[float]


@dataclass(frozen=True)
class EmbeddingModelInstallResult:
    profile_id: str
    status: str
    path: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "status": self.status,
            "path": self.path,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class VectorSearchResult:
    memories: list[Memory]
    status: str
    profile: EmbeddingProfile
    row_count: int = 0
    stale_count: int = 0
    error: str | None = None

    def diagnostics(self) -> dict[str, object]:
        return {
            "profile_id": self.profile.id,
            "profile_fingerprint": self.profile.fingerprint,
            "provider": self.profile.provider,
            "model": self.profile.model,
            "quantization": self.profile.quantization,
            "dimension": self.profile.dimension,
            "status": self.status,
            "row_count": self.row_count,
            "stale_count": self.stale_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class ChunkVectorSearchResult:
    chunks: list[MemoryChunk]
    status: str
    profile: EmbeddingProfile
    row_count: int = 0
    stale_count: int = 0
    error: str | None = None

    def diagnostics(self) -> dict[str, object]:
        return {
            "profile_id": self.profile.id,
            "profile_fingerprint": self.profile.fingerprint,
            "provider": self.profile.provider,
            "model": self.profile.model,
            "quantization": self.profile.quantization,
            "dimension": self.profile.dimension,
            "status": self.status,
            "row_count": self.row_count,
            "stale_count": self.stale_count,
            "error": self.error,
        }


class EmbeddingProvider(Protocol):
    profile: EmbeddingProfile
    dimension: int

    def embed(self, text: str) -> list[float]:
        ...


ProviderFactory = Callable[[EmbeddingProfile], EmbeddingProvider]
_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_embedding_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDER_FACTORIES[name] = factory


def provider_names() -> list[str]:
    return sorted(name for name in _PROVIDER_FACTORIES if name not in {"test"})


def selected_embedding_profile(profile_id: str | None = None, *, store: Store | None = None) -> EmbeddingProfile:
    mem_dir = store.path.parent if store else None
    return get_embedding_profile(profile_id, mem_dir=mem_dir)


def instantiate_embedding_provider(
    profile: EmbeddingProfile,
    *,
    mem_dir: Path | None = None,
    local_only: bool = True,
) -> EmbeddingProvider:
    if profile.disabled:
        raise EmbeddingDisabledError("vector retrieval is disabled for the active embedding profile")
    factory = _PROVIDER_FACTORIES.get(profile.provider)
    if not factory:
        raise EmbeddingMissingDependencyError(f"embedding provider is not registered: {profile.provider}")
    return factory(_runtime_profile(profile, mem_dir=mem_dir, local_only=local_only))


def embedding_model_install_path(profile: EmbeddingProfile, *, mem_dir: Path) -> Path | None:
    if profile.disabled:
        return None
    if profile.cache_dir:
        path = Path(profile.cache_dir).expanduser()
        return path if path.is_absolute() else mem_dir / path
    if profile.provider in {"model2vec", "sentence-transformers", "flagembedding"} and _looks_like_remote_model_id(profile.model):
        return mem_dir / "models" / _safe_path_component(profile.id)
    return None


def embedding_model_status(profile: EmbeddingProfile, *, mem_dir: Path) -> EmbeddingModelInstallResult:
    if profile.disabled:
        return EmbeddingModelInstallResult(profile.id, "disabled", detail="vector retrieval is disabled for this profile")
    if profile.provider not in _PROVIDER_FACTORIES:
        return EmbeddingModelInstallResult(profile.id, "missing_dependency", detail=f"embedding provider is not registered: {profile.provider}")
    installed_model = _installed_model_path(profile, mem_dir=mem_dir)
    if installed_model:
        return _validate_installed_profile(profile, mem_dir=mem_dir)
    install_path = embedding_model_install_path(profile, mem_dir=mem_dir)
    if install_path:
        return EmbeddingModelInstallResult(profile.id, "missing_model", path=str(install_path), detail="embedding model is not installed")
    return EmbeddingModelInstallResult(profile.id, "unknown", detail="embedding model installation path cannot be inferred")


def install_embedding_model(
    profile: EmbeddingProfile,
    *,
    mem_dir: Path,
    force: bool = False,
) -> EmbeddingModelInstallResult:
    if profile.disabled:
        return EmbeddingModelInstallResult(profile.id, "disabled", detail="vector retrieval is disabled for this profile")
    if profile.provider not in _PROVIDER_FACTORIES:
        return EmbeddingModelInstallResult(profile.id, "missing_dependency", detail=f"embedding provider is not registered: {profile.provider}")
    if profile.provider == "model2vec":
        return _install_model2vec_model(profile, mem_dir=mem_dir, force=force)
    return EmbeddingModelInstallResult(profile.id, "unsupported", detail=f"embedding install is not supported for provider: {profile.provider}")


def memory_embedding_text(memory: Memory) -> str:
    return " ".join(
        part
        for part in [
            memory.content,
            " ".join(memory.tags),
            " ".join(memory.paths),
        ]
        if part
    )


def ensure_memory_embedding(
    store: Store,
    memory: Memory,
    profile: EmbeddingProfile | None = None,
) -> EmbeddingResult | None:
    profile = profile or selected_embedding_profile(store=store)
    if profile.disabled:
        return None
    try:
        provider = instantiate_embedding_provider(profile, mem_dir=store.path.parent, local_only=True)
        vector = provider.embed(profile.document_text(memory_embedding_text(memory)))
    except EmbeddingProviderError:
        return None
    except Exception:
        return None
    if not vector:
        return None
    dimension = len(vector)
    if profile.dimension is not None and profile.dimension > 0 and profile.dimension != dimension:
        return None
    store.upsert_memory_embedding(
        memory,
        profile=profile,
        embedding=vector,
    )
    return EmbeddingResult(
        profile_id=profile.id,
        profile_fingerprint=profile.fingerprint,
        provider=profile.provider,
        model=profile.model,
        quantization=profile.quantization,
        dimension=dimension,
        vector=vector,
    )


def ensure_memory_chunk_embedding(
    store: Store,
    chunk: MemoryChunk,
    profile: EmbeddingProfile | None = None,
) -> EmbeddingResult | None:
    profile = profile or selected_embedding_profile(store=store)
    if profile.disabled:
        return None
    try:
        provider = instantiate_embedding_provider(profile, mem_dir=store.path.parent, local_only=True)
        vector = provider.embed(profile.document_text(chunk_embedding_text(chunk)))
    except EmbeddingProviderError:
        return None
    except Exception:
        return None
    if not vector:
        return None
    dimension = len(vector)
    if profile.dimension is not None and profile.dimension > 0 and profile.dimension != dimension:
        return None
    store.upsert_memory_chunk_embedding(
        chunk,
        profile=profile,
        embedding=vector,
    )
    return EmbeddingResult(
        profile_id=profile.id,
        profile_fingerprint=profile.fingerprint,
        provider=profile.provider,
        model=profile.model,
        quantization=profile.quantization,
        dimension=dimension,
        vector=vector,
    )


def build_memory_embeddings(
    store: Store,
    *,
    project_id: str,
    profile: EmbeddingProfile,
) -> dict[str, object]:
    built: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    try:
        instantiate_embedding_provider(profile, mem_dir=store.path.parent, local_only=True)
    except EmbeddingProviderError as exc:
        return {
            "profile_id": profile.id,
            "status": exc.status,
            "built": built,
            "skipped": skipped,
            "errors": [{"profile_id": profile.id, "error": str(exc)}],
        }
    for memory in store.list_memories(project_id=project_id, include_global=True, status="active"):
        result = ensure_memory_embedding(store, memory, profile=profile)
        if result:
            built.append(memory.id)
        else:
            skipped.append(memory.id)
            errors.append({"memory_id": memory.id, "error": "embedding_not_created"})
    chunk_built: list[str] = []
    chunk_skipped: list[str] = []
    for chunk in store.list_memory_chunks(project_id=project_id, include_global=True, status="active"):
        result = ensure_memory_chunk_embedding(store, chunk, profile=profile)
        if result:
            chunk_built.append(chunk.id)
        else:
            chunk_skipped.append(chunk.id)
            errors.append({"chunk_id": chunk.id, "error": "chunk_embedding_not_created"})
    return {
        "profile_id": profile.id,
        "status": "ok" if not errors else "partial",
        "built": built,
        "skipped": skipped,
        "chunk_built": chunk_built,
        "chunk_skipped": chunk_skipped,
        "errors": errors,
    }


def vector_search(
    store: Store,
    *,
    query: str,
    project_id: str,
    profile: EmbeddingProfile | None = None,
    limit: int = 10,
) -> VectorSearchResult:
    profile = profile or selected_embedding_profile(store=store)
    if profile.disabled:
        return VectorSearchResult([], "disabled", profile)
    try:
        provider = instantiate_embedding_provider(profile, mem_dir=store.path.parent, local_only=True)
        query_vector = provider.embed(profile.query_text(query))
    except EmbeddingProviderError as exc:
        return VectorSearchResult([], exc.status, profile, error=str(exc))
    except Exception as exc:
        return VectorSearchResult([], "encode_error", profile, error=str(exc))
    if not query_vector:
        return VectorSearchResult([], "encode_error", profile, error="query embedding is empty")
    dimension = len(query_vector)
    if profile.dimension is not None and profile.dimension > 0 and profile.dimension != dimension:
        return VectorSearchResult([], "incompatible_dimension", profile, error=f"expected {profile.dimension}, got {dimension}")
    rows, stats = store.memory_embedding_rows_for_profile(
        project_id=project_id,
        profile=profile,
        dimension=dimension,
    )
    if not rows:
        status = "stale_cache" if stats.get("stale_count", 0) else "missing_cache"
        return VectorSearchResult([], status, profile, row_count=int(stats.get("row_count", 0)), stale_count=int(stats.get("stale_count", 0)))
    scored: list[tuple[float, Memory]] = []
    for row in rows:
        score = cosine_similarity(query_vector, row["embedding"])
        if score > 0:
            scored.append((score, row["memory"]))
    memories = [memory for _score, memory in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]
    return VectorSearchResult(
        memories,
        "ok",
        profile,
        row_count=int(stats.get("row_count", len(rows))),
        stale_count=int(stats.get("stale_count", 0)),
    )


def vector_search_chunks(
    store: Store,
    *,
    query: str,
    project_id: str,
    profile: EmbeddingProfile | None = None,
    limit: int = 10,
    include_raw: bool = False,
) -> ChunkVectorSearchResult:
    profile = profile or selected_embedding_profile(store=store)
    if profile.disabled:
        return ChunkVectorSearchResult([], "disabled", profile)
    try:
        provider = instantiate_embedding_provider(profile, mem_dir=store.path.parent, local_only=True)
        query_vector = provider.embed(profile.query_text(query))
    except EmbeddingProviderError as exc:
        return ChunkVectorSearchResult([], exc.status, profile, error=str(exc))
    except Exception as exc:
        return ChunkVectorSearchResult([], "encode_error", profile, error=str(exc))
    if not query_vector:
        return ChunkVectorSearchResult([], "encode_error", profile, error="query embedding is empty")
    dimension = len(query_vector)
    if profile.dimension is not None and profile.dimension > 0 and profile.dimension != dimension:
        return ChunkVectorSearchResult([], "incompatible_dimension", profile, error=f"expected {profile.dimension}, got {dimension}")
    rows, stats = store.memory_chunk_embedding_rows_for_profile(
        project_id=project_id,
        profile=profile,
        dimension=dimension,
        include_raw=include_raw,
    )
    if not rows:
        status = "stale_cache" if stats.get("stale_count", 0) else "missing_cache"
        return ChunkVectorSearchResult([], status, profile, row_count=int(stats.get("row_count", 0)), stale_count=int(stats.get("stale_count", 0)))
    scored: list[tuple[float, MemoryChunk]] = []
    for row in rows:
        score = cosine_similarity(query_vector, row["embedding"])
        if score > 0:
            scored.append((score, row["chunk"]))
    chunks = [chunk for _score, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]
    return ChunkVectorSearchResult(
        chunks,
        "ok",
        profile,
        row_count=int(stats.get("row_count", len(rows))),
        stale_count=int(stats.get("stale_count", 0)),
    )


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class Model2VecEmbeddingProvider:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        try:
            from model2vec import StaticModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EmbeddingMissingDependencyError("model2vec is not installed") from exc
        try:
            self.model = StaticModel.from_pretrained(
                profile.model,
                normalize=profile.normalize,
                quantize_to=_quantization_arg(profile),
                dimensionality=_dimension_arg(profile),
                force_download=False,
            )
        except Exception as exc:
            raise EmbeddingMissingModelError(str(exc)) from exc
        self.dimension = int(profile.dimension or getattr(self.model, "dim", 0) or 0)

    def embed(self, text: str) -> list[float]:
        try:
            result = self.model.encode([text])
        except Exception as exc:
            raise EmbeddingEncodeError(str(exc)) from exc
        vector = result[0]
        return _to_float_list(vector, normalize=self.profile.normalize)


class SentenceTransformersEmbeddingProvider:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EmbeddingMissingDependencyError("sentence-transformers is not installed") from exc
        try:
            self.model = SentenceTransformer(profile.model)
        except Exception as exc:
            raise EmbeddingMissingModelError(str(exc)) from exc
        self.dimension = int(profile.dimension or 0)

    def embed(self, text: str) -> list[float]:
        try:
            vector = self.model.encode(text, normalize_embeddings=self.profile.normalize)
        except Exception as exc:
            raise EmbeddingEncodeError(str(exc)) from exc
        return _to_float_list(vector, normalize=False)


class FlagEmbeddingProvider:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile
        try:
            from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-not-found]
        except ImportError as exc:
            raise EmbeddingMissingDependencyError("FlagEmbedding is not installed") from exc
        try:
            self.model = BGEM3FlagModel(profile.model, use_fp16=False)
        except Exception as exc:
            raise EmbeddingMissingModelError(str(exc)) from exc
        self.dimension = int(profile.dimension or 0)

    def embed(self, text: str) -> list[float]:
        try:
            output = self.model.encode([text], return_dense=True, return_sparse=False, return_colbert_vecs=False)
            vector = output["dense_vecs"][0]
        except Exception as exc:
            raise EmbeddingEncodeError(str(exc)) from exc
        return _to_float_list(vector, normalize=self.profile.normalize)


def _to_float_list(value: object, *, normalize: bool) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        raise EmbeddingEncodeError("embedding provider returned a non-list vector")
    vector = [float(item) for item in value]
    return _normalize(vector) if normalize else vector


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [round(value / norm, 8) for value in vector]


def _install_model2vec_model(profile: EmbeddingProfile, *, mem_dir: Path, force: bool) -> EmbeddingModelInstallResult:
    try:
        from model2vec import StaticModel  # type: ignore[import-not-found]
    except ImportError as exc:
        return EmbeddingModelInstallResult(profile.id, "missing_dependency", detail=str(exc))
    local_model = _existing_local_model_path(profile, mem_dir=mem_dir)
    if local_model:
        try:
            StaticModel.from_pretrained(
                local_model,
                normalize=profile.normalize,
                quantize_to=_quantization_arg(profile),
                dimensionality=_dimension_arg(profile),
                force_download=False,
            )
        except Exception as exc:
            return EmbeddingModelInstallResult(profile.id, "missing_model", path=str(local_model), detail=str(exc))
        return EmbeddingModelInstallResult(profile.id, "ok", path=str(local_model), detail="embedding model is available locally")
    install_path = embedding_model_install_path(profile, mem_dir=mem_dir)
    if not install_path:
        return EmbeddingModelInstallResult(profile.id, "missing_model", detail="embedding model has no local install path")
    if _path_has_files(install_path) and not force:
        validation = _validate_installed_profile(profile, mem_dir=mem_dir)
        if validation.status == "ok":
            return validation
    if not _looks_like_remote_model_id(profile.model):
        return EmbeddingModelInstallResult(profile.id, "missing_model", path=str(install_path), detail=f"local model path does not exist: {profile.model}")
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
    except ImportError as exc:
        return EmbeddingModelInstallResult(profile.id, "missing_dependency", detail=str(exc))
    try:
        install_path.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=profile.model,
            repo_type="model",
            revision=profile.revision,
            local_dir=str(install_path),
            force_download=force,
        )
    except Exception as exc:
        return EmbeddingModelInstallResult(profile.id, "install_error", path=str(install_path), detail=str(exc))
    return _validate_installed_profile(profile, mem_dir=mem_dir)


def _validate_installed_profile(profile: EmbeddingProfile, *, mem_dir: Path) -> EmbeddingModelInstallResult:
    path = _installed_model_path(profile, mem_dir=mem_dir) or embedding_model_install_path(profile, mem_dir=mem_dir)
    try:
        instantiate_embedding_provider(profile, mem_dir=mem_dir, local_only=True)
    except EmbeddingProviderError as exc:
        return EmbeddingModelInstallResult(profile.id, exc.status, path=str(path) if path else None, detail=str(exc))
    except Exception as exc:
        return EmbeddingModelInstallResult(profile.id, "install_error", path=str(path) if path else None, detail=str(exc))
    return EmbeddingModelInstallResult(profile.id, "ok", path=str(path) if path else None, detail="embedding model is installed and loadable")


def _runtime_profile(profile: EmbeddingProfile, *, mem_dir: Path | None, local_only: bool) -> EmbeddingProfile:
    if profile.provider not in {"model2vec", "sentence-transformers", "flagembedding"}:
        return profile
    if mem_dir:
        installed_model = _installed_model_path(profile, mem_dir=mem_dir)
        if installed_model:
            return replace(profile, model=str(installed_model))
        if local_only:
            raise EmbeddingMissingModelError(
                f"embedding model is not installed for profile {profile.id}; run `memassist embedding install --profile {profile.id}`"
            )
    if local_only and _looks_like_remote_model_id(profile.model):
        raise EmbeddingMissingModelError(
            f"embedding model is not installed for profile {profile.id}; run `memassist embedding install --profile {profile.id}`"
        )
    return profile


def _installed_model_path(profile: EmbeddingProfile, *, mem_dir: Path) -> Path | None:
    local_model = _existing_local_model_path(profile, mem_dir=mem_dir)
    if local_model:
        return local_model
    install_path = embedding_model_install_path(profile, mem_dir=mem_dir)
    if install_path and _path_has_files(install_path):
        return install_path
    return None


def _existing_local_model_path(profile: EmbeddingProfile, *, mem_dir: Path) -> Path | None:
    raw = Path(profile.model).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.append(mem_dir / raw)
    for candidate in candidates:
        if _path_has_files(candidate):
            return candidate
    return None


def _path_has_files(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    except OSError:
        return False
    return True


def _looks_like_remote_model_id(model: str) -> bool:
    return model.count("/") == 1 and not model.startswith((".", "/", "~"))


def _safe_path_component(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    return safe or "default"


def _quantization_arg(profile: EmbeddingProfile) -> str | None:
    return None if profile.quantization in {"", "none"} else profile.quantization


def _dimension_arg(profile: EmbeddingProfile) -> int | None:
    return profile.dimension if profile.dimension and profile.dimension > 0 else None


register_embedding_provider("model2vec", Model2VecEmbeddingProvider)
register_embedding_provider("sentence-transformers", SentenceTransformersEmbeddingProvider)
register_embedding_provider("flagembedding", FlagEmbeddingProvider)
