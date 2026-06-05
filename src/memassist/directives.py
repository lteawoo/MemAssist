from __future__ import annotations

from pathlib import Path

# Helper utilities shared with the isolated memory judge. The former directive
# interpreter that also lived here was removed once the judge superseded it.


def _normalize_project_files(files: list[str], project_root: Path) -> list[str]:
    normalized: list[str] = []
    resolved_root = project_root.resolve()
    for file in files:
        path = Path(file)
        candidate = path if path.is_absolute() else project_root / path
        try:
            file = candidate.resolve().relative_to(resolved_root).as_posix()
        except ValueError:
            continue
        if file == ".." or file.startswith("../"):
            continue
        if file not in normalized:
            normalized.append(file)
    return normalized
