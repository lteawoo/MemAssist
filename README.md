# memassist

`memassist` is a local memory, trace, and policy helper for AI coding agents.

It keeps project-scoped memories, builds relevant memory packs for agent
context, records tool activity, and evaluates simple policies before or after
tool use.

## MVP commands

```bash
PYTHONPATH=src python3 -m memassist init
PYTHONPATH=src python3 -m memassist status
PYTHONPATH=src python3 -m memassist memory add --type decision --content "Use pnpm for this repo" --tag tooling
PYTHONPATH=src python3 -m memassist memory search "pnpm"
PYTHONPATH=src python3 -m memassist memory pack "login session bug"
PYTHONPATH=src python3 -m memassist policy check --tool shell --command "rm -rf dist"
PYTHONPATH=src python3 -m memassist hooks install codex
```

Set `MEMASSIST_HOME` to override the default `~/.memassist` storage location.

Codex hooks install to the current project's `.codex/hooks.json` by default.
Use `--scope user` only when you intentionally want user-wide hooks in
`~/.codex/hooks.json`.

Project-local Codex hooks run only after the project `.codex` layer and exact
hook definitions are trusted in Codex. If hooks are new or changed, open
`/hooks` in Codex CLI and trust them before relying on trace capture. For
automation, Codex also exposes `--dangerously-bypass-hook-trust`, but persisted
trust is the reliable default for repeated local testing.

## Always-on lifecycle

After a Codex run, inspect the latest traced session:

```bash
PYTHONPATH=src python3 -m memassist session latest --json
PYTHONPATH=src python3 -m memassist verify --session latest --json
PYTHONPATH=src python3 -m memassist memory candidates --session latest --json
```

`Stop` hooks run the memory lifecycle automatically. The lifecycle extracts
memory candidates, classifies risk, stores low-risk workflow or preference
memories as `auto_active`, keeps touched-file evidence as `ephemeral`, and
leaves risky rules as `pending_confirmation`.

Manual review commands remain available for debugging and development, but they
are not the normal user path:

```bash
PYTHONPATH=src python3 -m memassist memory list --all
PYTHONPATH=src python3 -m memassist memory review
PYTHONPATH=src python3 -m memassist memory approve <memory-id>
PYTHONPATH=src python3 -m memassist memory reject <memory-id>
PYTHONPATH=src python3 -m memassist memory cleanup
```

Turn a traced session into a draft lesson, promote that lesson into project
policy, and run the lightweight evaluation loop during development:

```bash
PYTHONPATH=src python3 -m memassist lesson from-session latest --feedback "What should be remembered"
PYTHONPATH=src python3 -m memassist policy promote <memory-id> --protected-path src/auth/refresh-token-policy.ts
PYTHONPATH=src python3 -m memassist eval run --session latest --json
```

Project memory can be shared without exposing the local SQLite database:

```bash
PYTHONPATH=src python3 -m memassist memory export
PYTHONPATH=src python3 -m memassist memory import .memassist/memories.json
```

Imported memories are drafts by default. Review and approve them before they
become active context.

Run one maintenance pass after a Codex session:

```bash
PYTHONPATH=src python3 -m memassist daemon once --session latest --json
```

This stores draft candidates from the latest trace, expires old memories, and
runs the lightweight evaluation check. In normal Codex usage the same lifecycle
is invoked by the `Stop` hook.
