from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import memassist_home


@dataclass(frozen=True)
class Project:
    id: str
    root: Path
    git_remote: str | None


def detect_project(start: Path | None = None) -> Project:
    start = (start or Path.cwd()).resolve()
    root = _walk_for_root(start)
    remote = _git_remote(root)
    if remote:
        project_id = _normalize_remote(remote)
    else:
        digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
        project_id = f"local:{digest}"
    return Project(id=project_id, root=root, git_remote=remote)


def _walk_for_root(start: Path) -> Path:
    current = start
    global_memassist_home = memassist_home().resolve()
    while True:
        project_memassist = current / ".memassist"
        if (current / ".git").exists() or (
            project_memassist.exists() and project_memassist.resolve() != global_memassist_home
        ):
            return current
        if current.parent == current:
            return start
        current = current.parent


def _git_remote(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    remote = result.stdout.strip()
    return remote or None


def _normalize_remote(remote: str) -> str:
    cleaned = remote.strip()
    if cleaned.startswith("git@"):
        cleaned = cleaned.removeprefix("git@").replace(":", "/", 1)
    if cleaned.startswith("https://"):
        cleaned = cleaned.removeprefix("https://")
    if cleaned.startswith("http://"):
        cleaned = cleaned.removeprefix("http://")
    return cleaned.removesuffix(".git")
