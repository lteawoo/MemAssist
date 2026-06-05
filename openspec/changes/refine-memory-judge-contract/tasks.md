## 1. Contract Definition

- [x] 1.1 Confirm final V2 field names and enum values for `source_integrity`
- [x] 1.2 Document the V2 judge JSON schema and examples
- [x] 1.3 Identify V1 fields, code paths, and tests to remove rather than preserve

## 2. Judge Input And Output

- [x] 2.1 Update judge instruction to request V2 output
- [x] 2.2 Remove `existing_conflicts` from judge payload construction
- [x] 2.3 Replace V1 output parsing with V2 parsing
- [x] 2.4 Add parser tests for raw JSON, Codex JSONL, Claude result envelope, and fenced JSON

## 3. Storage And Policy

- [x] 3.1 Add an activation policy function that decides active/candidate/rejected after extraction
- [x] 3.2 Remove `caution_level` from the judge contract and any now-obsolete contract behavior
- [x] 3.3 Remove judge-created memory path handling
- [x] 3.4 Preserve `source_quote`, `reason`, and source ledger references in artifacts and lifecycle events
- [x] 3.5 Remove dead code, obsolete compatibility branches, and comments that only describe removed behavior

## 4. Deferred Conflict Resolution Boundary

- [x] 4.1 Remove `existing_conflicts` from judge payload construction
- [x] 4.2 Keep enhanced conflict retrieval and relation classification out of this change
- [x] 4.3 Create follow-up notes for RAG-backed conflict retrieval using the existing memory search system

## 5. Retrieval And Display

- [x] 5.1 Add tests that semantic classification does not depend on language-specific keyword heuristics
- [x] 5.2 Update GUI/API labels for source integrity where exposed
- [x] 5.3 Decide whether path relevance is retrieval-time only or backed by a dynamic association table in a follow-up change

## 6. Verification

- [x] 6.1 Update E2E tests for Stop hook memory extraction with V2 fixture output
- [x] 6.2 Update eval fixtures and expected outputs
- [x] 6.3 Remove tests that only assert removed V1 compatibility or obsolete behavior
- [x] 6.4 Run `PYTHONPATH=src python3 -m unittest discover -s tests`
- [x] 6.5 Run relevant `memassist eval` commands
