## ADDED Requirements

### Requirement: Explicit session approval SHALL override the next matching protected or sensitive path decision

When a user gives an explicit approval in the current session, memassist SHALL allow the next matching protected or sensitive path tool call for that same session and project, then consume the approval.

#### Scenario: Approval allows the next protected path edit

- **GIVEN** project policy includes `protected_paths: ["src/auth/session.py"]`
- **AND** the current session receives an explicit approval prompt such as `승인`
- **WHEN** a pre-tool check receives a patch targeting `src/auth/session.py` in the same session
- **THEN** memassist SHALL return `allow`
- **AND** memassist SHALL record that the approval was consumed

#### Scenario: Approval is single-use

- **GIVEN** project policy includes `protected_paths: ["src/auth/session.py"]`
- **AND** the current session receives one explicit approval prompt
- **WHEN** two consecutive pre-tool checks target `src/auth/session.py`
- **THEN** the first check SHALL return `allow`
- **AND** the second check SHALL return `block`

#### Scenario: Approval does not cross sessions

- **GIVEN** project policy includes `sensitive_paths: [".env"]`
- **AND** session `A` receives an explicit approval prompt
- **WHEN** session `B` receives a pre-tool check targeting `.env`
- **THEN** memassist SHALL return `warn`

#### Scenario: Approval matches glob protected path policies

- **GIVEN** project policy includes `protected_paths: ["src/auth/*.py"]`
- **AND** the current session receives an explicit approval prompt such as `승인`
- **WHEN** a pre-tool check receives a patch targeting `src/auth/session.py` in the same session
- **THEN** memassist SHALL return `allow`
- **AND** memassist SHALL record that the approval was consumed
