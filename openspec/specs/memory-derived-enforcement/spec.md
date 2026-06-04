## Purpose

memassist는 retrieval-first memory layer다. 사용자 지시는 memory로 저장되어
UserPromptSubmit 시 context로 주입되며, **agent가 그 context를 읽고 스스로
멈추거나 승인을 요청하는 자율적 판단**이 실제 집행이다.

deterministic PolicyEngine 기반 PreToolUse allow/warn/block 집행은 memassist의
책임이 아니므로 제거되었다. `verification_commands`는 집행이 아닌 검증 reminder
데이터로 보존된다.

## Requirements

### Requirement: memassist SHALL NOT block or warn tool calls in the PreToolUse hook

memassist SHALL NOT return `deny`/`block`/`warn` from `PolicyEngine.check_pre_tool`
or equivalent mechanical policy logic. 사용자 지시는 memory로 저장되어 retrieval을
통해 agent context에 주입된다.

#### Scenario: pre-tool-use hook은 trace만 기록하고 집행 출력을 내지 않는다

- **WHEN** `pre-tool-use` hook이 임의의 tool call에 대해 실행된다
- **THEN** hook은 trace 이벤트를 기록한다
- **AND** `permissionDecision: deny` 또는 `systemMessage` warning을 출력하지 않는다

#### Scenario: 사용자 지시는 memory retrieval → context 주입 경로로만 처리된다

- **GIVEN** 사용자가 "리프레시 토큰 변경은 승인받고 진행해"라고 지시했다
- **WHEN** isolated memory judge가 이를 persistent memory로 저장했다
- **WHEN** 다음 UserPromptSubmit에서 관련 작업이 요청된다
- **THEN** memassist는 저장된 memory를 `additionalContext`로 주입한다
- **AND** agent가 그 context를 읽고 승인을 요청하거나 멈추는 것은 agent의 자율 판단이다
- **AND** memassist는 PreToolUse에서 기계적으로 차단하지 않는다

### Requirement: verification_commands SHALL be preserved as retrieval reminders

`verification.yaml`의 `verification_commands` SHALL be treated as retrieval reminders,
not allow/warn/block caution_level data. `load_verification_config`는 이 필드를 읽어 verifier 섹션
retrieval과 verify/eval에서 사용한다.

#### Scenario: verification_commands 보존

- **GIVEN** `verification.yaml`에 `verification_commands`가 설정되어 있다
- **WHEN** `load_verification_config`가 호출된다
- **THEN** `verification_commands`는 로드된다
- **AND** 이 데이터는 `verifier` memory 섹션 retrieval과 verify/eval에서 사용된다

### Requirement: generated verification config files SHALL NOT include caution_level keys

새로 생성되는 `verification.yaml` SHALL NOT include `sensitive_paths`, `protected_paths`,
or `dangerous_commands` keys.

#### Scenario: 기본 verification config 파일 형식

- **WHEN** `memassist init`이 실행된다
- **THEN** 생성된 `verification.yaml`은 `verification_commands: []`만 포함한다
- **AND** `sensitive_paths`, `protected_paths`, `dangerous_commands`는 포함하지 않는다
