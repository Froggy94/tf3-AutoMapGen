# ADR 0002: Graph documents in JSON, parameter sets in TOML

Date: 2026-10-12 - Status: accepted

## Context

Graphs will mostly be written by a graph editor later, but must be readable and editable by hand now; parameter sets
are maintained by hand and need comments. Both must give clean git diffs.

## Decision

- Graph documents: **JSON** with a JSON schema (`automapgen schema`), `note` fields instead of comments, editor-only
  data in `ui`, stable writing (same document = same text, defaults omitted).
- Parameter sets: **TOML** (as in the legacy project). Tools that write parameters write their own override files so
  hand-written comments stay untouched.

## Alternatives

| Format | For | Against |
|---|---|---|
| TOML for graphs | consistent, comments | nested graphs and link lists get unwieldy; writers lose comments and layout |
| YAML | readable, comments | implicit typing pitfalls (`no` -> false); comments survive only with extra libraries |
| JSON5 / JSONC | JSON with comments | weak Python support; editors write plain JSON back, comments get lost |
| Python code as composition | IDE support, refactoring | no graphical editor; users must program; composition would not be data |

## Consequences

Editor libraries (LiteGraph.js, Rete.js, React Flow) can load and save graphs directly; IDEs validate graph files via
`$schema`. JSON has no comments, so explanations go into `note` and `description` fields.
