# ADR 0010: Advanced settings generated from the parameter schemas

Date: 2026-10-13 - Status: accepted

## Context

Requirement: every setting can be changed in the GUI, and only deviations are stored per project. The map graph
has about 390 node parameters (in about 140 nodes, some of them in two instances of a subgraph: the draft and the
final terrain) plus large preset values (climate look, industries, crops, texts of the mods). New nodes and
parameters keep coming; the GUI must not need a change for each of them: new settings must be easy to add.

## Decision

- **Entries from the compiled graph.** The compiler records parameter slots (`ExecutionPlan.slots`): which value of
  a parameter set (`[params]`, `[nodes."<path>"]` of a node or of a subgraph instance) reaches which node
  parameters. A node parameter belongs to the highest slot that sets it, so a value exposed by the graph (e.g.
  `water_sink_m` for three nodes) is one entry. The same node in several instances of a subgraph with the same value
  is one entry writing all of them.
- **Forms from the JSON schemas** (`app/gui/forms.py`): field kinds as strategies (choice, scalars with unit and
  limits, lists of plain values, variants, objects, tables with free keys, JSON text for the rest). Objects get one
  field per key, so a single value inside a preset (`climate.heights.tree_line`) is changed and stored on its own.
  A new kind of value = one `FieldKind` plus a renderer of the same name in `static/advanced.js`.
- **Layout as configuration** (`configs/gui.toml [advanced]`): sections by node type patterns, hidden parameters by
  patterns (file names, preview pictures), the exposed parameters the main page edits. Unmatched parameters go to
  "Other", so nothing is lost.
- **What is stored where:** values shown are those of the look and the main page (the project compiled without its
  own changes); a change goes to `[defaults]` for preset values (tables merge key by key with the look's), to
  `[params]` for other exposed parameters, to `[nodes."<path>"]` otherwise. A change equal to the look's value is
  removed.
- **Tables merge key by key at every layer of a compile** (document value < exposed parameter < node path), like the
  layers of parameter sets - required so that a stored single value does not replace its whole table.

## Alternatives

- **A curated list of parameters per section** (the legacy GUI) - every new parameter needs a line; easy to forget.
- **Hand-written forms** - the opposite of "easy to add".
- **Large preset values only as files** - the climate look is the part users most want to tune.

## Consequences

- A new node or parameter appears in the GUI by itself, with the description, unit and limits of its schema; only
  a new node type prefix may want a section pattern (otherwise it shows under "Other").
- Keys of free tables (crop shares, OSM tag maps) cannot be removed by a change - the merge keeps the look's keys
  (set a share to 0 instead); lists of tables are edited as JSON text and stored whole. Values of the look that are
  `null` cannot be stored as a change of a table entry in TOML (TOML has no null); a nullable number left empty means
  "the look's value".
- Grouped instances (draft/final) read the change of the first instance; a project that changed only one of them
  shows that change for both and writing stores it in both.
