## Why

`UserPromptSubmit` currently can interpret and persist memory directives on the hot path, which lets contaminated session context or assistant restatements distort the user's original intent. This surfaced as a `refresh token` directive being remembered as browser/server refresh behavior, changing the subject of the user's instruction.

## What Changes

- Introduce an isolated memory judge that evaluates memory write candidates from minimal source events rather than the current session agent's full context.
- Keep automatic memory ingestion and RAG behavior invisible to users; users do not need to know or operate the memory system explicitly.
- Change `UserPromptSubmit` so it records memory-intent source events and injects retrieved memory, but does not directly create durable directive memories or compile policy.
- Add automatic candidate creation from the isolated judge for explicit user directives, with low-risk memories eligible for activation and policy-like memories staged safely.
- Prevent assistant echo, retrieved memory context, current agent plans, and system/developer prompts from becoming memory source material for the isolated judge.
- Keep session approval grants as a separate hot-path behavior for explicit approval prompts.
- **BREAKING**: Direct user memory directives are no longer compiled into project policy during `UserPromptSubmit`; policy compilation moves to an activation path after isolated judgment.

## Capabilities

### New Capabilities

- `isolated-memory-judgment`: Defines how memassist automatically evaluates source events with an isolated LLM judge and turns them into memory candidates without contaminated context.

### Modified Capabilities

- `memory-derived-enforcement`: Project policy enforcement SHALL be derived from explicit project configuration or activated/judged memory, not from unreviewed hot-path `UserPromptSubmit` interpretation.

## Impact

- Affected modules: hook handling, directive interpretation, memory lifecycle, storage/trace events, retrieval, policy compilation, tests, and documentation.
- Adds an internal judge interface and tool adapter path that can reuse initialized tools while preventing hook recursion.
- Requires migration or cleanup guidance for memories created from assistant echo or distorted semantic subjects.
