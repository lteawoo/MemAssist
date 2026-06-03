# memassist

`memassist` is a local memory assistant for AI coding tools such as Codex,
Claude Code, and OpenCode. It records agent sessions through native tool
integrations, extracts useful memory candidates, keeps project-scoped memory,
retrieves relevant reminders into future prompts, and can turn approved
protective lessons into project policy.

It is designed for coding-agent users who want the assistant to remember
project conventions without pasting the same context into every session.

> Current status: early local tool. The CLI and storage model are usable, but
> you should review generated memories and policy changes before relying on
> them in high-risk projects.

## What It Does

- Records coding-agent tool activity as local trace events.
- Extracts memory candidates from completed sessions.
- Automatically activates low-risk workflow or preference memories.
- Keeps risky or protective inferred memories as inactive candidates.
- Retrieves relevant memories and verification reminders into future agent
  prompts.
- Blocks dangerous shell commands and asks for approval around protected paths.
- Provides session verification and retrieval evaluation commands.
- Exports and imports project memory without sharing the local SQLite database.

## How It Works

```mermaid
flowchart LR
    A[Agent session] --> B[Tool integration records trace events]
    B --> C[Session stop runs lifecycle]
    C --> D[Extract memory candidates]
    D --> E{Risk and usefulness}
    E -->|low-risk workflow or preference| F[auto_active memory]
    E -->|touched-file evidence| G[ephemeral memory]
    E -->|risky or protective| H[inactive candidate]
    E -->|weak signal| I[rejected]
    H --> J[Manual review or ignored]
    F --> M
    M --> N[Relevant context injected into the agent prompt]
```

`memassist` stores data under `~/.memassist` by default. Project configuration
lives in `.memassist/` inside the project directory. A git repository is not
required.

## Installation

From this repository:

```bash
python3 -m pip install -e .
```

During local development, you can also run commands without installing by using
`PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m memassist status
```

Set `MEMASSIST_HOME` if you want to keep the database somewhere other than
`~/.memassist`:

```bash
export MEMASSIST_HOME="$HOME/.local/share/memassist"
```

## Initialize A Project

Run this from the project where the coding agent will work:

```bash
memassist init
memassist status
```

`init` creates:

- `.memassist/policy.yaml` for sensitive paths, protected paths, dangerous
  command patterns, and verification commands.
- `.memassist/ignore` for paths that should not be recorded.
- A project record in the local memassist database.

Install tool integrations in the same step with `--tools`:

```bash
memassist init --tools codex
memassist init --tools codex,claude,opencode
memassist init --tools all
```

Use `--mode` to install only part of the integration surface:

```bash
memassist init --tools codex --mode context
memassist init --tools all --mode full
```

Modes:

| Mode | Behavior |
| --- | --- |
| `full` | Memory context, policy guard, trace capture, and lifecycle processing. |
| `context` | Memory context injection only. |
| `trace` | Tool/session trace capture and lifecycle processing. |
| `guard` | Policy checks before tool execution. |

Check setup health at any time:

```bash
memassist doctor
memassist doctor --json
```

`doctor` checks project config, local storage, installed tool integrations, and
available agent CLIs. Project integrations may still need to be trusted or
enabled in each agent tool before trace capture runs.

## Manage Tool Integrations

Use `tools` after initialization to change integrations:

```bash
memassist tools status
memassist tools install claude
memassist tools uninstall opencode
memassist tools repair all
```

Project integrations are written to the native config location for each tool:

| Tool | Project integration path |
| --- | --- |
| Codex | `.codex/hooks.json` |
| Claude Code | `.claude/settings.json` |
| OpenCode | `.opencode/plugins/memassist.js` |

Use user-wide integrations only when you intentionally want memassist to run
across projects:

```bash
memassist tools install all --scope user
```

The integrations normalize these lifecycle events into the same memassist hook
handler:

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent
    participant M as memassist

    U->>A: Submit prompt
    A->>M: UserPromptSubmit
    M-->>A: Direct instruction result + relevant memory context
    A->>M: PreToolUse
    M-->>A: Policy decision
    A->>M: PostToolUse
    M-->>M: Store trace evidence
    A->>M: Stop
    M-->>M: Verify session and run memory lifecycle
