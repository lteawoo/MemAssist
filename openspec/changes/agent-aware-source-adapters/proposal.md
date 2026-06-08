## Why

런타임 hook 처리(turn-end source 추출, transcript 파싱)는 어떤 코딩 에이전트가 호출했는지 모른 채 단일 함수가 payload 모양을 보고 도구를 역추론(sniffing)한다. 그 결과 transcript 파서가 Codex 형식만 처리하고 Claude Code 형식을 못 읽어, Claude에서는 source가 한 건도 기록되지 않아 메모리 수집 전체가 막힌다. 도구가 늘수록 sniffing 분기가 누적되어 깨지기 쉬운 구조다.

## What Changes

- 각 tool integration이 install 시 hook 명령에 자기 **에이전트 식별자**를 포함시킨다 (예: `... hook stop --agent claude`). 런타임이 호출 에이전트를 추론이 아니라 명시적으로 알게 된다.
- `ToolIntegration` 추상화에 **turn-end source 파싱 책임**을 추가한다. 각 에이전트 어댑터가 자기 payload/transcript 형식에서 직접 사용자 발화를 추출한다.
  - Claude Code: transcript JSONL의 `{"type":"user","message":{"role":"user","content":...}}` 형식
  - Codex: payload/`~/.codex/history.jsonl`의 기존 형식
- `extract_turn_end_memory_source`의 sniffing 사다리를 **에이전트 식별자 기반 어댑터 디스패치**로 대체한다. 식별자가 없거나 미지의 경우에 한해 기존 호환 추출을 fallback으로 유지한다.
- source ledger 레코드에 **출처 에이전트**를 기록해, `init --tools all`로 여러 에이전트를 동시 설치했을 때 source가 어느 에이전트에서 왔는지 추적 가능하게 한다.
- 새 에이전트 추가가 **어댑터 클래스 추가만으로** 끝나도록 한다(개방-폐쇄). 이 과정에서 Claude transcript 파싱 버그는 자연히 해소된다.

## Capabilities

### New Capabilities
- `agent-source-extraction`: 호출 에이전트를 식별하고, 에이전트별 어댑터가 turn-end 사용자 발화 source를 자기 payload/transcript 형식에서 추출하는 런타임 계약. 식별자 전달, 어댑터 디스패치, fallback 동작을 정의한다.

### Modified Capabilities
- `memory-source-ledger`: source 레코드에 출처 에이전트 식별자를 보존하도록 요구사항을 확장한다.

## Impact

- 코드: `src/memassist/integrations/`(base/registry/claude/codex/opencode), `src/memassist/hooks.py`(`_python_hook_command`, hook 명령 빌더), `src/memassist/cli.py`(`cmd_hook_event`의 `--agent` 인자/디스패치), `src/memassist/memory_judge.py`(`extract_turn_end_memory_source`, `_user_text_from_transcript_obj`, codex history fallback), `src/memassist/source_ledger.py`(레코드 스키마).
- 설치물: `.claude/settings.json`, `.codex/hooks.json`, `.opencode/plugins/memassist.js`의 hook 명령 문자열이 변경된다. 기존 설치본은 `memassist init`/`tools repair` 재실행 또는 마이그레이션으로 갱신해야 한다.
- 동작: Claude Code에서 turn-end source 추출과 메모리 수집이 처음으로 정상 동작한다. Codex 동작은 식별자 경로로 동등하게 보존한다.
- 비목표: 검색/주입(읽기 경로), judge 계약, 메모리 lifecycle 자체는 변경하지 않는다.
