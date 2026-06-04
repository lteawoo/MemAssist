# Evaluator Rubric

Evaluate like a code review, with OpenSpec contract compliance first.

## Review Order

1. **Contract compliance**
   - Does the implementation satisfy every required behavior?
   - Are non-goals respected?
   - Are breaking changes explicit and intentional?
   - Are task checkboxes honest?

2. **Behavioral correctness**
   - Does the feature or fix work as specified?
   - Are edge cases handled?
   - Are errors and invalid states handled appropriately?

3. **Regression risk**
   - Could affected modules regress?
   - Are legacy paths, compatibility boundaries, or migration assumptions covered?
   - Are shared helpers or public interfaces changed safely?

4. **Test quality**
   - Would tests fail without the implementation?
   - Do tests cover meaningful behavior instead of implementation trivia?
   - Are negative and regression cases included where risk requires them?

5. **Maintainability**
   - Does the code match local patterns?
   - Is complexity justified by the contract?
   - Are comments, names, and boundaries clear enough?

## Finding Format

Findings come first, ordered by severity:

```text
Findings
- F1 [high] path/to/file.py:123: Requirement X is not satisfied because ...
- F2 [medium] tests/test_x.py:45: Regression path Y is untested because ...
```

Each finding should include:
- severity
- file/line or artifact reference when possible
- violated requirement or risk
- concrete remediation

## Pass Criteria

Declare pass only when:
- no blocking or material findings remain
- required verification passed
- remaining risks are explicit and acceptable
- task state matches actual work

If there are no findings, say so plainly and mention any residual test gaps or operational risks.
