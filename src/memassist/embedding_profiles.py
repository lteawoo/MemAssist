from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .paths import memassist_home


PROFILE_FILE_NAME = "embedding-profiles.yaml"
KNOWN_PROVIDERS = {"none", "model2vec", "sentence-transformers", "flagembedding", "test"}
KNOWN_QUANTIZATIONS = {"none", "int8", "qint8", "uint8", "binary", "ubinary", "q4", "q4_0", "q8_0"}


class EmbeddingProfileError(ValueError):
    pass


@dataclass(frozen=True)
class EmbeddingProfile:
    id: str
    provider: str
    model: str
    quantization: str = "none"
    dimension: int | None = None
    normalize: bool = True
    query_prefix: str = ""
    document_prefix: str = ""
    device: str | None = None
    runtime: str | None = None
    revision: str | None = None
    cache_dir: str | None = None
    options: dict[str, object] = field(default_factory=dict)

    @property
    def disabled(self) -> bool:
        return self.provider == "none"

    @property
    def fingerprint(self) -> str:
        payload = {
            "provider": self.provider,
            "model": self.model,
            "quantization": self.quantization,
            "dimension": self.dimension,
            "normalize": self.normalize,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
            "device": self.device,
            "runtime": self.runtime,
            "revision": self.revision,
            "cache_dir": self.cache_dir,
            "options": self.options,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def document_text(self, text: str) -> str:
        return f"{self.document_prefix}{text}" if self.document_prefix else text

    def query_text(self, text: str) -> str:
        return f"{self.query_prefix}{text}" if self.query_prefix else text

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "quantization": self.quantization,
            "dimension": self.dimension,
            "normalize": self.normalize,
            "query_prefix": self.query_prefix,
            "document_prefix": self.document_prefix,
            "device": self.device,
            "runtime": self.runtime,
            "revision": self.revision,
            "cache_dir": self.cache_dir,
            "fingerprint": self.fingerprint,
        }
        if self.options:
            value["options"] = dict(self.options)
        return value


@dataclass(frozen=True)
class EmbeddingProfileConfig:
    active: str
    profiles: dict[str, EmbeddingProfile]

    @property
    def active_profile(self) -> EmbeddingProfile:
        try:
            return self.profiles[self.active]
        except KeyError as exc:
            raise EmbeddingProfileError(f"active embedding profile not found: {self.active}") from exc

    def as_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "profiles": {profile_id: profile.as_dict() for profile_id, profile in self.profiles.items()},
        }


def embedding_profiles_path(mem_dir: Path | None = None) -> Path:
    return (mem_dir or memassist_home()) / PROFILE_FILE_NAME


