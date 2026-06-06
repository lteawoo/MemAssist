from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class SourceRecord:
    id: str
    kind: str
    session_id: str | None
    project_id: str | None
    source_ref: str | None
    source_hash: str
    text: str
    summary: str | None
    created_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "text": self.text,
            "summary": self.summary,
            "created_at": self.created_at,
        }


def source_ledger_path(memassist_dir: Path) -> Path:
    return memassist_dir / "sources.jsonl"


def ensure_source_ledger(memassist_dir: Path) -> Path:
    path = source_ledger_path(memassist_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    return path


def append_source_record(
    memassist_dir: Path,
    *,
    kind: str,
    text: str,
    session_id: str | None,
    project_id: str | None,
    source_ref: str | None = None,
    summary: str | None = None,
) -> SourceRecord:
    full_text = text
    source_hash = hash_source(kind=kind, text=full_text, source_ref=source_ref)
    existing = find_source_record(memassist_dir, source_hash=source_hash)
    if existing:
        return existing
    record = SourceRecord(
        id=f"src_{uuid.uuid4().hex[:12]}",
        kind=kind,
        session_id=session_id,
        project_id=project_id,
        source_ref=source_ref,
        source_hash=source_hash,
        text=full_text,
        summary=summary,
        created_at=now_iso(),
    )
    path = ensure_source_ledger(memassist_dir)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record.as_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return record


def load_source_records(memassist_dir: Path) -> list[SourceRecord]:
    path = ensure_source_ledger(memassist_dir)
    records: list[SourceRecord] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        record = _record_from_dict(value)
        if record:
            records.append(record)
    return records


def find_source_record(memassist_dir: Path, *, source_hash: str) -> SourceRecord | None:
    for record in reversed(load_source_records(memassist_dir)):
        if record.source_hash == source_hash:
            return record
    return None


def hash_source(*, kind: str, text: str, source_ref: str | None = None) -> str:
    payload = json.dumps(
        {"kind": kind, "source_ref": source_ref, "text": text},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record_from_dict(value: dict[str, Any]) -> SourceRecord | None:
    record_id = value.get("id")
    kind = value.get("kind")
    source_hash = value.get("source_hash")
    text = value.get("text")
    created_at = value.get("created_at")
    if not all(isinstance(item, str) and item for item in (record_id, kind, source_hash, text, created_at)):
        return None
    return SourceRecord(
        id=record_id,
        kind=kind,
        session_id=_optional_str(value.get("session_id")),
        project_id=_optional_str(value.get("project_id")),
        source_ref=_optional_str(value.get("source_ref")),
        source_hash=source_hash,
        text=text,
        summary=_optional_str(value.get("summary")),
        created_at=created_at,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
