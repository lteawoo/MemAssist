from __future__ import annotations

import os
from pathlib import Path


def memassist_home() -> Path:
    return Path(os.environ.get("MEMASSIST_HOME", "~/.memassist")).expanduser()


def db_path() -> Path:
    return memassist_home() / "memassist.db"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()

