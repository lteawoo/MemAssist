from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .models import Memory


ACTIVE_ARTIFACT_STATUSES = {"active"}
CANDIDATE_ARTIFACT_STATUSES = {"candidate"}


def ensure_memory_artifact_dirs(memassist_dir: Path) -> None:
    for name in ("active", "candidates", "archived"):
        (memassist_dir / "memories" / name).mkdir(parents=True, exist_ok=True)


def sync_memory_artifact(
    memassist_dir: Path,
    memory: Memory,
    *,
    source_quote: str | None = None,
) -> None:
    try:
        ensure_memory_artifact_dirs(memassist_dir)
        target = _artifact_path(memassist_dir, memory)
        previous = _find_existing_artifact(memassist_dir, memory.id)
        if source_quote is None and previous and previous.exists():
            source_quote = _existing_source_quote(previous)
        if previous and previous != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(previous), str(target))
        target.write_text(_render_artifact(memory, source_quote=source_quote), encoding="utf-8")
    except OSError:
        return


def load_memory_artifacts(memassist_dir: Path) -> list[Memory]:
    ensure_memory_artifact_dirs(memassist_dir)
    memories: list[Memory] = []
    for bucket in ("active", "candidates", "archived"):
        for path in sorted((memassist_dir / "memories" / bucket).glob("*.md")):
            memory = read_memory_artifact(path)
            if memory:
                memories.append(memory)
    return memories


def read_memory_artifact(path: Path) -> Memory | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata = _metadata_from_text(text)
    if metadata is None:
        return None
    memory_id = str(metadata.get("id") or path.stem)
    status = _status_from_bucket(path) or str(metadata.get("status") or "candidate")
    return Memory(
        id=memory_id,
        scope_type=str(metadata.get("scope_type") or "project"),
        project_id=_optional_str(metadata.get("project_id")),
        session_id=_optional_str(metadata.get("session_id")),
        type=str(metadata.get("type") or "fact"),
        content=_artifact_content(text),
        reason=_optional_str(metadata.get("reason")) or _section_text(text, "Reason"),
        tags=_string_list(metadata.get("tags")),
        paths=_string_list(metadata.get("paths")),
        status=status,
        importance=_float(metadata.get("importance"), 0.5),
        confidence=_float(metadata.get("confidence"), 0.8),
        strength=_float(metadata.get("strength"), 0.5),
        recurrence=_int(metadata.get("recurrence"), 1),
        retrieval_count=_int(metadata.get("retrieval_count"), 0),
        utility=_float(metadata.get("utility"), 0.0),
        half_life_days=_float(metadata.get("half_life_days"), 30.0),
        enforcement=str(metadata.get("enforcement") or "none"),
        source_kind=str(metadata.get("source_kind") or "manual"),
        source_ref=_optional_str(metadata.get("source_ref")),
        created_at=str(metadata.get("created_at") or ""),
        updated_at=str(metadata.get("updated_at") or ""),
        last_used_at=_optional_str(metadata.get("last_used_at")),
        expires_at=_optional_str(metadata.get("expires_at")),
        superseded_by=_optional_str(metadata.get("superseded_by")),
    )


def _artifact_path(memassist_dir: Path, memory: Memory) -> Path:
    if memory.status in ACTIVE_ARTIFACT_STATUSES:
        bucket = "active"
    elif memory.status in CANDIDATE_ARTIFACT_STATUSES:
        bucket = "candidates"
    else:
        bucket = "archived"
    return memassist_dir / "memories" / bucket / f"{memory.id}.md"


def _find_existing_artifact(memassist_dir: Path, memory_id: str) -> Path | None:
    for bucket in ("active", "candidates", "archived"):
        path = memassist_dir / "memories" / bucket / f"{memory_id}.md"
        if path.exists():
            return path
    return None


def _existing_source_quote(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    section = _section_text(text, "Source Quote")
    if section:
        return section
    metadata = _metadata_from_text(text)
    if metadata is None:
        return None
    value = metadata.get("source_quote") if isinstance(metadata, dict) else None
    return str(value) if value else None


def _render_artifact(memory: Memory, *, source_quote: str | None) -> str:
    metadata: dict[str, Any] = {
        "id": memory.id,
        "scope_type": memory.scope_type,
        "project_id": memory.project_id,
        "session_id": memory.session_id,
        "type": memory.type,
        "status": memory.status,
        "importance": memory.importance,
        "confidence": memory.confidence,
        "strength": memory.strength,
        "recurrence": memory.recurrence,
        "utility": memory.utility,
        "half_life_days": memory.half_life_days,
        "tags": memory.tags,
        "paths": memory.paths,
        "enforcement": memory.enforcement,
        "source_kind": memory.source_kind,
        "source_ref": memory.source_ref,
        "source_quote": source_quote,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
        "last_used_at": memory.last_used_at,
        "expires_at": memory.expires_at,
        "superseded_by": memory.superseded_by,
    }
    title = memory.type.replace("_", " ").title()
    reason = memory.reason or ""
    sections = [
        "<!-- memassist",
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        "-->",
        f"# {title}",
        "",
        memory.content.strip(),
    ]
    if source_quote:
        sections.extend(["", "## Source Quote", "", source_quote.strip()])
    if reason:
        sections.extend(["", "## Reason", "", reason.strip()])
    return "\n".join(sections).rstrip() + "\n"


def _metadata_from_text(text: str) -> dict[str, Any] | None:
    match = re.search(r"<!-- memassist\n(.*?)\n-->", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return metadata if isinstance(metadata, dict) else None


def _artifact_content(text: str) -> str:
    body = re.sub(r"\A\s*<!-- memassist\n.*?\n-->\s*", "", text, flags=re.DOTALL)
    body = re.sub(r"\A# .*\n+", "", body)
    body = re.split(r"\n## (Source Quote|Reason)\n", body, maxsplit=1)[0]
    return body.strip()


def _section_text(text: str, heading: str) -> str | None:
    match = re.search(rf"\n## {re.escape(heading)}\n\n(.*?)(?:\n## |\Z)", text, flags=re.DOTALL)
    return match.group(1).strip() if match else None


def _status_from_bucket(path: Path) -> str:
    if path.parent.name == "active":
        return "active"
    if path.parent.name == "candidates":
        return "candidate"
    return "archived"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
