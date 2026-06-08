## 1. 중립 타입과 어댑터 계약

- [x] 1.1 어댑터 파싱 결과용 경량 타입(추출 텍스트 + `source_ref` + `source_kind`)을 순환을 피할 중립 모듈(`integrations/base.py` 또는 `models.py`)에 정의
- [x] 1.2 `ToolIntegration` 프로토콜에 `extract_turn_source(payload) -> <결과 타입> | None` 메서드 추가
- [x] 1.3 의존 방향이 `memory_judge → integrations` 한 방향이 되도록 import 그래프 점검(순환 없음 확인)

## 2. 에이전트별 어댑터 구현

- [x] 2.1 `ClaudeIntegration.extract_turn_source`: `transcript_path`의 Claude transcript(`{"type":"user","message":{"role":"user","content":...}}`)에서 최신 user 텍스트 파싱
- [x] 2.2 `CodexIntegration.extract_turn_source`: 기존 `_user_text_from_transcript_obj`/`_latest_user_prompt_from_codex_history` 로직을 옮겨 Codex 동작 동등성 유지
- [x] 2.3 `OpenCodeIntegration.extract_turn_source`: payload 직접 prompt + 호환 fallback에 의존하는 최소 골격(정확 형식은 후속)
- [x] 2.4 transcript content가 문자열/리스트 양형식일 때 텍스트 추출이 동작하도록 보장

## 3. 식별자 전달 (install → runtime)

- [x] 3.1 `_python_hook_command`가 hook 명령에 `--agent <name>`을 포함하도록 수정
- [x] 3.2 각 integration install 경로가 자기 식별자를 명령 빌더에 전달하도록 연결(claude/codex/opencode)
- [x] 3.3 `cmd_hook_event`에 `--agent` 인자 추가, registered name이 아니면 미지로 취급
- [x] 3.4 식별자 주입 중 `_python_hook_command`의 POSIX/`python3` 가정이 식별자와 충돌하지 않는지 점검(광범위 셸 재작성은 범위 밖)

## 4. 디스패처 리팩토링

- [x] 4.1 `extract_turn_end_memory_source`에 calling-agent 인자를 전달
- [x] 4.2 추출 순서를 ① payload 직접 prompt → ② 식별자 어댑터 디스패치 → ③ 호환 fallback 으로 재구성
- [x] 4.3 per-agent `if` 분기를 어댑터로 이동하고 디스패처에서 제거
- [x] 4.4 식별자 없음/미지 시 fallback이 hook을 깨뜨리지 않는지 보장

## 5. source ledger 출처 보존

- [x] 5.1 `append_source_record`에 선택적 `agent` 인자 추가, 레코드 스키마에 포함
- [x] 5.2 `observe_turn_end_memory_source` / `memory_source_observed` 이벤트에 agent 전달
- [x] 5.3 agent가 없을 때 `null`/생략으로 두어 기존 레코드와 호환

## 6. 테스트

- [x] 6.1 Claude transcript fixture로 source 추출이 성공하는 단위 테스트 추가(버그 ① 회귀 가드)
- [x] 6.2 Codex 추출 동등성 회귀 테스트(기존 동작 유지 확인)
- [x] 6.3 식별자 없음/미지 fallback 경로 테스트
- [x] 6.4 `init --tools all` 동시 설치 시 hook 명령에 각 식별자가 박히고 ledger에 agent가 기록되는 통합 테스트
- [x] 6.5 `_python_hook_command`가 `--agent`를 포함하고 기존 install/status 테스트가 통과하는지 확인

## 7. 마무리

- [x] 7.1 `PYTHONPATH=src python3 -m unittest discover -s tests` 통과
- [x] 7.2 `openspec validate --all --strict` 통과
- [x] 7.3 README/문서에 `--agent` 식별자와 동시 설치 출처 추적, 기존 설치본 `tools repair` 갱신 안내 반영
