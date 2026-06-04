---
name: openspec-delivery-loop
description: Run an OpenSpec/SDD change through a contract-driven delivery loop with scoped implementation, independent verification, evaluator review, and finding-driven retries until the change satisfies the proposal and specs. Use when the user asks to delivery-loop a change, run implementation/verification/evaluation separately or in parallel, evaluate quality against OpenSpec, or keep reworking substandard results.
license: MIT
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "manual"
---

Run an OpenSpec change through a contract-driven delivery loop.

This skill is an orchestration workflow. OpenSpec artifacts are the source of truth; code changes, verification, evaluation, and retries are all judged against that contract.

Requires the `openspec` CLI.

**Input**: Optionally specify a change name. If omitted, infer it from conversation context or auto-select when there is exactly one active change. If ambiguous, run `openspec list --json` and ask the user to choose.

**Reference files**

Read only the references needed for the current step:
- `references/contract-normalization.md`: Use before implementation to convert artifacts into the delivery contract.
- `references/implementer-brief.md`: Use when making or delegating implementation changes.
- `references/verifier-brief.md`: Use when designing or delegating independent verification.
- `references/evaluator-rubric.md`: Use for final or interim quality review.
- `references/retry-policy.md`: Use when evaluation produces findings.

**Steps**

1. **Select the change**

   If a name is provided, use it. Otherwise:
   - Infer from the user's message if a change was named.
   - If exactly one active change exists, use that change.
   - If multiple active changes exist, ask the user to choose.

   Announce: `Using change: <name>`.

2. **Read OpenSpec status and instructions**

   ```bash
   openspec status --change "<name>" --json
   openspec instructions apply --change "<name>" --json
   ```

   Use the JSON output to identify:
   - schema name
   - context files
   - task artifact
   - progress
   - dynamic apply instructions
   - missing or blocked artifacts

   If the change is not apply-ready, stop and explain which artifact is missing.

3. **Read context files**

   Read the files listed in `contextFiles`. Do not assume fixed paths, but for spec-driven changes this usually includes:
   - `proposal.md`
   - `design.md`
   - `tasks.md`
   - `specs/*/spec.md`

4. **Normalize the delivery contract**

   Use `references/contract-normalization.md`.

   Produce a concise contract summary before editing:
   - goal
   - non-goals
   - affected modules
   - required behavior
   - acceptance criteria
   - required verification
   - risk areas

   If the artifacts conflict or the acceptance criteria are not actionable, pause and recommend artifact updates before implementation.

5. **Plan workstreams**

   Split the work into three roles:
   - **Implementer**: makes scoped changes that satisfy the contract.
   - **Verifier**: independently checks behavior, regressions, and required commands.
   - **Evaluator**: reviews final work against proposal/spec/task contracts.

   If subagent or delegation tools are available, use them for independent workstreams when practical. If not, execute the roles sequentially while keeping their evidence separate.

6. **Implement**

   Use `references/implementer-brief.md`.

   Implement pending tasks in contract order. Keep edits scoped. Do not mark a task complete until the corresponding behavior is implemented and at least focused local verification has passed.

7. **Verify independently**

   Use `references/verifier-brief.md`.

   Verification must not rely only on the implementer's summary. Run focused tests, relevant full or broad tests when risk justifies it, and `openspec validate --strict` when OpenSpec artifacts were changed or the change is ready for review.

8. **Evaluate**

   Use `references/evaluator-rubric.md`.

   Evaluate in code-review style:
   - findings first, ordered by severity
   - each finding cites a file/line or artifact requirement when possible
   - no findings means say so clearly and list residual risk or test gaps

9. **Retry when needed**

   Use `references/retry-policy.md`.

   If findings remain, retry only the failing areas. Each retry must map to a finding and must be re-verified and re-evaluated. If the same finding fails repeatedly because the contract is wrong or incomplete, pause and recommend proposal/design/spec updates.

10. **Close the loop**

   When all findings are resolved:
   - ensure task checkboxes reflect actual completion
   - summarize code changes
   - summarize verification commands and results
   - report residual risks
   - suggest archiving only when implementation and validation are complete

**Output format**

Before editing:

```text
Using change: <name>

Contract
- Goal: ...
- Non-goals: ...
- Required behavior: ...
- Required verification: ...
- Risk areas: ...

Workstreams
- Implementer: ...
- Verifier: ...
- Evaluator: ...
```

When evaluation fails:

```text
Result: retry required

Findings
- F1 [severity]: <contract violation or regression risk>

Retry scope
- F1: <specific correction and required verification>
```

When complete:

```text
Result: passed

Changes
- ...

Verification
- <command>: passed

Residual risk
- ...
```

**Guardrails**

- OpenSpec proposal, design, tasks, and specs are the contract.
- Keep implementation, verification, and evaluation evidence separate even when performed by one agent.
- Do not broaden scope to fix unrelated issues.
- Do not mark tasks complete based only on intent or partial implementation.
- Do not treat tests as sufficient when a spec requirement is visibly unmet.
- Do not archive or declare completion while evaluator findings remain.
