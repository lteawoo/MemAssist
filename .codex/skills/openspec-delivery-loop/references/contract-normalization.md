# Contract Normalization

Convert OpenSpec artifacts into a delivery contract before implementation.

## Inputs

Use the files returned by:

```bash
openspec instructions apply --change "<name>" --json
```

Typical spec-driven inputs:
- `proposal.md`
- `design.md`
- `tasks.md`
- `specs/*/spec.md`

## Contract Fields

Extract these fields:

- **Goal**: The intended outcome in one or two sentences.
- **Non-goals**: Explicit exclusions and implied boundaries from the proposal/design.
- **Affected modules**: Files, packages, commands, APIs, data stores, or UI surfaces likely to change.
- **Required behavior**: Observable behavior required by specs and acceptance criteria.
- **Acceptance criteria**: Conditions that must be true before completion.
- **Required verification**: Commands, tests, manual checks, OpenSpec validation, or regression paths.
- **Risk areas**: Compatibility, data migration, auth/security, persistence, concurrency, CLI behavior, UI behavior, performance, or cleanup risks.

## Conflict Handling

Pause before implementation when:
- proposal and specs disagree
- tasks ask for behavior outside the proposal
- design requires an unavailable dependency or external service
- acceptance criteria cannot be tested or observed
- a breaking change is implied but not called out

When pausing, recommend the artifact that should change:
- scope mismatch -> `proposal.md`
- implementation strategy mismatch -> `design.md`
- missing work item -> `tasks.md`
- behavioral contract mismatch -> `specs/<capability>/spec.md`

## Output

Keep the contract summary short:

```text
Contract
- Goal: ...
- Non-goals: ...
- Affected modules: ...
- Required behavior: ...
- Acceptance criteria: ...
- Required verification: ...
- Risk areas: ...
```
