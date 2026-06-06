from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row
from typing import Any

from .models import Memory
from .verification_config import VerificationConfig
from .session import summarize_session
from .trace import command_is_test


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    issues: list[str]
    warnings: list[str]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "issues": self.issues,
            "warnings": self.warnings,
            "summary": self.summary,
        }


def verify_session(
    events: list[Row],
    *,
    memories: list[Memory],
    verification_config: VerificationConfig,
) -> VerificationResult:
    summary = summarize_session(events)
    issues: list[str] = []
    warnings: list[str] = []

    if summary.denied_events:
        warnings.append(f"{summary.denied_events} tool call(s) were denied by the host tool.")

    verifier_memories = [
        memory
        for memory in memories
        if memory.type == "workflow" or "verification" in memory.tags or "test" in memory.tags
    ]
    requires_tests = bool(verifier_memories or verification_config.verification_commands)
    test_commands = [command for command in summary.commands if command_is_test(command)]
    if requires_tests and not test_commands and not summary.denied_events:
        issues.append("Verification workflow is configured but no test command was recorded.")

    return VerificationResult(
        passed=not issues,
        issues=issues,
        warnings=warnings,
        summary=summary.as_dict(),
    )
