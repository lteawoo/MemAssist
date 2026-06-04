## Why

`memassist init` can currently misidentify a plain, non-git project as a parent directory when a parent contains `.memassist`. This happened in a temporary project where `MEMASSIST_HOME` was set to `.memassist-home`, while `/Users/twlee/.memassist` existed. The init flow treated the parent home `.memassist` as a project marker, installed hooks under `/Users/twlee/.codex`, and wrote project state against `/Users/twlee` instead of the intended working directory.

This violates the product model: project memory, policy, trace DB, and hook configuration should be anchored to the project being initialized. Users should not have to understand `MEMASSIST_HOME` or create a git repository for `memassist init` to behave locally.

## What Changes

- Make `memassist init` declare the current working directory as the project root when no nearer git or project `.memassist` marker exists.
- Treat the project `.memassist/` directory as the canonical local home for project memory state, including `memassist.db`, `policy.yaml`, `ignore`, exports, and hook-pinned `MEMASSIST_HOME`.
- Prevent parent user/global `.memassist` directories from becoming project roots during init or hook runtime detection.
- Ensure project-scoped hooks pin `MEMASSIST_HOME` to the initialized project `.memassist` directory.
- Preserve explicit opt-in support for external/global `MEMASSIST_HOME` where callers set it intentionally outside init, but do not let it change project root selection.

## Impact

- Affects project detection, init, hook command generation, status/doctor output, tests, and documentation.
- Existing project-local `.memassist/policy.yaml` remains valid.
- New initialized projects will store their SQLite DB at `<project>/.memassist/memassist.db` by default instead of relying on `~/.memassist/memassist.db`.
- User-level hooks and global memories remain possible, but project init defaults become local and self-contained.
