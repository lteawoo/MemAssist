## Why

memassist의 역할은 **메모리 적재와 주입(retrieval-first memory layer)** 이다. 사용자가
"리프레시 토큰 변경은 승인받고 진행해" 같은 지시를 내리면, 그것은 memory로 저장되어
다음 UserPromptSubmit 시 context로 주입된다. **agent가 그 context를 읽고 스스로
멈추거나 승인을 요청하는 자율적 판단**이 실제 집행이다.

따라서 hook 레벨에서 `pre-tool-use`를 가로채 기계적으로 deny/warn을 내려보내는
`PolicyEngine.check_pre_tool` 기반 deterministic 집행은 memassist의 책임 범위를
벗어난다. 이 레이어는 삭제한다.

`verification_commands`는 집행(allow/warn/block)이 아닌 검증 reminder 데이터이므로
보존한다. `policy.yaml` 파일 자체도 `verification_commands` 보관 용도로 최소 형태로
유지한다.

## What Changes

- `src/memassist/policy.py`: `PolicyEngine`, `PolicyDecision`, `check_pre_tool`,
  `append/remove_protected_path`, `append/remove_sensitive_path`, `_matches_any`,
  `_first_embedded_match`, `_glob_literal_hint` 삭제.
  `sensitive_paths`, `protected_paths`, `dangerous_commands` 관련 코드 제거.
  `PolicyConfig`는 `verification_commands`만 보유하도록 축소.
  `load_policy`는 `verification_commands`만 읽도록 축소.
  `default_policy_yaml`에서 집행 관련 키 제거.

- `src/memassist/policy_simulator.py`: 전체 삭제.

- `src/memassist/cli.py`: `policy check` 서브커맨드와 `cmd_policy_check` 함수 제거.
  `cmd_hook_event`의 pre-tool-use 정책 집행 경로(`PolicyEngine.check_pre_tool` 호출,
  `_codex_pre_tool_use_output` 호출) 제거. trace 기록(`record_tool_event`)은 보존하되
  `policy_decision` 인자를 `None`으로 고정.
  `PolicyEngine` import 제거. `load_policy`는 `verify`/`eval`/`daemon` 경로에서만 사용.
  `_codex_pre_tool_use_output` 함수 삭제.

- `src/memassist/hooks.py`: `guard` mode를 `CODEX_MODE_EVENTS`에서 제거.
  `PreToolUse` hook의 `statusMessage`를 `"memassist policy check"` → 없애거나 trace
  로깅으로 변경. `guard` mode 관련 선택지 제거.

- `src/memassist/verifier.py`: `protected_paths` 체크 로직 제거. `PolicyConfig`는
  `verification_commands` 존재 여부만 확인.

- `src/memassist/gui.py`: `_api_policy`에서 `sensitive_paths`, `protected_paths`,
  `dangerous_commands` 노출 제거. `verification_commands`만 반환.

- `src/memassist/cli.py`: `init`, `tools install/repair` 의 `--mode guard` 선택지 제거.

- `tests/test_memassist.py`: 정책 집행 관련 테스트 제거:
  `test_policy_allows_sensitive_path_and_dangerous_command_without_project_policy`,
  `test_policy_enforces_explicit_sensitive_path_and_dangerous_command`,
  `test_policy_partial_file_does_not_restore_removed_defaults`,
  `test_policy_detects_protected_path_inside_apply_patch`,
  `test_hook_pre_tool_use_maps_block_to_deny`,
  `test_hook_user_approval_does_not_override_manual_protected_path`,
  `test_hook_user_approval_creates_no_single_use_grant`,
  `test_hook_user_approval_does_not_override_glob_protected_path_patch_target`,
  `test_hook_user_approval_does_not_cross_sessions`,
  `test_verify_treats_block_as_policy_block`.
  `PolicyEngine` import 제거.

- `openspec/specs/memory-derived-enforcement/spec.md`: 집행 시나리오를
  retrieval-first 자율 판단 설명으로 교체.

- `README.md`: `guard` mode 설명, PreToolUse 경고·차단, `policy check` 명령,
  정책 파일 집행 키 설명을 retrieval-first 자율 판단 설명으로 대체.

## Capabilities

### Modified Capabilities

- `retrieval-first-memory`: PreToolUse 정책 집행이 제거되고, 사용자 지시는
  memory context 주입을 통해 agent의 자율 판단으로만 처리된다.

### Removed Capabilities

- `memory-derived-enforcement` (deterministic): `PolicyEngine`과 pre-tool-use
  allow/warn/block 집행이 완전히 제거된다. `verification_commands`는 retrieval
  reminder로 보존된다.

## Impact

- `src/memassist/policy.py`: 대폭 축소 (집행 코드 전체 제거, verification_commands 보존)
- `src/memassist/policy_simulator.py`: 삭제
- `src/memassist/cli.py`: `policy check` 명령, hook 집행 경로 제거
- `src/memassist/hooks.py`: `guard` mode 제거
- `src/memassist/verifier.py`: `protected_paths` 집행 체크 제거
- `src/memassist/gui.py`: 집행 정책 필드 노출 제거
- `tests/test_memassist.py`: 집행 관련 테스트 10개 제거
- `tests/test_gui.py`: 수동으로 protected_paths 추가하는 seed 패턴 정리
- `README.md`: guard/PreToolUse 집행 설명 → retrieval-first 자율 판단 설명
- 저장된 데이터 마이그레이션 불필요
