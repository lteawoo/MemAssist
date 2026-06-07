from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .integrations import status_tools
from .embedding_profiles import EmbeddingProfileError, get_embedding_profile
from .embeddings import embedding_model_status
from .memory_judge import judge_backend_diagnostics
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
        _path_check("project_config", root / ".memassist" / "verification.yaml", "run `memassist init`"),
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
    try:
        profile = get_embedding_profile(None, mem_dir=root / ".memassist")
        embedding = embedding_model_status(profile, mem_dir=root / ".memassist")
        checks.append(
            DoctorCheck(
                name="embedding_model",
                status="pass" if embedding.status in {"ok", "disabled"} else "warn",
                detail=_embedding_detail(embedding.as_dict()),
            )
        )
    except EmbeddingProfileError as exc:
        checks.append(
            DoctorCheck(
                name="embedding_model",
                status="warn",
                detail=f"Embedding profile is not readable: {exc}",
            )
        )

    statuses = status_tools(project, tools=[], scope="project")
    installed_tools = [status.tool for status in statuses if status.installed]
    checks.append(
        DoctorCheck(
            name="tool_integrations",
            status="pass" if installed_tools else "warn",
            detail=(
                f"installed tools: {', '.join(installed_tools)}"
                if installed_tools
                else "No tool integrations are installed; run `memassist init --tools codex` or `memassist init --tools all`."
            ),
        )
    )
    judge = judge_backend_diagnostics(project)
    checks.append(
        DoctorCheck(
            name="memory_judge",
            status="pass" if judge["available"] else "warn",
            detail=str(judge["detail"]),
        )
    )

    checks.append(
        DoctorCheck(
            name="hook_trust",
            status="warn",
            detail="Tool integrations may require each agent CLI to trust project config before trace capture runs.",
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


def _embedding_detail(value: dict[str, object]) -> str:
    status = str(value.get("status") or "")
    profile_id = str(value.get("profile_id") or "")
    path = value.get("path")
    detail = value.get("detail")
    parts = [f"profile={profile_id}", f"status={status}"]
    if isinstance(path, str) and path:
        parts.append(f"path={path}")
    if isinstance(detail, str) and detail:
        parts.append(detail)
    return "; ".join(parts)
