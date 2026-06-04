## Context

memassist has two concepts that are currently easy to confuse:

- data home: `MEMASSIST_HOME` or `~/.memassist`, used for SQLite storage
- project config: `<project>/.memassist`, used for policy and ignore files

That split made tests easy to isolate, but it created a bad default for real use. In a plain folder without `.git` or `.memassist`, `detect_project()` walks upward and can adopt a parent `.memassist` marker. If `MEMASSIST_HOME` is set to a different test/local folder, even `~/.memassist` is not excluded and can become the project marker.

## Goals

- `memassist init` in a plain folder initializes that folder, not a parent.
- Project-local `.memassist` is the single canonical folder for project memory state.
- Hooks installed by project init use the same project-local `.memassist` home.
- Runtime hook detection identifies the project from the hook payload cwd after init.
- Existing explicit global/user workflows remain possible.

## Non-Goals

- Do not remove support for user-level Codex hooks.
- Do not migrate every existing `~/.memassist` database automatically.
- Do not implement cross-project global memory sharing in this change.
- Do not change policy semantics or isolated judge behavior.

## Decisions

### Decision: init uses an init-specific root detector

`cmd_init` should not call generic `detect_project()` when the current directory has no marker. It should use a detector that:

1. resolves the requested start directory,
2. walks upward for `.git` or project `.memassist`,
3. ignores user/global `.memassist` homes,
4. returns `start` if no project marker exists.

This preserves git-root behavior for real repositories while making plain folders safe.

### Decision: project `.memassist` is the default project data home

After init determines the project root, it creates `<root>/.memassist` and uses that as the project-local home. Project hooks should include:

```bash
MEMASSIST_HOME=<root>/.memassist python3 -m memassist hook ...
```

That makes hook-time DB, trace, memories, policy, and project id consistent.

### Decision: runtime root detection ignores user/global homes

Generic `detect_project()` should ignore `.memassist` directories that are known global homes, including:

- `MEMASSIST_HOME` when configured,
- `Path.home()/.memassist`.

If no project marker exists at runtime, it can still return the provided start directory. After init, the local `.memassist` marker means hook runtime should resolve correctly.

### Decision: tests should use `.memassist` for project-local DB

Tests can still isolate by creating temporary project folders. They should no longer need `.memassist-home` for project tests. External `MEMASSIST_HOME` can remain for tests that intentionally exercise global behavior.

## Risks

- Existing users who expected `~/.memassist` as the default DB for every project may see new projects use local DBs. This is aligned with current product direction but should be documented.
- User-level hooks may still run for many projects. Project-local hooks are preferred for project memory. User-level hooks must rely on cwd detection and should not write into a parent `.memassist`.
- Tests that assume `MEMASSIST_HOME` points outside the project may need updates.

## Rollout

1. Add project-local home helpers and init-specific root detection.
2. Update init and project hook installation to pin `MEMASSIST_HOME=<project>/.memassist`.
3. Update runtime detection to ignore global homes as project markers.
4. Add regression coverage for plain folders, parent `~/.memassist`, project-local DB, and hook command generation.
5. Update README.
