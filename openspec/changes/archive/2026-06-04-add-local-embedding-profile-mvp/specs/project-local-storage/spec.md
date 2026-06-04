## ADDED Requirements

### Requirement: memassist SHALL keep embedding profiles project-local

Project-scoped initialization SHALL create or use embedding profile configuration under the project `.memassist` home. A parent, user-global, or unrelated project `.memassist` SHALL NOT define the active profile for the initialized project.

#### Scenario: Init creates local profile file
- **WHEN** a user runs `memassist init` in a project directory
- **THEN** memassist SHALL create or prepare `<project>/.memassist/embedding-profiles.yaml`
- **AND** the initialized project SHALL use that file for active embedding profile selection

#### Scenario: Parent profile file is ignored
- **GIVEN** a parent directory has `.memassist/embedding-profiles.yaml`
- **AND** the current project has no `.memassist`
- **WHEN** a user runs `memassist init` in the current project
- **THEN** memassist SHALL initialize `<project>/.memassist/embedding-profiles.yaml`
- **AND** memassist SHALL NOT adopt the parent profile file as the current project's active profile configuration

### Requirement: memassist SHALL keep derived vector cache project-local

Embedding vector cache rows and profile cache metadata SHALL live under the project `.memassist` home or project-local SQLite database. Deleting or rebuilding those derived rows SHALL NOT delete authoritative Markdown memories.

#### Scenario: Project-local cache is rebuilt from Markdown
- **GIVEN** active Markdown memory artifacts exist under `<project>/.memassist/memories/active`
- **AND** the project SQLite vector cache is missing
- **WHEN** the user builds embeddings for an active profile
- **THEN** memassist SHALL create derived vector cache rows under the project `.memassist` runtime state
- **AND** the Markdown memory artifacts SHALL remain authoritative

#### Scenario: Provider model cache may be external but vector rows remain local
- **GIVEN** a local embedding provider downloads model files to a user cache directory
- **WHEN** memassist builds memory embeddings for a project profile
- **THEN** the derived memory vector rows SHALL be stored in the project `.memassist` home or project-local SQLite database
- **AND** the provider model cache location SHALL NOT redefine project memory ownership
