## Context

`ToolIntegration`(`integrations/base.py`)은 `install`/`uninstall`/`status`만 다형적으로 추상화한다. 런타임 hook 처리는 이 추상화 바깥에 있다: `cmd_hook_event`(`cli.py`)와 `extract_turn_end_memory_source`(`memory_judge.py:111`)가 호출 에이전트를 모른 채 payload 모양을 보고 도구를 역추론한다. `_user_text_from_transcript_obj`는 Codex transcript(`payload.message`)와 최상위 `role`만 파싱하고 Claude transcript(`{"type":"user","message":{"role":"user","content":...}}`)를 못 읽어, Claude에서는 source가 비어 `sources.jsonl`이 0줄이 되고 메모리 수집이 전혀 안 된다. 식별자 부재가 근본 원인이고, fallback(`_latest_user_prompt_from_codex_history`)도 Codex 전용이다.

설치 시점에는 어떤 integration인지 분명히 알지만(`ClaudeIntegration` 등), 그 지식이 설치하는 hook 명령(`python3 -m memassist hook stop`)에 실리지 않아 런타임으로 전달되지 않는다.

## Goals / Non-Goals

**Goals:**
- 호출 에이전트를 런타임이 추론이 아니라 명시적 식별자로 알게 한다.
- turn-end source 추출을 에이전트별 어댑터로 다형화하고, 공용 디스패치는 per-agent 분기 없이 유지한다.
- Claude Code transcript 형식을 정확히 파싱한다(버그 ① 해소).
- source ledger에 출처 에이전트를 보존한다.
- 식별자가 없거나 미지인 경우에도 hook이 깨지지 않게 fallback을 보장한다.

**Non-Goals:**
- 검색/주입(읽기 경로), judge 계약, 메모리 lifecycle 변경.
- 새로운 에이전트(예: 다른 IDE) 실제 추가. 이번엔 확장 가능한 골격만 만든다.

## Decisions

### 1. 식별자는 hook 명령 인자로 전달한다
`cmd_hook_event`에 `--agent <name>` 인자를 추가하고, 각 integration이 install 시 자기 이름을 명령에 박는다. payload 필드에 식별자를 끼워넣는 대안은 에이전트가 payload 스키마를 통제하므로 신뢰할 수 없어 배제한다. CLI 인자는 우리가 install 시점에 완전히 통제한다.

### 2. 파싱 책임을 `ToolIntegration`로 끌어올린다
`ToolIntegration` 프로토콜에 turn-end source 추출 메서드를 추가하고(`extract_turn_source(payload) -> TurnSource | None`), `ClaudeIntegration`/`CodexIntegration`/`OpenCodeIntegration`이 자기 형식을 파싱한다. `memory_judge`는 registry를 통해 어댑터를 디스패치한다.

### 3. 순환 의존성 회피: 파싱 결과 타입을 중립 위치에 둔다
integration이 `memory_judge`의 `TurnEndMemorySource`를 import하면 `memory_judge → registry → integrations → memory_judge` 순환이 생긴다. 따라서 어댑터가 반환할 경량 결과 타입(추출 텍스트 + `source_ref` + `source_kind`)을 `integrations/base.py`(또는 `models.py`) 같은 중립 모듈에 정의하고, `memory_judge`가 이를 `TurnEndMemorySource`로 매핑한다. 의존 방향은 `memory_judge → integrations` 한 방향만 유지한다.

### 4. `extract_turn_end_memory_source`는 디스패처로 축소한다
순서: ① payload 직접 prompt(에이전트 무관) → ② 식별자로 어댑터 조회해 추출 → ③ 식별자가 없거나 미지면 기존 호환 추출(현재 Codex-style sniffing + codex history)을 fallback으로 호출. per-agent `if` 분기는 어댑터 안으로 들어가고, 디스패처에는 남기지 않는다.

### 5. source ledger에 agent 필드를 추가한다
`append_source_record`에 선택적 `agent` 인자를 추가하고 레코드/`memory_source_observed` 이벤트에 포함한다. 식별자가 없으면 `null`/생략으로 두어 기존 레코드와 호환한다.

### 6. hook 명령 빌더 정비는 식별자 주입 범위 안에서 함께 한다
`_python_hook_command`를 수정해 `--agent`를 붙이므로, 같은 함수의 POSIX/`python3` 가정(②)도 이 작업 중에 점검한다. 단 OS 호환 전면 개편은 회귀 위험이 있어, 최소한 식별자 주입과 충돌하지 않는 선에서 다루고 광범위한 셸 호환 재작성은 별도 change로 분리한다.

## Risks / Trade-offs

- **기존 설치본의 hook에는 식별자가 없다** → 디스패처의 fallback 경로(결정 4-③)가 식별자 부재를 안전하게 처리한다. `init`/`tools repair` 재실행으로 식별자 있는 명령으로 갱신하도록 안내한다.
- **순환 import** → 결정 3의 중립 타입으로 의존 방향을 고정해 회피한다. 구현 시 import 그래프를 검증한다.
- **Codex 회귀** → Codex 어댑터는 기존 `_user_text_from_transcript_obj`/codex history 로직을 그대로 옮겨 동작 동등성을 유지하고, 기존 테스트로 회귀를 막는다.
- **OpenCode 형식 미상** → OpenCode 어댑터는 일단 payload 직접 prompt + 호환 fallback에 의존하고, 정확한 형식은 후속 작업으로 둔다(이번 범위는 골격).
- **식별자 위조/오용** → 식별자는 우리가 install한 명령에서만 오고 registered name으로 검증하므로, 미지값은 fallback으로 안전 처리한다.

## Migration Plan

1. 코드 변경 후 `memassist init --tools <...>` 또는 `memassist tools repair <...>`로 기존 프로젝트의 hook 명령을 식별자 포함 형태로 재설치.
2. 미갱신 설치본은 fallback 경로로 계속 동작(무중단). Codex는 그대로, Claude는 갱신 후 정상 수집 시작.
3. 롤백: 디스패처와 어댑터는 추가 계층이므로, 문제가 생기면 `extract_turn_end_memory_source`를 직접 추출 fallback만 쓰도록 되돌릴 수 있다.

## Open Questions

- OpenCode의 turn-end payload/transcript 실제 형식 — 별도 확인 필요(이번 범위 밖, 골격만).
- `--agent` 인자 이름 최종 확정(`--agent` vs `--source-agent`) — 구현 시 CLI 일관성 기준으로 결정.
