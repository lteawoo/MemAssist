## 1. Project Detection

- [x] 1.1 Add an init-specific project detector that returns cwd when no git/project marker exists.
- [x] 1.2 Update generic project detection to ignore known user/global `.memassist` homes as project markers.
- [x] 1.3 Add tests for plain folders under a parent/user `.memassist` with external `MEMASSIST_HOME`.

## 2. Project-Local Home

- [x] 2.1 Add helpers for resolving a project-local memassist home at `<project>/.memassist`.
- [x] 2.2 Update `cmd_init` to use project-local `.memassist` as the DB/home for project initialization.
- [x] 2.3 Ensure init creates or opens `<project>/.memassist/memassist.db`.
- [x] 2.4 Preserve explicit external `MEMASSIST_HOME` support outside project init.

## 3. Hook Installation

- [x] 3.1 Update project-scoped Codex hook generation to pin `MEMASSIST_HOME=<project>/.memassist`.
- [x] 3.2 Ensure project-scoped Claude/OpenCode integrations remain rooted under the initialized project.
- [x] 3.3 Add tests that project hooks do not install into a parent/user directory for plain projects.

## 4. Verification

- [x] 4.1 Add an end-to-end temp project test for `init --tools codex` in a non-git plain folder with parent `.memassist`.
- [x] 4.2 Add a hook test proving `UserPromptSubmit` stores a judged candidate into the project-local DB.
- [x] 4.3 Run focused project detection/init tests.
- [x] 4.4 Run full unittest suite.
- [x] 4.5 Run strict OpenSpec validation.

## 5. Documentation

- [x] 5.1 Document that project init uses a single project `.memassist` folder for policy, ignore, DB, and project hook runtime state.
- [x] 5.2 Document that `~/.memassist` is only a fallback/global home, not the default project storage for initialized projects.