```

Codex requires hook trust. After installing or changing the Codex integration,
open `/hooks` in Codex CLI and trust the project `.codex` layer and exact hook
definitions. Claude Code and OpenCode use their own native config/plugin trust
behavior.

## Automatic Memory Lifecycle

The normal path is automatic:

1. The agent runs with a memassist tool integration enabled.
2. The session stop event records the final state and runs the lifecycle.
3. `memassist` extracts memory candidates from trace events and the final
   assistant message.
4. Low-risk candidates become active immediately.
5. Risky or protective inferred candidates stay inactive unless reviewed.
6. Future prompts receive a small memory pack when the query is relevant.

Current lifecycle statuses include:

| Status | Meaning |
| --- | --- |
| `observed` | A behavior or event was seen in a session trace. |
| `candidate` | The signal may be useful but is not active yet. |
| `auto_active` | A low-risk memory was activated automatically. |
| `long_term` | A repeated high-quality memory was promoted for durable retrieval. |
| `policy_active` | A direct protective instruction was added to project policy and passed simulation. |
| `ephemeral` | Useful trace evidence kept as session context, not a durable rule. |
| `rejected` | The memory was rejected or fell below the automatic threshold. |

Inspect the latest lifecycle state:

```bash
memassist session latest --json
memassist verify --session latest --json
memassist memory candidates --session latest --json
memassist memory list --all
```

## Direct Memory And Policy Instructions

If the user directly gives a memory or policy instruction, memassist treats that
instruction as already approved. For example:

```text
Refresh token changes must ask for my approval before editing.
```

or:

```text
리프레시 토큰 관련 변경은 변경 전에 나의 승인부터 받아야 해.
```

On `UserPromptSubmit`, memassist stores the instruction immediately. If it can
infer a matching project file, it promotes the memory to `policy_active` and
adds the protected path to `.memassist/policy.yaml`. If the path cannot be
inferred, the memory still becomes active so it can be retrieved before related
future work.

Inferred risky or protective candidates are not promoted by short follow-up
responses such as `yes`, `ok`, or `응`. They remain inactive candidates. To turn
one into enforcement, give a direct policy instruction or promote a reviewed
memory with an explicit protected path.

## Policy Protection

`memassist` ships with a small local policy engine. The default policy warns on
sensitive paths, denies dangerous recursive commands, and lets you add protected
paths that require explicit approval.

Example `.memassist/policy.yaml`:

```yaml
sensitive_paths:
  - ".env"
  - ".env.*"
  - "*.pem"
  - "*.key"
  - "*secret*"

protected_paths:
  - "src/auth/refresh-token-policy.ts"

dangerous_commands:
  - "\\brm\\s+-r[f]?\\b"
  - "\\bgit\\s+reset\\s+--hard\\b"
  - "\\bgit\\s+clean\\s+-fd\\b"

verification_commands: []
```

Check policy decisions manually:

```bash
memassist policy check --tool shell --command "rm -rf dist"
memassist policy check --tool apply_patch --command "*** Update File: src/auth/refresh-token-policy.ts"
```

Promote a reviewed memory into protected-path policy:

```bash
memassist policy promote <memory-id> --protected-path src/auth/refresh-token-policy.ts
```

In tool integrations, `require_approval` is mapped to the strongest available
tool-blocking response. Codex and OpenCode deny the tool call with a reason that
explicit user approval is required.

## Retrieval And Evaluation

Build a memory pack for a new prompt:

```bash
memassist memory pack "login session bug"
```

The pack separates memories into:

- `context`: relevant facts, lessons, decisions, and preferences.
- `policy`: rules or memories with warning, approval, or blocking enforcement.
- `verifier`: workflow memories such as test commands.

RAG retrieval is intent-aware and section-aware:

```mermaid
flowchart LR
    A[User prompt] --> B[Query intent]
    B --> C[Lexical channel]
    B --> D[Metadata channel]
    B --> E[Policy channel]
    B --> F[Verifier channel]
    B --> G[Links/path channel]
    C --> H[RRF fusion]
    D --> H
    E --> H
    F --> H
    G --> H
    H --> I[Context / Policy / Verifier pack]
    I --> J[Codex additionalContext]
    J --> K[memory_injected trace event]
