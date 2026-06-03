## 1. Metric Model

- [x] 1.1 Add read-only GUI helpers that compute deterministic `priority` from status, strength, utility, confidence, importance, recurrence, and retrieval count.
- [x] 1.2 Add read-only GUI helpers that compute query-dependent `relevance` from search match signals plus the priority baseline.
- [x] 1.3 Include computed metric fields and metric evidence in `/api/memories` responses without mutating memory rows.
- [x] 1.4 Sort default memory rows by priority and searched memory rows by relevance.

## 2. Metric UI

- [x] 2.1 Replace the memory table `Score` label and raw `importance / confidence` display with `Priority` in the default list.
- [x] 2.2 Show `Relevance` as the primary metric label only when the memory search query is non-empty.
- [x] 2.3 Render compact metric evidence for confidence, strength, utility, uses, and recurrence.
- [x] 2.4 Add `?` help controls for primary metrics and supporting metric labels with hover and keyboard focus behavior.

## 3. Localization

- [x] 3.1 Add an inline English/Korean translation dictionary for touched visible GUI strings and tooltip copy.
- [x] 3.2 Add a locale selector with `en` and `ko` options.
- [x] 3.3 Persist the selected locale in browser local storage and restore it on reload.
- [x] 3.4 Update `document.documentElement.lang` when the active locale changes.
- [x] 3.5 Use Korean as the initial locale when browser language starts with `ko`; otherwise use English.

## 4. Tests

- [x] 4.1 Extend GUI API tests to assert computed priority, relevance, and metric evidence fields.
- [x] 4.2 Extend GUI tests to assert `Score` is no longer exposed as the primary metric label.
- [x] 4.3 Add localization tests for English and Korean translation keys, tooltip copy, and locale persistence behavior where feasible.
- [x] 4.4 Re-run full unittest discovery and existing memassist eval fixtures.

## 5. Browser Verification

- [x] 5.1 Run the GUI against an isolated fixture database and verify the default table shows Priority.
- [x] 5.2 Search memory rows and verify the primary metric changes to Relevance.
- [x] 5.3 Verify tooltip text appears for metric help controls in English and Korean.
- [x] 5.4 Verify locale switching changes visible labels and `html lang` without changing database or project files.
