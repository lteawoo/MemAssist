from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .policy import PolicyEngine, load_policy


@dataclass(frozen=True)
class PolicySimulationResult:
    passed: bool
    path: str
    action: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
        }


def simulate_protected_path(project_root: Path, path: str) -> PolicySimulationResult:
    command = (
        "*** Begin Patch\n"
        f"*** Update File: {path}\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )
    decision = PolicyEngine(load_policy(project_root)).check_pre_tool(
        tool="apply_patch",
        args={"command": command},
    )
    return PolicySimulationResult(
        passed=decision.action in {"require_approval", "deny"},
        path=path,
        action=decision.action,
        reason=decision.reason,
    )
