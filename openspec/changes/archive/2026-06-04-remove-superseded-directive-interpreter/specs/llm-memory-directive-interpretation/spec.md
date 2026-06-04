## REMOVED Requirements

### Requirement: memassist SHALL interpret direct user memory directives semantically

**Reason**: The separate LLM directive interpreter is superseded by the isolated memory judge, which already evaluates user prompts for durable memory in `isolated-memory-judgment`. The interpreter is dead code with no live caller.
**Migration**: Durable memory derived from user directives is produced by the isolated memory judge. See `isolated-memory-judgment`.

### Requirement: memassist SHALL return structured directive interpretation results

**Reason**: The interpreter's structured `DirectiveCandidate` output is removed with the interpreter. The judge returns its own structured candidate.
**Migration**: Use the isolated judge's structured output (`should_store`, `memory_content`, `source_quote`, `memory_type`, `enforcement`, `activation`, `candidate_paths`, `meaning_preserved`, `contamination_risk`, `reason`) defined in `isolated-memory-judgment`.

### Requirement: memassist SHALL use the initialized tool as the default LLM interpreter backend

**Reason**: Backend selection now applies to the judge, not a separate interpreter. The judge selects its backend from initialized tools.
**Migration**: The judge selects its backend from initialized tools (codex, then claude) per `isolated-memory-judgment`.

### Requirement: memassist SHALL gate LLM-derived directives before policy activation

**Reason**: Policy compilation from directive output is removed with the interpreter. Memory never auto-compiles into project policy.
**Migration**: Policy enforcement is governed by `memory-derived-enforcement`; judged memories are stored as candidates/active memories and never auto-compiled into `sensitive_paths`/`protected_paths`.

### Requirement: memassist SHALL support deterministic fallback when initialized-tool LLM interpretation is unavailable

**Reason**: The deterministic fallback interpreter is removed. It was unreachable from the live hook flow and produced no production behavior.
**Migration**: When no initialized tool provides a judge adapter, the judge is unavailable and no durable prompt-derived memory is written, per `isolated-memory-judgment`. Heuristic session lifecycle extraction is unaffected.

### Requirement: memassist SHALL verify directive behavior through temp-project E2E tests

**Reason**: The directive interpreter E2E surface is removed along with the interpreter.
**Migration**: Judge behavior is verified by `isolated-memory-judgment` scenarios and the judge test suite.
