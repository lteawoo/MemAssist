## Context

The current memassist GUI is a single read-only stdlib HTTP server with inline HTML, CSS, and JavaScript in `src/memassist/gui.py`. Its memory table currently labels `importance / confidence` as `Score`, but those values are only two of the signals used by memory retrieval and lifecycle behavior.

The memory model already stores behavior-relevant signals: `status`, `importance`, `confidence`, `strength`, `recurrence`, `retrieval_count`, and `utility`. Retrieval also distinguishes query relevance from baseline memory priority, so the GUI should show those as separate concepts.

## Goals / Non-Goals

**Goals:**

- Replace the misleading `Score` display with behavior-aligned `Priority` and search-time `Relevance` metrics.
- Explain each displayed metric with keyboard-accessible and hover-accessible help.
- Add a minimal Korean/English localization layer for the GUI without adding a frontend framework or runtime dependency.
- Preserve read-only GUI behavior and localhost-only serving.

**Non-Goals:**

- Do not change memory storage schema or retrieval semantics.
- Do not make GUI viewing increment `retrieval_count`, `utility`, `strength`, or `last_used_at`.
- Do not add editing, approving, deleting, or policy promotion actions in the GUI.
- Do not introduce a build step, package manager, or external i18n library.

## Decisions

### 1. Define `Priority` as a composite existing-signal score

`Priority` will be a computed 0-100 display metric derived from persisted memory fields:

- status weight
- `strength`
- `utility`
- `confidence`
- `importance`
- normalized `recurrence`
- normalized `retrieval_count`

The exact implementation can tune weights, but it must keep the score explainable and deterministic. A practical starting point is:

```text
priority = 100 * (
  status_weight * 0.22
  + strength * 0.20
  + utility * 0.16
  + confidence * 0.16
  + importance * 0.14
  + min(recurrence / 5, 1) * 0.07
  + min(log1p(retrieval_count) / log1p(20), 1) * 0.05
)
```

Rationale: this makes the displayed ranking reflect the same classes of signals that retrieval and lifecycle already maintain, without mutating those signals during GUI reads.

Alternative considered: map `Priority` directly to `importance`. That would be simpler but would repeat the current problem: the main number would ignore actual lifecycle/retrieval strength and usage signals.

### 2. Show `Relevance` only in search context

When the memory list has a non-empty `q` search parameter, the displayed metric will change from `Priority` to `Relevance`. `Relevance` will combine query match strength with the memory priority baseline.

Rationale: relevance is query-dependent. Showing it without a query would imply a precision that does not exist.

Alternative considered: always show both `Priority` and `Relevance`. That adds noise to the default browsing view and makes the table less scannable.

### 3. Keep metrics read-only and server-computed

The API will include computed metric fields in the read-only memory response. It will not call `Store.search_memories()` or any method that touches usage counters.

Rationale: the GUI must remain a viewer. Computing metrics server-side keeps table sorting and display testable from API responses.

Alternative considered: compute everything in JavaScript. That would reduce Python changes but would make API tests less meaningful and duplicate scoring logic in the browser only.

### 4. Add tooltip copy as localized structured data

Each metric label will include a small `?` help control. Tooltip content will come from the same localization dictionary as visible labels.

Rationale: metric names are compact, but the meanings are product-specific. Tooltips let the table stay dense while keeping the score auditable.

Alternative considered: add explanatory text blocks in the page. That would make the GUI feel like documentation rather than a working memory dashboard.

### 5. Use an inline localization dictionary

The GUI will start with a simple in-page dictionary:

- locale keys: `en`, `ko`
- translated visible labels and tooltip strings
- locale selector in the header
- selected locale saved in `localStorage`
- `document.documentElement.lang` updated on selection

Rationale: the current GUI has no frontend framework or asset pipeline. An inline dictionary is enough to start Korean/English support while preserving the small stdlib-only surface.

Alternative considered: add a Python or JavaScript i18n dependency. That is unnecessary for a two-locale, single-page GUI.

## Risks / Trade-offs

- [Risk] Users may treat `Priority` as an absolute quality score. → Mitigation: tooltip copy must explain that it is a ranking/attention score derived from memory signals, not a correctness guarantee.
- [Risk] Query relevance may not exactly match future retrieval algorithms. → Mitigation: define it as a GUI search relevance score and keep it based on explicit query match plus priority baseline.
- [Risk] Localization may drift as new strings are added. → Mitigation: tests should assert that touched visible labels have both `en` and `ko` entries.
- [Risk] More metrics could clutter the table. → Mitigation: show one primary metric and place supporting signals in a compact evidence line.

## Migration Plan

1. Add computed metric fields to GUI memory API responses without changing stored data.
2. Update table rendering to use `Priority` by default and `Relevance` during search.
3. Add metric help controls and localized tooltip copy.
4. Add language selector, `localStorage` persistence, and `html lang` updates.
5. Extend GUI tests for metric fields, metric labels, tooltips, localization keys, and read-only behavior.

Rollback is straightforward: revert the GUI display/API metric additions and localization dictionary. No database migration is required.

## Open Questions

- Should the default locale follow browser language or always start with English? The proposed default is browser Korean detection (`ko*`) otherwise English, with persisted user choice taking precedence.
- Should `Priority` be rounded to a whole number or one decimal place? The proposed display is a whole 0-100 integer for scanability.
