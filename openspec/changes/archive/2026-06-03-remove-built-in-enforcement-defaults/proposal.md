## Why

`memassist` currently includes built-in default enforcement for sensitive paths and dangerous commands. That makes the tool behave like a universal safety policy engine even when the project has not remembered or requested those rules.

This conflicts with the product's core responsibility: memassist should preserve and apply project/user memory. It should not impose independent guardrails such as `.env` warnings or `rm -rf` blocking unless those behaviors were explicitly recorded as memory-derived policy for the current project.

## What Changes

- Remove built-in default sensitive path enforcement.
- Remove built-in default dangerous command enforcement.
- Change the default project policy file to contain no active enforcement entries.
- Ensure policy checks only enforce policies compiled from explicit project/user memory or explicitly configured project policy.
- Remove documentation and tests that present universal sensitive path or dangerous command defaults as product behavior.
- Keep deterministic matching only as an execution mechanism for already-recorded policy, not as a source of policy.

## Non-Goals

- Do not keep the removed defaults as dormant presets, commented examples, suggested baseline policies, or hidden fallback behavior.
- Do not add a new universal safety layer under another name.
- Do not use LLM judgment to directly allow, warn, or block tool execution.
- Do not remove the ability to enforce explicit remembered policies.

## Capabilities

### New Capabilities

- `memory-derived-enforcement`: memassist enforces only active policy entries that come from explicit user/project memory or explicit project configuration.

### Modified Capabilities

- `project-policy-defaults`: initialized policy files no longer include active sensitive path or dangerous command defaults.
- `pre-tool-policy-check`: pre-tool checks continue to match compiled policy deterministically, but have no built-in universal enforcement rules.

## Impact

- Affected code:
  - `src/memassist/policy.py`
  - `src/memassist/cli.py`
  - `src/memassist/directives.py`
  - tests that assert built-in sensitive path or dangerous command behavior
- Affected docs:
  - `README.md`
  - `README.ko.md`
- Behavioral impact:
  - Fresh projects do not warn on `.env`, `*.pem`, `*.key`, or `*secret*` unless those paths are explicitly configured or remembered.
  - Fresh projects do not block `rm -rf`, `git reset --hard`, `git clean -fd`, or similar commands unless those commands are explicitly configured or remembered.
  - Existing explicit project policy entries remain enforceable.

