## ADDED Requirements

### Requirement: Korean and English GUI locales
The GUI SHALL support English (`en`) and Korean (`ko`) translations for visible text introduced or touched by this change, including table labels, filters, buttons, empty states, loading states, error states, metric names, metric evidence labels, and tooltip copy.

#### Scenario: English locale renders translated labels
- **WHEN** the active GUI locale is `en`
- **THEN** visible labels introduced or touched by this change render in English
- **AND** metric tooltips render in English

#### Scenario: Korean locale renders translated labels
- **WHEN** the active GUI locale is `ko`
- **THEN** visible labels introduced or touched by this change render in Korean
- **AND** metric tooltips render in Korean

### Requirement: Locale selector persists choice
The GUI SHALL include a locale selector with English and Korean options. The selected locale MUST be persisted locally and restored on subsequent page loads.

#### Scenario: User switches locale
- **WHEN** the user selects Korean or English from the locale selector
- **THEN** the GUI updates visible localized text without requiring a server restart
- **AND** the selected locale is saved in browser local storage

#### Scenario: Persisted locale is restored
- **WHEN** the user reloads the GUI after selecting a locale
- **THEN** the GUI restores the persisted locale

### Requirement: HTML language reflects active locale
The GUI SHALL set the document `html lang` attribute to the active locale.

#### Scenario: Active locale updates html lang
- **WHEN** the active GUI locale is changed to `ko`
- **THEN** the document `html lang` attribute is `ko`
- **WHEN** the active GUI locale is changed to `en`
- **THEN** the document `html lang` attribute is `en`

### Requirement: Locale fallback is deterministic
The GUI SHALL choose a deterministic locale when no stored locale exists. It MUST use Korean when the browser language starts with `ko`; otherwise it MUST use English.

#### Scenario: No stored locale with Korean browser language
- **WHEN** no locale is stored locally
- **AND** the browser language starts with `ko`
- **THEN** the GUI starts in Korean

#### Scenario: No stored locale with non-Korean browser language
- **WHEN** no locale is stored locally
- **AND** the browser language does not start with `ko`
- **THEN** the GUI starts in English

### Requirement: Localization preserves read-only behavior
Localization controls MUST NOT introduce any server-side mutation, memory mutation, policy mutation, or project configuration mutation.

#### Scenario: Language change is client-local only
- **WHEN** the user changes the GUI locale
- **THEN** no memassist database rows are inserted, updated, or deleted
- **AND** no project `.memassist` files are created or modified
