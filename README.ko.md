# memassist

프로젝트 로컬 메모리 RAG 시스템입니다.

`memassist`는 사용자 선호, 결정, 작업 방식, 프로젝트 맥락을 프로젝트 안의 로컬
메모리로 저장합니다. 이후 관련 요청이 들어오면 저장된 메모리를 검색해 Codex,
Claude Code, OpenCode 같은 활성 AI 코딩 에이전트의 컨텍스트로 주입합니다.

메모리는 정책이 아니라 컨텍스트입니다. `memassist`는 도구 실행을 차단하거나
에이전트의 판단을 대신하지 않습니다.

[English README](README.md)

## 왜 쓰나요?

- 프로젝트 선호, 테스트 명령, 리뷰 기준, 결정 사항을 반복해서 설명하지 않기 위해.
- 메모리를 프로젝트 로컬에 두고 Markdown으로 직접 확인하기 위해.
- 메모리가 생긴 원문 근거를 함께 보존하기 위해.
- 현재 요청과 관련 있는 메모리만 찾아 주입하기 위해.

## 설치

```bash
python3 -m pip install -e .
```

프로젝트에서 초기화합니다.

```bash
memassist init --tools codex
memassist doctor
```

`memassist init`은 `.memassist/`를 만들고, 프로젝트 설정과 도구 integration을
설치하며, 활성 로컬 임베딩 모델도 준비합니다. 모델 설치를 나중으로 미루고 싶을
때만 `--skip-embedding-install`을 사용합니다.

지원하는 integration은 다음과 같습니다.

| 도구 | 프로젝트 설정 파일 |
| --- | --- |
| Codex | `.codex/hooks.json` |
| Claude Code | `.claude/settings.json` |
| OpenCode | `.opencode/plugins/memassist.js` |

필요하면 여러 도구를 함께 설치합니다.

```bash
memassist init --tools codex,claude,opencode
memassist init --tools all
memassist tools status --json
```

## 핵심 흐름

`memassist`는 읽기 경로와 쓰기 경로를 분리합니다.

### 메모리 탐색과 주입

새 사용자 프롬프트가 들어올 때 실행됩니다.

```text
Retrieval & Injection

┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ UserPromptSubmit │──▶│ Build query      │──▶│ Hybrid search    │
│ new prompt       │   │ prompt + context │   │ local memories   │
└──────────────────┘   └──────────────────┘   └────────┬─────────┘
                                                        │
                                                        ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Agent decides    │◀──│ Inject context   │◀──│ Rank memory pack │
│ what to do       │   │ to active agent  │   │ for this request │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

하이브리드 검색은 다음 신호를 조합합니다.

| 신호 | 기준 |
| --- | --- |
| Lexical | chunked memory text에 대한 SQLite FTS5 |
| Vector | 활성 로컬 embedding profile 기반 chunk vector similarity |
| Structure | tags, paths, links, status, chunk type |
| Fusion | 채널별 결과를 Reciprocal Rank Fusion (RRF-style)로 결합 |
| Usage | `retrieval_count`, `last_used_at`, `utility`, `strength` |

주입되는 결과는 현재 요청을 위한 memory pack입니다. 그 컨텍스트를 어떻게 사용할지는
에이전트가 판단합니다.

### 메모리 저장

턴이 끝난 뒤 실행됩니다. 기본 stop hook 모드는 source evidence를 기록한 뒤 분리된
ingestion worker를 시작하므로, 일반적인 사용자 흐름이 memory judge 때문에 오래
막히지 않도록 설계되어 있습니다.

```text
Memory Ingestion

┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Stop         │──▶│ Capture source   │──▶│ Source ledger    │
│ turn ended   │   │ evidence         │   │ append-only      │
└──────────────┘   └──────────────────┘   └────────┬─────────┘
                                                   │
                                                   ▼
┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ SQLite index │◀──│ Markdown memory  │◀──│ Judge + resolver │
│ derived data │   │ source of truth  │   │ durable only     │
└──────────────┘   └──────────────────┘   └──────────────────┘
```

메모리 저장은 evidence-first 방식입니다.

- 턴 종료 시점의 source evidence를 프로젝트 ledger에 기록합니다.
- 분리된 judge가 그 근거에서 오래 유지될 메모리만 추출합니다.
- resolver가 중복, 충돌, lifecycle 상태, 링크를 정리합니다.
- Markdown memory artifact가 source of truth입니다.
- SQLite, FTS, vector embedding, telemetry는 파생 데이터입니다.

## 자주 쓰는 명령

```bash
memassist status
memassist memory list
memassist memory pending
memassist memory search "refresh token policy"
memassist memory pack "how should I test auth changes?"
memassist embedding profiles
memassist gui
```

유지보수 명령입니다.

```bash
memassist memory rebuild-index
memassist embedding install --profile local-default
memassist embedding build --profile local-default
memassist memory cleanup
memassist doctor --json
```

## Embedding Profiles

기본 profile은 `local-default`입니다.

- provider: `model2vec`
- model: `minishlab/potion-multilingual-128M`
- quantization: `int8`
- dimension: `256`
- cache: `.memassist/models/local-default`

profile 설정은 `.memassist/embedding-profiles.yaml`에 저장됩니다.

```bash
memassist embedding profiles
memassist embedding activate local-default
memassist embedding install --profile local-default
memassist embedding build --profile local-default
```

활성 profile을 한 번 정하면 일반 메모리 검색은 그 profile을 자동으로 사용합니다.

## 저장 위치

프로젝트 로컬 데이터는 `.memassist/` 아래에 있습니다.

| 경로 | 역할 |
| --- | --- |
| `.memassist/memories/active/` | 활성 Markdown 메모리 |
| `.memassist/memories/candidates/` | 후보 메모리 |
| `.memassist/memories/archived/` | 보관 메모리 |
| `.memassist/sources.jsonl` | append-only source evidence ledger |
| `.memassist/memassist.db` | 파생 index, embedding, trace, telemetry |
| `.memassist/embedding-profiles.yaml` | 로컬 embedding profile 설정 |
| `.memassist/ignore` | memassist가 기록하지 않을 경로 |

지속되는 메모리 artifact는 Markdown 파일입니다. 데이터베이스는 필요하면 다시 만들 수
있는 파생 데이터입니다.

## 개발

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
openspec validate --all --strict
git diff --check
```

선택형 embedding backend입니다.

```bash
python3 -m pip install -e ".[sentence-transformers]"
python3 -m pip install -e ".[flagembedding]"
python3 -m pip install -e ".[embeddings]"
```

## 상태

`memassist`는 초기 단계의 local-first 도구입니다. 핵심 계약은 단순합니다.
사용자가 제공한 메모리 맥락을 잃지 않고 저장하고, 이후 관련 요청에서 탐색해 활성
코딩 에이전트의 컨텍스트로 주입합니다.
