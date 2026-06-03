from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hooks import codex_hooks_status
from .paths import db_path, memassist_home
from .project import Project


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class DoctorReport:
    project_id: str
    project_root: str
    memassist_home: str
    database: str
    checks: list[DoctorCheck]

    @property
    def passed(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "project_id": self.project_id,
            "project_root": self.project_root,
            "memassist_home": self.memassist_home,
            "database": self.database,
            "checks": [check.as_dict() for check in self.checks],
        }


def run_doctor(project: Project) -> DoctorReport:
    root = project.root
    checks: list[DoctorCheck] = [
        _path_check("project_config", root / ".memassist" / "policy.yaml", "run `memassist init`"),
        _path_check("project_ignore", root / ".memassist" / "ignore", "run `memassist init`"),
    ]

    home = memassist_home()
    checks.append(
        DoctorCheck(
            name="home",
            status="pass" if home.exists() else "warn",
            detail=str(home) if home.exists() else f"{home} will be created when memassist stores data.",
        )
    )
    checks.append(
        DoctorCheck(
            name="database",
            status="pass" if db_path().exists() else "warn",
            detail=str(db_path()) if db_path().exists() else "No database exists yet; this is normal before first use.",
        )
    )

    hooks = codex_hooks_status(scope="project", project_root=root)
    checks.append(
        DoctorCheck(
            name="codex_project_hooks",
            status="pass" if hooks["installed"] else "warn",
            detail=(
                f"installed events: {', '.join(hooks['events'])}"
                if hooks["installed"]
                else "Project hooks are not installed; run `memassist hooks install codex`."
            ),
        )
    )

    codex_path = shutil.which("codex")
    if codex_path:
        checks.append(DoctorCheck("codex_cli", "pass", _codex_version(codex_path)))
    else:
        checks.append(DoctorCheck("codex_cli", "warn", "`codex` executable was not found on PATH."))

    checks.append(
        DoctorCheck(
            name="hook_trust",
            status="warn",
            detail="Codex project hooks must be trusted with `/hooks`; interactive CLI is the validated E2E path.",
        )
    )
    return DoctorReport(
        project_id=project.id,
        project_root=str(project.root),
        memassist_home=str(home),
        database=str(db_path()),
        checks=checks,
    )


def _path_check(name: str, path: Path, remediation: str) -> DoctorCheck:
    if path.exists():
        return DoctorCheck(name=name, status="pass", detail=str(path))
    return DoctorCheck(name=name, status="fail", detail=f"Missing {path}; {remediation}.")


def _codex_version(codex_path: str) -> str:
    try:
        result = subprocess.run(
            [codex_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"{codex_path}; version check failed."
    version = (result.stdout or result.stderr).strip()
    return f"{codex_path}; {version or 'version unknown'}"
