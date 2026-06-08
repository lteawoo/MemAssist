# memassist

Project-local memory RAG for AI coding agents.

`memassist` stores durable project memories from user preferences, decisions,
workflows, and project context. On the next related request, it retrieves those
memories and injects them into Codex, Claude Code, OpenCode, or another active
coding agent.

Memory is context, not policy. `memassist` does not block tools or replace the
agent's judgment.

[한국어 README](README.ko.md)

## Why

- Stop repeating project preferences, test commands, review rules, and decisions.
- Keep memory local to the project and readable as Markdown.
- Preserve the source evidence that made a memory true.
- Retrieve only relevant memories for the current request.

## Install

```bash
python3 -m pip install -e .
```

Initialize a project:

```bash
memassist init --tools codex
memassist doctor
```

`memassist init` creates `.memassist/`, writes project configuration, installs the
requested tool integration, and prepares the active local embedding model. Use
`--skip-embedding-install` only when you explicitly want to defer that download.

Supported integrations:

| Tool | Project file |
| --- | --- |
| Codex | `.codex/hooks.json` |
| Claude Code | `.claude/settings.json` |
| OpenCode | `.opencode/plugins/memassist.js` |

Install more tools when needed:

```bash
memassist init --tools codex,claude,opencode
memassist init --tools all
memassist tools status --json
```

## Core Flow

`memassist` has separate read and write paths.

### Retrieval And Injection

Runs when a new user prompt is submitted.

```text
Retrieval & Injection

┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ UserPromptSubmit │──▶│ Build query      │──▶│ Hybrid search    │
│ new prompt       │   │ prompt + context │   │ BM25 + cosine    │
└──────────────────┘   └──────────────────┘   └────────┬─────────┘
                                                        │
                                                        ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Agent decides    │◀──│ Inject context   │◀──│ Rank memory pack │
│ what to do       │   │ to active agent  │   │ for this request │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

Hybrid search combines:

| Signal | Source |
| --- | --- |
| Lexical | SQLite FTS5 BM25 ranking over chunked memory text |
| Vector | active local embedding profile with cosine similarity over memory chunks |
| Structure | tags, paths, links, status, and chunk type |
| Fusion | Reciprocal Rank Fusion (RRF-style) across lexical, vector, metadata, and link/path channels |
| Usage | `retrieval_count`, `last_used_at`, `utility`, `strength` |

The injected context is a memory pack for the current request. The agent still
decides how to use it.

### Memory Ingestion

Runs after a turn ends. The default stop-hook mode records source evidence and
starts a detached ingestion worker so normal agent flow is not held by memory
judging.

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

Memory ingestion is evidence-first:

- Source evidence is captured at turn end and recorded in the project ledger.
- An isolated judge extracts durable memory content from that evidence.
- The resolver handles duplicates, conflicts, lifecycle state, and links.
- Markdown memory artifacts are the source of truth.
- SQLite, FTS, vector embeddings, and telemetry are derived data.

## Memory Lifecycle

Memories live in three Markdown artifact buckets. The buckets are lifecycle
states, not separate stores: `active/` feeds later retrieval, `candidates/`
holds uncertain or conflicting memories for review, and `archived/` keeps
history out of normal search and injection. SQLite mirrors these artifacts only
as a rebuildable index/cache.

```text
Placement

+----------------+   accepted / clean             +----------------+
|   Stop turn    | -----------------------------> |    active/     |
| judge/resolver |                                | search target  |
+----------------+                                | injected later |
        |                                         +----------------+
        |
        | held / uncertain / conflict
        v
+----------------+
|  candidates/   |
| review queue   |
| not injected   |
+----------------+

+----------------+   memassist memory add         +----------------+
|  manual add    | -----------------------------> |    active/     |
+----------------+                                +----------------+

Movement

+----------------+   memassist memory activate    +----------------+
|  candidates/   | -----------------------------> |    active/     |
+----------------+                                +--------+-------+
        |                                                  |
        | memassist memory deactivate                      |
        v                                                  |
+----------------+                                         |
|   archived/    | <---------------------------------------+
| retained       |   deactivate / cleanup / superseded
| excluded       |
+----------------+
```

## Daily Commands

```bash
memassist status
memassist memory list
memassist memory pending
memassist memory search "refresh token policy"
memassist memory pack "how should I test auth changes?"
memassist embedding profiles
memassist gui
```

Useful maintenance commands:

```bash
memassist memory rebuild-index
memassist embedding install --profile local-default
memassist embedding build --profile local-default
memassist memory cleanup
memassist doctor --json
```

## Embedding Profiles

The default profile is `local-default`:

- provider: `model2vec`
- model: `minishlab/potion-multilingual-128M`
- quantization: `int8`
- dimension: `256`
- cache: `.memassist/models/local-default`

Profiles live in `.memassist/embedding-profiles.yaml`.

```bash
memassist embedding profiles
memassist embedding activate local-default
memassist embedding install --profile local-default
memassist embedding build --profile local-default
```

> If a TLS-intercepting proxy blocks the download with a certificate error, you can disable verification with `memassist embedding install --insecure` (or `memassist init --insecure-embedding-install`). This exposes downloads to MITM tampering, so use it only in trusted environments — prefer pointing `REQUESTS_CA_BUNDLE` at your corporate CA instead of disabling verification.

Set the active profile once, then normal memory retrieval uses it automatically.

## Storage

All project-local data lives under `.memassist/`.

| Path | Purpose |
| --- | --- |
| `.memassist/memories/active/` | active Markdown memories |
| `.memassist/memories/candidates/` | pending memory candidates |
| `.memassist/memories/archived/` | archived memories |
| `.memassist/sources.jsonl` | append-only source evidence ledger |
| `.memassist/memassist.db` | derived index, embeddings, trace, telemetry |
| `.memassist/embedding-profiles.yaml` | local embedding profile configuration |
| `.memassist/ignore` | paths memassist should not record |

The Markdown files are the durable memory artifacts. The database can be rebuilt
from them when needed.

## Development

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
openspec validate --all --strict
git diff --check
```

Optional embedding backends:

```bash
python3 -m pip install -e ".[sentence-transformers]"
python3 -m pip install -e ".[flagembedding]"
python3 -m pip install -e ".[embeddings]"
```

## Status

`memassist` is an early local-first tool. The core contract is simple: preserve
user-provided memory context, retrieve it later, and inject it into the active
coding agent without turning memory into hidden policy.
