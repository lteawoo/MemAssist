from __future__ import annotations

from pathlib import Path

# Helper utilities shared with the isolated memory judge. The former directive
# interpreter that also lived here was removed once the judge superseded it; only
# the tag helpers the judge still calls remain.

GATE_WORDING_TERMS = {
    "approv",
    "confirm",
    "permission",
    "ask",
    "tell me",
    "check with me",
    "허락",
    "확인",
}


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


def _directive_caution_level(content: str) -> str:
    lowered = content.lower()
    block_terms = {
        "do not",
        "don't",
        "never",
        "must not",
        "block",
        "forbid",
        "without asking",
        "without approv",
        "금지",
        "막아",
        "건드리지",
        "수정하지",
        "바꾸지",
    }
    warn_terms = {"warn", "warning", "remind", "careful", "주의", "경고", "알려"}
    gate_before_change = any(term in lowered for term in GATE_WORDING_TERMS) and any(
        term in lowered for term in {"before", "change", "edit", "modify", "변경", "수정", "전에", "전"}
    )
    if any(term in lowered for term in block_terms):
        return "block"
    if gate_before_change or any(term in lowered for term in warn_terms):
        return "warn"
    return "none"


def _instruction_tags(content: str) -> list[str]:
    lowered = content.lower()
    tags = ["directive", "explicit", "user_prompt"]
    caution_level = _directive_caution_level(content)
    if caution_level != "none":
        tags.append(caution_level)
    if any(term in lowered for term in {"auth", "인증", "token", "토큰", "refresh", "리프레시"}):
        tags.append("auth")
    if any(term in lowered for term in {"token", "토큰"}):
        tags.append("token")
    if any(term in lowered for term in {"refresh", "리프레시"}):
        tags.append("refresh")
    if any(term in lowered for term in {"policy", "정책"}):
        tags.append("policy")
    return list(dict.fromkeys(tags))