def ensure_embedding_profiles(mem_dir: Path | None = None) -> Path:
    path = embedding_profiles_path(mem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save_embedding_profile_config(default_embedding_profile_config(), mem_dir=path.parent)
    return path


def default_embedding_profile_config() -> EmbeddingProfileConfig:
    profiles = {
        "none": EmbeddingProfile(id="none", provider="none", model="none", quantization="none", dimension=0, normalize=False),
        "local-default": EmbeddingProfile(
            id="local-default",
            provider="model2vec",
            model="minishlab/potion-multilingual-128M",
            quantization="int8",
            dimension=256,
            normalize=True,
        ),
    }
    return EmbeddingProfileConfig(active="local-default", profiles=profiles)


def load_embedding_profile_config(mem_dir: Path | None = None) -> EmbeddingProfileConfig:
    path = ensure_embedding_profiles(mem_dir)
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raw = _parse_profile_yaml(text)
    if not isinstance(raw, dict):
        raise EmbeddingProfileError("embedding profile config must be a mapping")
    active = str(raw.get("active") or "")
    raw_profiles = raw.get("profiles")
    if not active:
        raise EmbeddingProfileError("embedding profile config must define active")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise EmbeddingProfileError("embedding profile config must define profiles")
    profiles: dict[str, EmbeddingProfile] = {}
    for profile_id, value in raw_profiles.items():
        if not isinstance(profile_id, str) or not isinstance(value, dict):
            raise EmbeddingProfileError("each embedding profile must be a mapping")
        profiles[profile_id] = _profile_from_mapping(profile_id, value)
    config = EmbeddingProfileConfig(active=active, profiles=profiles)
    _validate_config(config)
    return config


def save_embedding_profile_config(config: EmbeddingProfileConfig, *, mem_dir: Path | None = None) -> Path:
    _validate_config(config)
    path = embedding_profiles_path(mem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_profile_yaml(config), encoding="utf-8")
    return path


def activate_embedding_profile(profile_id: str, *, mem_dir: Path | None = None) -> EmbeddingProfileConfig:
    config = load_embedding_profile_config(mem_dir)
    if profile_id not in config.profiles:
        raise EmbeddingProfileError(f"unknown embedding profile: {profile_id}")
    updated = replace(config, active=profile_id)
    save_embedding_profile_config(updated, mem_dir=mem_dir)
    return updated


def get_embedding_profile(profile_id: str | None = None, *, mem_dir: Path | None = None) -> EmbeddingProfile:
    config = load_embedding_profile_config(mem_dir)
    if profile_id is None:
        return config.active_profile
    try:
        return config.profiles[profile_id]
    except KeyError as exc:
        raise EmbeddingProfileError(f"unknown embedding profile: {profile_id}") from exc


def _profile_from_mapping(profile_id: str, value: dict[str, object]) -> EmbeddingProfile:
    provider = str(value.get("provider") or "")
    model = str(value.get("model") or "")
    quantization = str(value.get("quantization") or "none")
    dimension = _optional_int(value.get("dimension"))
    normalize = _bool(value.get("normalize"), default=True)
    options = value.get("options")
    return EmbeddingProfile(
        id=profile_id,
        provider=provider,
        model=model,
        quantization=quantization,
        dimension=dimension,
        normalize=normalize,
        query_prefix=str(value.get("query_prefix") or ""),
        document_prefix=str(value.get("document_prefix") or ""),
        device=_optional_str(value.get("device")),
        runtime=_optional_str(value.get("runtime")),
        revision=_optional_str(value.get("revision")),
        cache_dir=_optional_str(value.get("cache_dir")),
        options=options if isinstance(options, dict) else {},
    )


def _validate_config(config: EmbeddingProfileConfig) -> None:
    if config.active not in config.profiles:
        raise EmbeddingProfileError(f"active embedding profile not found: {config.active}")
    for profile in config.profiles.values():
        if not profile.id:
            raise EmbeddingProfileError("embedding profile id cannot be empty")
        if profile.provider not in KNOWN_PROVIDERS:
            raise EmbeddingProfileError(f"unknown embedding provider: {profile.provider}")
        if not profile.model:
            raise EmbeddingProfileError(f"embedding profile {profile.id} must define model")
        if profile.quantization not in KNOWN_QUANTIZATIONS:
            raise EmbeddingProfileError(f"invalid quantization for {profile.id}: {profile.quantization}")
        if profile.dimension is not None and profile.dimension < 0:
            raise EmbeddingProfileError(f"invalid dimension for {profile.id}: {profile.dimension}")


def _parse_profile_yaml(text: str) -> dict[str, object]:
    data: dict[str, object] = {"profiles": {}}
    current_profile: str | None = None
    in_profiles = False
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0:
            current_profile = None
            if line == "profiles:":
                in_profiles = True
                continue
            key, value = _split_key_value(line)
            data[key] = _parse_scalar(value)
            continue
        if in_profiles and indent == 2 and line.endswith(":"):
            current_profile = line[:-1].strip()
            profiles = data.setdefault("profiles", {})
            if isinstance(profiles, dict):
                profiles[current_profile] = {}
            continue
        if in_profiles and indent >= 4 and current_profile:
            key, value = _split_key_value(line)
            profiles = data.get("profiles")
            if isinstance(profiles, dict) and isinstance(profiles.get(current_profile), dict):
                profiles[current_profile][key] = _parse_scalar(value)  # type: ignore[index]
            continue
    return data


def _render_profile_yaml(config: EmbeddingProfileConfig) -> str:
    lines = [
        "# memassist project embedding profiles",
        f"active: {config.active}",
        "profiles:",
    ]
    for profile_id, profile in config.profiles.items():
        lines.append(f"  {profile_id}:")
        for key, value in [
            ("provider", profile.provider),
            ("model", profile.model),
            ("quantization", profile.quantization),
            ("dimension", profile.dimension),
            ("normalize", profile.normalize),
            ("query_prefix", profile.query_prefix),
            ("document_prefix", profile.document_prefix),
            ("device", profile.device),
            ("runtime", profile.runtime),
            ("revision", profile.revision),
            ("cache_dir", profile.cache_dir),
        ]:
            if value is None or value == "":
                continue
            lines.append(f"    {key}: {_render_scalar(value)}")
    return "\n".join(lines) + "\n"


def _split_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise EmbeddingProfileError(f"invalid embedding profile line: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_scalar(value: str) -> object:
    stripped = value.strip().strip('"').strip("'")
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False
    if stripped.lower() in {"null", "~"}:
        return None
    try:
        return int(stripped)
    except ValueError:
        return stripped


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if not text or any(ch in text for ch in ":#[]{}"):
        return json.dumps(text, ensure_ascii=False)
    return text


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise EmbeddingProfileError(f"invalid embedding dimension: {value}") from exc


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)