```

The deterministic intent analyzer extracts task type, domains, likely paths,
risk level, policy need, verifier need, and derived retrieval queries. Retrieval
then combines lexical, metadata, policy, verifier, and memory-link/path channels
with RRF-style rank fusion. No embedding service or network dependency is
required.

The current implementation is a local memory-pack RAG system, not a general
document QA system. It is optimized for coding-agent reminders:

```mermaid
flowchart TD
    Q[Prompt] --> I[Intent analysis]
    I --> L[Lexical FTS/BM25]
    I --> M[Metadata and path scoring]
    I --> P[Policy channel]
    I --> V[Verifier channel]
    I --> G[Memory links and path expansion]
    L --> F[RRF fusion]
    M --> F
    P --> F
    V --> F
    G --> F
    F --> S[Section assignment]
    S --> C[Context memories]
    S --> R[Policy reminders]
    S --> T[Verification reminders]
    C --> A[Codex additionalContext]
    R --> A
    T --> A
```

The quality target for this repository is a `memassist eval rag` score of 4.5
or higher on seeded, repeatable fixtures. The score is a 5-point weighted
summary:

- `section_accuracy`: expected memories appear in the expected pack section.
- `context_relevance`: expected hits are dense relative to retrieved pack size.
- `policy_leak_rate`: forbidden policy leakage remains zero.
- `verifier_recall`: expected verification workflows are retrieved.
- `pass_rate`: each case satisfies expected and forbidden terms.

Evaluate retrieval quality with expected and forbidden terms:

```bash
memassist eval retrieval \
  --query "session timeout npm verification" \
  --expect "npm test" \
  --forbid "refresh token" \
  --json
```

Retrieval evaluation reports:

- `recall_at_k`
- `precision_at_k`
- `mrr`
- `forbidden_recall_rate`

Use this before depending on automatic memory injection in a project where noisy
or stale memories could mislead Codex.

Evaluate broader memory quality:

```bash
memassist eval memory \
  --query "session timeout npm verification" \
  --expect "npm test" \
  --forbid "refresh token" \
  --json
```

Memory quality evaluation reports:

- `memory_recall`
- `memory_precision`
- `wrong_promotion_rate`
- `wrong_policy_rate`
- `stale_memory_rate`

Example fixture files live under `evals/`:

```bash
memassist eval retrieval --case-file evals/retrieval/basic.json --json
memassist eval memory --case-file evals/memory_quality/basic.json --json
memassist eval rag --case-file evals/rag/basic.json --json
```

RAG evaluation is section-aware. It checks whether expected terms appear in the
right pack section and reports:

- `section_accuracy`
- `context_relevance`
- `policy_leak_rate`
- `verifier_recall`
- `pass_rate`
- `score`

RAG fixture cases may include a `seed` list. These memories are inserted only
for that case and removed before the command exits, so fixture evaluation works
even when the current project has no active memories:

```json
{
  "query": "change session timeout and verify",
  "seed": [
    {
      "section": "context",
      "content": "Session timeout fixes should update the session timeout behavior only.",
      "tags": ["auth", "session", "timeout"],
      "paths": ["src/auth/session.py"]
    },
    {
      "section": "verifier",
      "content": "Run npm test -- auth session after session timeout changes.",
      "tags": ["verification", "test", "auth", "session"]
    }
  ],
  "expect": [
    { "term": "session timeout", "section": "context" },
    { "term": "npm test", "section": "verifier" }
  ],
  "forbid": [
    { "term": "refresh token", "section": "context" }
  ]
}
```

The repository fixture currently passes the target:

```bash
PYTHONPATH=src python3 -m memassist eval rag --case-file evals/rag/basic.json --json
```

Expected headline result:

```json
{
  "passed": true,
  "score": 4.833
}
```

## RAG Improvement Roadmap

The next quality steps are intentionally staged so each can be verified with
fixture evals before it changes prompt injection behavior:

```mermaid
flowchart LR
    A[Seeded eval fixtures] --> B[Score and failure diagnostics]
    B --> C[Contextual memory index text baseline]
    C --> D[Optional dense retrieval channel]
    D --> E[Cross-encoder reranking]
    E --> F[Corrective confidence gate]
    F --> G[Trace-derived production eval set]
