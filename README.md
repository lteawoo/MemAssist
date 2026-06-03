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

## Trace and learning loop

After a Codex run, inspect the latest traced session:

```bash
PYTHONPATH=src python3 -m memassist session latest --json
PYTHONPATH=src python3 -m memassist verify --session latest --json
PYTHONPATH=src python3 -m memassist memory candidates --session latest --json
```

`Stop` hooks store extracted memories as `draft` records. Review them before
promoting them to active memory:

```bash
PYTHONPATH=src python3 -m memassist memory list --all
PYTHONPATH=src python3 -m memassist memory review
PYTHONPATH=src python3 -m memassist memory approve <memory-id>
PYTHONPATH=src python3 -m memassist memory reject <memory-id>
PYTHONPATH=src python3 -m memassist memory cleanup
```

Turn a traced session into a draft lesson, promote that lesson into project
policy, and run the lightweight evaluation loop:

```bash
PYTHONPATH=src python3 -m memassist lesson from-session latest --feedback "What should be remembered"
PYTHONPATH=src python3 -m memassist policy promote <memory-id> --protected-path src/auth/refresh-token-policy.ts
PYTHONPATH=src python3 -m memassist eval run --session latest --json
```
