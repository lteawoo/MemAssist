## 1. Remove Built-In Enforcement Defaults

- [x] 1.1 Change policy configuration defaults so `sensitive_paths`, `protected_paths`, and `dangerous_commands` default to empty lists.
- [x] 1.2 Remove runtime fallback behavior that restores old sensitive path or dangerous command defaults when policy keys are missing.
- [x] 1.3 Update `default_policy_yaml()` so generated project policy contains no active enforcement entries.

## 2. Preserve Explicit Policy Matching Only

- [x] 2.1 Keep deterministic matching for explicitly configured `protected_paths`.
- [x] 2.2 Keep deterministic matching for explicitly configured `sensitive_paths`.
- [x] 2.3 Keep deterministic matching for explicitly configured `dangerous_commands`.
- [x] 2.4 Verify no code path applies hidden defaults when config files are absent, partial, or empty.

## 3. Tests

- [x] 3.1 Replace tests that expect fresh projects to block dangerous commands or warn on sensitive paths.
- [x] 3.2 Add tests proving fresh projects allow `.env` paths and `rm -rf`-style commands because no memory-derived policy exists.
- [x] 3.3 Add tests proving explicit project policy entries still warn/block.
- [x] 3.4 Add tests proving partial policy files do not revive removed defaults.

## 4. Documentation

- [x] 4.1 Remove README language that presents `.env`, key files, or dangerous shell commands as built-in defaults.
- [x] 4.2 Document that enforcement is memory-derived or explicitly project-configured.
- [x] 4.3 Avoid adding removed defaults as sample presets, commented examples, or recommended baseline policy.
