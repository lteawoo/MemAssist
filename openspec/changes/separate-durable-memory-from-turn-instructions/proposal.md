## Why

The isolated memory judge currently preserves the full user prompt as the stored memory content, so one-shot turn instructions such as `응답은 OK만 해` can become durable project memory. This weakens retrieval quality because future RAG receives transient response-format instructions alongside the actual long-lived preference.

## What Changes

- Store the judge's durable normalized memory as the primary memory content.
- Preserve the original user wording as source evidence in lifecycle metadata and reason text, not as the retrievable memory body.
- Teach the judge prompt to separate long-lived project preferences/directives from one-shot execution or response-format instructions in the same user prompt.
- Add regression coverage for Korean and English mixed prompts where a durable directive is followed by a transient instruction.
- Add E2E/evaluation checks that fail if transient phrases are retrieved as durable memory content.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `isolated-memory-judgment`: Prompt-derived memories must retain source evidence while storing only the durable portion as retrievable content.
- `llm-memory-directive-interpretation`: Direct user memory directives must separate durable future behavior from one-shot response or execution instructions in the same prompt.

## Impact

- Affected code: `src/memassist/memory_judge.py`, tests around hook ingestion, storage, and retrieval.
- Affected docs: README memory model guidance for source-language preservation versus retrievable durable content.
- No migration is required because there are no existing users to preserve.
