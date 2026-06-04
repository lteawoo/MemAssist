# project-local-storage Specification

## Purpose
Define how memassist scopes a project initialization to the current working directory, treating `<project>/.memassist` as the default project home for memory data, policy, ignore files, and hook-pinned runtime state, while never adopting a parent or user/global `.memassist` as the project root.
## Requirements
### Requirement: memassist SHALL initialize plain directories as local projects
When `memassist init` runs in a directory without `.git` or an existing project `.memassist`, memassist SHALL initialize the current working directory as the project root rather than adopting a parent directory.

#### Scenario: Plain folder init stays local
- **GIVEN** the current directory is `/work/plain-project`
- **AND** `/work/plain-project` has no `.git` and no `.memassist`
- **AND** a parent directory has `.memassist`
- **WHEN** the user runs `memassist init`
- **THEN** memassist SHALL create `/work/plain-project/.memassist`
- **AND** memassist SHALL store the project root as `/work/plain-project`
- **AND** memassist SHALL NOT write project policy or hooks into the parent directory

### Requirement: memassist SHALL use project `.memassist` as the default project home
For project-scoped initialization, memassist SHALL use `<project>/.memassist` as the default home for project memory data, policy, ignore files, and hook-pinned runtime state.

#### Scenario: Project DB is created under project `.memassist`
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL create `<project>/.memassist/policy.yaml`
- **AND** memassist SHALL create or use `<project>/.memassist/memassist.db` for project memory storage

#### Scenario: Project hook pins local memory home
- **WHEN** `memassist init --tools codex` installs project-scoped hooks
- **THEN** each memassist hook command SHALL set `MEMASSIST_HOME=<project>/.memassist`
- **AND** hook-time project detection SHALL resolve the hook payload cwd to the initialized project

### Requirement: memassist SHALL ignore user/global `.memassist` as project markers
Project root detection SHALL NOT treat user/global memassist homes as project markers.

#### Scenario: User home `.memassist` is ignored
- **GIVEN** `/Users/example/.memassist` exists
- **AND** the current directory is `/Users/example/projects/plain-project`
- **AND** the current directory has no `.git` and no `.memassist`
- **WHEN** memassist detects the project for initialization
- **THEN** memassist SHALL NOT return `/Users/example` as the project root
- **AND** memassist SHALL return `/Users/example/projects/plain-project`

#### Scenario: Explicit external MEMASSIST_HOME does not redefine project root
- **GIVEN** `MEMASSIST_HOME=/tmp/custom-home`
- **AND** `/Users/example/.memassist` exists
- **AND** the current directory is `/Users/example/projects/plain-project`
- **WHEN** memassist detects the project for initialization
- **THEN** memassist SHALL ignore `/Users/example/.memassist` as a project marker
- **AND** memassist SHALL initialize `/Users/example/projects/plain-project`
