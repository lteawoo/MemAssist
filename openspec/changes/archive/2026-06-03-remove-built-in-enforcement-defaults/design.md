## Context

The existing policy code mixes two responsibilities:

- generating policy defaults from hard-coded safety assumptions
- matching policy entries against tool calls

The first responsibility violates project autonomy because it creates enforcement without memory. The second responsibility is still useful when it is limited to policies that the project explicitly remembered or configured.

## Goals / Non-Goals

**Goals:**

- Make fresh policy configuration empty by default.
- Remove hard-coded sensitive path and dangerous command defaults from runtime behavior.
- Ensure no removed default survives as a preset, fallback, commented recommendation, or implicit policy.
- Preserve deterministic matching for explicit `protected_paths`, `sensitive_paths`, and `dangerous_commands` when those entries exist in project policy.
- Make tests prove that fresh projects have no built-in warnings or blocks.

**Non-Goals:**

- Replacing policy matching with LLM enforcement.
- Adding generic security advice to generated policy files.
- Changing memory storage semantics outside policy enforcement.

## Decisions

### 1. Empty defaults are the only valid default policy

`default_policy_yaml()` should write empty lists for all enforcement-bearing sections:

- `sensitive_paths: []`
- `protected_paths: []`
- `dangerous_commands: []`
- `verification_commands: []`

The old lists should not remain in constants that runtime code can fall back to.

### 2. Missing policy keys mean empty policy, not baseline policy

`load_policy()` should treat missing enforcement keys as empty lists. A partial policy file should not silently revive built-in sensitive path or command behavior.

### 3. The matcher remains deterministic but source-neutral

The pre-tool matcher may still support:

- regex command matching for entries that exist in `dangerous_commands`
- glob path matching for entries that exist in `sensitive_paths` and `protected_paths`
- embedded path matching inside patch/write commands

Those are matching mechanisms, not policy sources.

### 4. Directives and future LLM parsing may create policy entries

User/project memory interpretation can later use LLMs to produce structured policy candidates. Once a candidate is explicitly accepted or directly instructed, it can compile into project policy entries that the matcher enforces.

## Risks / Trade-offs

- **Existing tests and docs assume default safety behavior.** Update them to assert autonomy-first behavior instead.
- **Users may still want baseline safety behavior.** That can be expressed as explicit project policy or memory, but this change must not ship a default preset.
- **Fresh projects lose generic safety warnings.** This is intentional; memassist should not impose policies not derived from the project.

## Migration Plan

1. Remove built-in default sensitive path and dangerous command constants from runtime fallback behavior.
2. Change default policy generation to empty enforcement sections.
3. Update policy loading so absent keys resolve to empty lists.
4. Update tests for fresh-project allow behavior and explicit-policy enforcement behavior.
5. Remove docs that describe built-in universal warnings/blocks.

Rollback would reintroduce universal enforcement and is not aligned with the product direction.