```

The planned 4.5+ architecture keeps the current local-first behavior, then adds
optional layers:

- Contextual memory text: index memory content together with section, path,
  status, enforcement, tags, and reason so short memories do not lose meaning.
  A SQLite FTS baseline is implemented.
- Hybrid retrieval: combine sparse BM25 with optional dense embeddings through
  rank fusion.
- Reranking: re-score top retrieval candidates before section assignment.
- Corrective gate: suppress or shrink memory packs when retrieval confidence is
  low or memories conflict.
- Production eval slices: promote real trace failures into repeatable cases.

## Verification And Testing

Verify a traced session:

```bash
memassist verify --session latest
memassist eval run --session latest --json
```

Verification checks trace evidence against visible memories and project policy.
For example, it can flag a session that touched code without an observed test
command, while treating policy-denied protected edits as expected blocks.

Run one maintenance pass manually:

```bash
memassist daemon once --session latest --json
```

This processes memory candidates, expires old memories, and runs lightweight
evaluation checks. In normal Codex usage, the `Stop` hook invokes the same
lifecycle automatically.

Run the repository tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

## Manual Memory Commands

Manual commands are useful for debugging, reviewing, and bootstrapping:

```bash
memassist memory add --type decision --content "Use pnpm for this repo" --tag tooling
memassist memory search "pnpm"
memassist memory list --all
memassist memory review
memassist memory approve <memory-id>
memassist memory reject <memory-id>
memassist memory rollback <memory-id>
memassist memory links <memory-id>
memassist memory cleanup
```

`rollback` rejects a memory and removes its protected path when the memory or
command supplies one. Use it to undo an incorrect `policy_active` promotion.
`links` shows related memories created from shared tags, paths, and content
terms. This is the first lightweight memory-evolution layer; future scoring can
use these links to explain why a memory was retrieved or why an older memory was
superseded.

Export and import project memory:

```bash
memassist memory export
memassist memory import .memassist/memories.json
```

Imported memories are drafts by default. Review and approve them before they
become active context.

## Known Codex CLI Caveat

End-to-end local testing confirmed that interactive Codex CLI sessions can run
the installed lifecycle hooks when the hook definitions are trusted or hook trust
is bypassed.

In Codex CLI `0.136.0`, `codex exec` did not run project lifecycle hooks in
local testing. Use the interactive CLI for hook-based E2E validation until that
behavior is verified in your installed Codex version.

## Storage And Privacy

- Memory and traces are local by default.
- The default database path is under `~/.memassist`.
- Project policy and ignore files live under `.memassist/`.
- Exported memories can be shared as `.memassist/memories.json`; the local
  SQLite database does not need to be shared.
- Add sensitive files or generated directories to `.memassist/ignore` when they
  should not appear in trace-derived memory candidates.

## Korean README

A Korean version is available at [README.ko.md](README.ko.md).

## References

- RAGAS: Automated Evaluation of Retrieval Augmented Generation:
  https://arxiv.org/abs/2309.15217
- RAGAS metrics documentation:
  https://docs.ragas.io/en/v0.3.1/concepts/metrics/available_metrics/
- TruLens RAG Triad:
  https://www.trulens.org/getting_started/core_concepts/rag_triad/
- Anthropic Contextual Retrieval:
  https://www.anthropic.com/engineering/contextual-retrieval
- Corrective Retrieval Augmented Generation:
  https://arxiv.org/abs/2401.15884
- Self-RAG:
  https://arxiv.org/abs/2310.11511
- BGE reranker documentation:
  https://bge-model.com/Introduction/reranker.html
- ColBERTv2:
  https://arxiv.org/abs/2112.01488
- RAPTOR:
  https://arxiv.org/abs/2401.18059
- Microsoft GraphRAG:
  https://microsoft.github.io/graphrag//index/overview/
