from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .extraction import extract_candidates
from .models import Memory
from .policy import PolicyConfig
from .verifier import verify_session


@dataclass(frozen=True)
class EvalResult:
    passed: bool
    verification: dict[str, Any]
    candidate_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verification": self.verification,
            "candidate_count": self.candidate_count,
        }


def run_eval(events: list[Any], *, memories: list[Memory], policy: PolicyConfig) -> EvalResult:
    verification = verify_session(events, memories=memories, policy=policy)
    candidates = extract_candidates(events)
    return EvalResult(
        passed=verification.passed,
        verification=verification.as_dict(),
        candidate_count=len(candidates),
    )

