## Purpose

memassist는 retrieval-first memory layer다. 사용자 지시는 memory로 저장되어 context로
주입되며, agent가 그 context를 읽고 스스로 판단한다. hook 레벨 deterministic 집행은
memassist의 책임이 아니다.

## Requirements

### Requirement: memassist는 PreToolUse hook에서 tool call을 차단하거나 경고하지 않는다

memassist는 `PolicyEngine.check_pre_tool` 또는 이와 동등한 기계적 로직으로
`deny`/`block`/`warn`을 반환하지 않는다. 사용자 지시는 memory로 저장되어 retrieval을
통해 agent context에 주입된다.

#### Scenario: pre-tool-use hook은 trace만 기록하고 집행 출력을 내지 않는다

- **WHEN** `pre-tool-use` hook이 임의의 tool call에 대해 실행된다
- **THEN** hook은 trace 이벤트를 기록한다
- **AND** `permissionDecision: deny` 또는 `systemMessage` warning을 출력하지 않는다

#### Scenario: 사용자 지시는 메모리 → retrieval → context 주입 경로로만 처리된다

- **GIVEN** 사용자가 "리프레시 토큰 변경은 승인받고 진행해"라고 지시했다
- **WHEN** 다음 UserPromptSubmit에서 관련 작업이 요청된다
- **THEN** memassist는 저장된 memory를 context로 주입한다
- **AND** agent가 그 context를 읽고 승인을 요청하거나 멈추는 것은 agent의 자율 판단이다
- **AND** memassist는 PreToolUse에서 기계적으로 차단하지 않는다

### Requirement: verification_commands는 retrieval reminder로 보존된다

`policy.yaml`의 `verification_commands`는 집행(allow/warn/block) 목적이 아닌
검증 reminder 데이터다. `load_policy`는 이 필드를 읽어 retrieval 경로에서 사용한다.

#### Scenario: verification_commands 보존

- **GIVEN** `policy.yaml`에 `verification_commands`가 설정되어 있다
- **WHEN** `load_policy`가 호출된다
- **THEN** `verification_commands`는 로드된다
- **AND** 이 데이터는 `verifier` memory 섹션 retrieval과 verify/eval에서 사용된다
