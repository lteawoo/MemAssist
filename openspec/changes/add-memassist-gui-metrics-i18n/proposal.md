## Why

The memassist GUI currently labels raw `importance / confidence` values as `Score`, which makes the number hard to trust because it is not aligned with the memory ranking behavior users care about. The GUI also needs a first localization layer so Korean and English users can understand metric meanings without changing the underlying read-only product surface.

## What Changes

- Replace the ambiguous memory table `Score` display with a real `Priority` metric that is derived from existing memory signals used by memassist behavior.
- Show `Relevance` instead of `Priority` when the user is actively searching, using query match signals plus the same priority baseline.
- Add metric help affordances with `?` tooltips that explain `Priority`, `Relevance`, `Confidence`, `Strength`, `Utility`, `Uses`, and `Recurrence`.
- Add Korean and English GUI localization for visible labels, metric names, empty/loading/error states, and tooltip copy.
- Add a language selector that reflects the selected locale in `html lang` and persists the choice locally.
- Preserve the GUI as a read-only localhost viewer with no write actions, no new runtime dependency, and no change to memory storage semantics.

## Capabilities

### New Capabilities

- `memassist-gui-memory-metrics`: The GUI exposes behavior-aligned memory metrics and explains how each metric should be interpreted.
- `memassist-gui-localization`: The GUI supports Korean and English interface text with a persisted locale selection.

### Modified Capabilities

None. There are no base OpenSpec capabilities in `openspec/specs/` to modify.

## Impact

- Affected code:
  - `src/memassist/gui.py`
  - `tests/test_gui.py`
- Affected UX:
  - Memory table metric column and related labels/tooltips.
  - GUI language selection and all touched visible strings.
- Affected APIs:
  - Read-only GUI JSON may include computed metric fields for display.
- Dependencies:
  - No new runtime dependency planned.
