from __future__ import annotations

import os
from pathlib import Path


def memassist_home() -> Path:
    configured = os.environ.get("MEMASSIST_HOME")
    if configured:
        return Path(configured).expanduser()
    project_home = nearest_project_memassist_home()
    if project_home:
        return project_home
    return user_memassist_home()


def db_path() -> Path:
    return memassist_home() / "memassist.db"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def user_memassist_home() -> Path:
    return (Path.home() / ".memassist").expanduser()


def project_memassist_home(project_root: Path) -> Path:
    return project_root.resolve() / ".memassist"


def nearest_project_memassist_home(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    ignored = {user_memassist_home().resolve()}
    configured = os.environ.get("MEMASSIST_HOME")
    if configured:
        ignored.add(Path(configured).expanduser().resolve())
    while True:
        candidate = current / ".memassist"
        if candidate.exists() and candidate.resolve() not in ignored:
            return candidate.resolve()
        if current.parent == current:
            return None
        current = current.parent
