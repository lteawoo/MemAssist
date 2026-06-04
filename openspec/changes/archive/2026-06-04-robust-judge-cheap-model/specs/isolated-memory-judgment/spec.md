## ADDED Requirements

### Requirement: memassist SHALL tolerate code-fenced or prose-wrapped judge JSON

memassist SHALL extract the judge JSON object from backend output even when the object is wrapped in a markdown code fence or surrounded by prose. Extraction MUST apply to the inner string fields of a CLI result envelope (such as `result`, `text`, or `item.text`), not only to the outermost output, and MUST prefer an object containing `should_store`.

#### Scenario: Fenced JSON in a result envelope is parsed

- **WHEN** a judge backend returns an envelope whose `result` field is a string containing a ```json fenced judgment object
- **THEN** memassist SHALL parse the inner judgment object
- **AND** memassist SHALL NOT return the outer envelope as the judgment

#### Scenario: Clean and JSONL outputs still parse

- **WHEN** a judge backend returns a clean JSON object, or a JSONL stream whose item text holds the object
- **THEN** memassist SHALL parse the judgment object as before

### Requirement: memassist SHALL run the Claude judge with a configurable low-cost model

memassist SHALL invoke the Claude judge backend with a low-cost model by default, and SHALL allow the model to be overridden by environment configuration. The Codex backend SHALL be unaffected.

#### Scenario: Default low-cost model

- **WHEN** memassist invokes the Claude judge without a model override configured
- **THEN** the judge process SHALL be launched with a low-cost default model

#### Scenario: Model override is honored

- **WHEN** a model override is configured via the judge model environment variable
- **THEN** the Claude judge process SHALL be launched with the configured model
