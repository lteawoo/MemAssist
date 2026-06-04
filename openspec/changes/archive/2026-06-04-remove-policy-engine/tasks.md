## 1. Implementation

- [x] 1.1 `policy.py` 축소: `PolicyEngine`, `PolicyDecision`, `check_pre_tool`,
  `append/remove_protected_path`, `append/remove_sensitive_path`, `_matches_any`,
  `_first_embedded_match`, `_glob_literal_hint` 삭제. `PolicyConfig`는
  `verification_commands`만 보유. `load_policy`와 `default_policy_yaml` 축소.
- [x] 1.2 `policy_simulator.py` 삭제.
- [x] 1.3 `cli.py`: `policy check` 서브커맨드와 `cmd_policy_check` 제거.
  pre-tool-use hook 집행 경로 제거 (trace 기록은 보존). `_codex_pre_tool_use_output` 삭제.
  `PolicyEngine` import 제거. `--mode guard` 선택지 제거.
- [x] 1.4 `hooks.py`: `guard` mode 제거. PreToolUse statusMessage 수정.
- [x] 1.5 `verifier.py`: `protected_paths` 집행 체크 제거.
- [x] 1.6 `gui.py`: `_api_policy` 에서 집행 키 제거.

## 2. Tests

- [x] 2.1 `test_memassist.py`: 정책 집행 관련 테스트 10개 제거. `PolicyEngine` import 제거.
- [x] 2.2 `test_gui.py`: `_seed_project`의 `protected_paths` 수동 추가 패턴 정리 (선택적).
- [x] 2.3 `PYTHONPATH=src python -m unittest discover -s tests` 통과 확인.

## 3. Spec & Docs

- [x] 3.1 `openspec/specs/memory-derived-enforcement/spec.md` 업데이트:
  deterministic 집행 시나리오 제거, retrieval-first 자율 판단 설명으로 교체.
- [x] 3.2 `README.md` 업데이트: guard mode, PreToolUse 경고·차단, `policy check` 명령 제거.

## 4. Evaluation

- [x] 4.1 `memassist doctor`, `memassist status`, `memassist init` 깨지지 않음 확인.
- [x] 4.2 전체 테스트 통과 확인.
