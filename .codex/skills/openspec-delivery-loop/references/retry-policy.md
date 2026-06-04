# Retry Policy

Retries are finding-driven. Do not restart the whole change unless the evaluator shows the implementation direction is invalid.

## Retry Inputs

Each retry needs:
- finding id
- severity
- violated requirement or risk
- specific remediation
- required verification after remediation

## Retry Rules

- Retry only the failing areas.
- Keep unrelated passing work intact.
- Re-run the focused verification for the corrected area.
- Re-run evaluator review for the corrected area and any touched neighbors.
- Close a finding only when evidence shows the violation is resolved.

## Escalate To Artifact Update

Pause and recommend OpenSpec artifact updates when:
- the same finding fails repeatedly because the requirement is ambiguous
- implementation proves the design is infeasible
- the proposal scope is too broad or too narrow
- verification cannot establish pass/fail from the current acceptance criteria

## Output

```text
Retry plan
- F1: <correction>
- Verification: <command/check>
- Evaluation focus: <requirement/risk>
```

After retry:

```text
Retry result
- F1: closed / still open
- Evidence: ...
- New findings: ...
```
