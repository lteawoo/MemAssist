from __future__ import annotations

import hashlib
import re
from typing import Iterable

from .models import MEMORY_CHUNK_KINDS, Memory, MemoryChunk
from .source_ledger import SourceRecord


DEFAULT_MAX_CHUNK_CHARS = 1200
DEFAULT_OVERLAP_CHARS = 120
RAW_SOURCE_MAX_CHUNK_CHARS = 1600
RAW_SOURCE_OVERLAP_CHARS = 160


def build_memory_chunks(
    memory: Memory,
    *,
    source_records: Iterable[SourceRecord] = (),
    created_at: str,
    indexed_at: str,
) -> list[MemoryChunk]:
    chunks: list[MemoryChunk] = []
    chunks.extend(
        _chunks_for_text(
            memory,
            kind="content",
            text=memory.content,
            source_ref=memory.source_ref,
            created_at=created_at,
            indexed_at=indexed_at,
        )
    )
    chunks.extend(
        _chunks_for_text(
            memory,
            kind="source_quote",
            text=memory.source_quote or "",
            source_ref=memory.source_ref,
            created_at=created_at,
            indexed_at=indexed_at,
        )
    )
    chunks.extend(
        _chunks_for_text(
            memory,
            kind="reason",
            text=memory.reason or "",
            source_ref=memory.source_ref,
            created_at=created_at,
            indexed_at=indexed_at,
        )
    )
    source_ids = set(memory.source_ids or [])
    for record in source_records:
        if record.id not in source_ids:
            continue
        chunks.extend(
            _chunks_for_text(
                memory,
                kind="raw_source",
                text=record.text,
                source_ref=record.source_ref,
                source_ids=[record.id],
                max_chars=RAW_SOURCE_MAX_CHUNK_CHARS,
                overlap_chars=RAW_SOURCE_OVERLAP_CHARS,
                created_at=created_at,
                indexed_at=indexed_at,
            )
        )
    return chunks


def chunk_embedding_text(chunk: MemoryChunk) -> str:
    return " ".join(
        part
        for part in [
            chunk.content,
            " ".join(chunk.tags),
            " ".join(chunk.paths),
        ]
        if part
    )


def chunk_content_hash(content: str) -> str:
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _chunks_for_text(
    memory: Memory,
    *,
    kind: str,
    text: str,
    source_ref: str | None,
    created_at: str,
    indexed_at: str,
    source_ids: list[str] | None = None,
    max_chars: int = DEFAULT_MAX_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[MemoryChunk]:
    if kind not in MEMORY_CHUNK_KINDS:
        raise ValueError(f"invalid memory chunk kind: {kind}")
    normalized = text.strip()
    if not normalized:
        return []
    chunks: list[MemoryChunk] = []
    for index, span in enumerate(_split_text(normalized, max_chars=max_chars, overlap_chars=overlap_chars)):
        content = span.content.strip()
        if not content:
            continue
        content_hash = chunk_content_hash(content)
        chunk_id = _chunk_id(
            memory_id=memory.id,
            kind=kind,
            index=index,
            source_ref=source_ref,
            content_hash=content_hash,
        )
        chunks.append(
            MemoryChunk(
                id=chunk_id,
                memory_id=memory.id,
                project_id=memory.project_id,
                scope_type=memory.scope_type,
                status=memory.status,
                chunk_index=index,
                chunk_kind=kind,
                content=content,
                content_hash=content_hash,
                tags=memory.tags,
                paths=memory.paths,
                source_ids=source_ids if source_ids is not None else (memory.source_ids or []),
                source_ref=source_ref,
                start_offset=span.start,
                end_offset=span.end,
                created_at=created_at,
                indexed_at=indexed_at,
            )
        )
    return chunks


class _TextSpan:
    def __init__(self, content: str, start: int, end: int) -> None:
        self.content = content
        self.start = start
        self.end = end


def _split_text(text: str, *, max_chars: int, overlap_chars: int) -> list[_TextSpan]:
    if len(text) <= max_chars:
        return [_TextSpan(text, 0, len(text))]
    spans: list[_TextSpan] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + max_chars)
        if end < length:
            boundary = _best_boundary(text, start=start, end=end, min_end=start + max_chars // 2)
            if boundary > start:
                end = boundary
        adjusted_start = start + len(text[start:end]) - len(text[start:end].lstrip())
        adjusted_end = end - (len(text[start:end]) - len(text[start:end].rstrip()))
        if adjusted_end > adjusted_start:
            spans.append(_TextSpan(text[adjusted_start:adjusted_end], adjusted_start, adjusted_end))
        if end >= length:
            break
        next_start = max(end - overlap_chars, start + 1)
        start = next_start
    return spans


def _best_boundary(text: str, *, start: int, end: int, min_end: int) -> int:
    candidates = [
        text.rfind("\n\n", min_end, end),
        text.rfind("\n", min_end, end),
    ]
    sentence_match = None
    for match in re.finditer(r"(?<=[.!?。！？])\s+", text[min_end:end]):
        sentence_match = min_end + match.end()
    if sentence_match is not None:
        candidates.append(sentence_match)
    boundary = max(candidates)
    if boundary <= start:
        return end
    if text.startswith("\n\n", boundary):
        return boundary + 2
    if text.startswith("\n", boundary):
        return boundary + 1
    return boundary


def _chunk_id(*, memory_id: str, kind: str, index: int, source_ref: str | None, content_hash: str) -> str:
    payload = f"{memory_id}:{kind}:{index}:{source_ref or ''}:{content_hash}"
    return f"chk_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"
