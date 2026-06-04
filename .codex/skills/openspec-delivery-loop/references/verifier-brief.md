# Verifier Brief

The verifier checks the implementation independently against the contract.

## Verification Scope

Verify:
- required behavior from specs and proposal
- task completion honesty
- regression paths in affected modules
- edge cases implied by the design or data model
- command help, docs, or UI behavior when user-facing surfaces changed
- OpenSpec validation when artifacts changed or the change is ready for review

## Test Selection

Prefer this order:

1. Focused tests for the changed behavior.
2. Existing nearby tests that could regress.
3. Broader suite when shared infrastructure, persistence, auth, CLI routing, or public APIs changed.
4. Manual or exploratory checks only when automated coverage is impractical.

Use `openspec validate --strict` when specs or change artifacts are part of the deliverable.

## Independence Rules

- Do not accept the implementer's handoff as proof.
- Re-read the contract and inspect relevant changed code.
- A passing test is not enough if the implementation visibly violates the spec.
- A missing test is a finding when the risk or acceptance criteria require coverage.

## Output

```text
Verification result
- Commands run: ...
- Passed: ...
- Failed: ...
- Untested required behavior: ...
- Regression concerns: ...
```
