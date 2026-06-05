## Context

memassist extracts durable memory candidates through the isolated memory judge and currently stores them in `store_judge_result`. That function also performs inline duplicate handling: exact content duplicates are reinforced, and uncertain candidates may be suppressed through token-overlap matching in `dedupe.py`.

The memory judge contract now explicitly separates extraction from lifecycle and conflict policy. Conflict handling must therefore become a dedicated project-local stage after extraction and before storage mutation.

## Goals / Non-Goals

**Goals:**
- Introduce a project-local conflict resolver stage between memory extraction and storage mutation.
- Use existing retrieval/index facilities only for conflict candidate discovery, without mutating retrieval usage signals.
- Classify candidate-to-existing relationships through a dedicated relation judge contract, not language-specific keyword or token-overlap heuristics.
- Apply deterministic resolution policy that is testable and auditable.
- Persist relation outcomes through lifecycle events and memory links.
- Keep `source_integrity` as an extraction-quality signal and combine it with relation policy only after extraction.

**Non-Goals:**
- No global or cross-project memory conflict resolution.
- No agent prompt injection of conflict candidates.
- No new memory status beyond `active`, `candidate`, and `archived`.
- No language-specific hardcoded keyword rules for duplicate, contradiction, or supersession.
- No archival of memories outside the same project.

## Decisions

### Decision 1: Resolver runs inside the store-before-mutation path

`store_judge_result` remains the integration point for persisted judge results, but its inline duplicate/suppression blocks will be replaced by a conflict resolver call.

Flow:

```text
MemoryJudgeResult
-> normalize candidate
-> reject contaminated source
-> discover related project-local memories
-> classify relations
-> resolve deterministic storage action
-> apply store mutation
-> record lifecycle event and memory links
```

Alternative considered: run conflict resolution in `process_session_lifecycle`. Rejected because lifecycle cleanup happens after storage and would allow conflicting active memories to be created before policy runs.

### Decision 2: Conflict candidate discovery is project-local and read-only

The resolver SHALL inspect only memories with the same `project_id`. It may include active and candidate memories for relation classification and may inspect archived memories only for exact content duplicate checks. It SHALL NOT call retrieval APIs that mutate `retrieval_count`, `utility`, `last_used_at`, or `strength`.

Alternative considered: reuse `build_memory_pack`. Rejected because it is optimized for agent context injection and marks memories as used through storage search paths.

### Decision 3: Relation judge owns semantic relation classification

The relation judge returns structured relationships between the new candidate and existing project memories:

- `duplicate`
- `complementary`
- `candidate_supersedes`
- `existing_supersedes`
- `conflicts`
- `unrelated`

The system may use deterministic exact content matching as a fast path because it does not classify multilingual meaning. Semantic duplicate, contradiction, and supersession decisions are delegated to the relation judge.

Alternative considered: extend `dedupe.py` token overlap. Rejected because lexical overlap is brittle for Korean, mixed-language prompts, paraphrases, and future extensibility.

### Decision 4: Resolution policy is deterministic

Relation judge output is not allowed to mutate storage directly. A deterministic policy converts `source_integrity` and relation outcomes into one action:

- contaminated source: reject
- exact duplicate or `duplicate`: reinforce existing memory, do not store a new row
- `conflicts`: store candidate only and link conflict
- `candidate_supersedes`: if source is clean, store active and archive same-project existing memory; otherwise store candidate
- `existing_supersedes`: hold candidate or reinforce existing according to source integrity and relation evidence
- `complementary`: store according to source integrity and link complementary memories
- no relevant relation: clean stores active, uncertain stores candidate
- relation judge unavailable while related memories exist: store candidate only

Alternative considered: let the relation judge return lifecycle actions. Rejected because lifecycle policy must remain auditable and testable without changing judge prompts.

### Decision 5: Multiple relations are collapsed by conservative priority

When multiple related memories produce different relation values, policy chooses the highest-priority blocking or replacement action:

```text
conflicts
> candidate_supersedes
> existing_supersedes
> duplicate
> complementary
> unrelated
```

Exact content duplicate remains a pre-judge fast path and returns reinforce immediately.

### Decision 6: Audit records use existing tables

The resolver will record lifecycle decisions in `lifecycle_events`. The existing `memory_links` table will be used for durable relation records by allowing relation values beyond `related`.

No new table is required for the first version.

## Risks / Trade-offs

- Relation judge unavailable or invalid output → Mitigation: store as candidate when related memories exist and record `held_relation_judge_unavailable`.
- Candidate discovery misses a related memory → Mitigation: combine exact match, lexical/index discovery, and optional vector discovery in read-only mode; add focused tests around paraphrase and Korean conflict examples.
- Relation judge returns overbroad conflicts → Mitigation: deterministic policy prevents unsafe active storage but preserves candidate evidence for review.
- Supersession archives the wrong memory → Mitigation: allow archival only within the same project and only for clean candidate sources.
- Memory links grow noisy → Mitigation: only persist links used by final policy decisions, not every retrieval candidate.
