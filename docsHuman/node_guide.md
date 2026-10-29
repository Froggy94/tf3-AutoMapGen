# Node guide

For developers who write nodes, graphs or parameter sets. The ideas behind it: [architecture.md](architecture.md);
where the code lives: [../docsAI/code_map.md](../docsAI/code_map.md).

## 1. Writing a node

```python
from automapgen.graph import NodeContext, NodeParams, node, param

class BelowParams(NodeParams):
    limit: float = param(1000.0, unit="m")
    """Upper limit; pixels below it are set."""

@node("mask.below", output="mask")
def below(value: Field, params: BelowParams, context: NodeContext) -> Mask:
    """Pixels whose value is below the limit."""
    ...
```

- Inputs: arguments annotated with a data type - `T` (one link), `T | None = None` (optional), `tuple[T, ...]` (any
  number of links, in link order).
- `NodeParams` argument: parameters; `NodeContext` argument: log, progress, cancellation.
- Outputs: return a data type (one output, named by `output`), a `NamedTuple` of data types (several) or `None`
  (only with `side_effects=True`).
- `version` + `migrations={old_version: function}` for incompatible parameter changes.
- Mistakes in a declaration are `NodeDefinitionError`s when the catalog is built - not at run time.
- Real metres to game metres: distances with `MapFormat.game_per_real`, heights (elevations, depths, slopes as height
  per pixel) with `MapFormat.height_game_per_real`; the two differ when a map has its own height scale
  ([ADR 0019](../docsAI/adr/0019-heights-and-distances-two-factors.md)).

Data types: `@data_type("mask", "description", codec=..., preview=..., fingerprint=...)` on a frozen class. A data
type whose class derives from another data type's class fits that type's ports (`river_plan` is a `json_document`,
so `file.json` writes it). The codec
lets the disk cache store values; the preview renders a PNG or text for debugging and the editor; the fingerprint
identifies graph inputs for cache keys.

## 2. Graph documents (`<id>.graph.json`)

```json
{
  "$schema": "../schemas/graph.schema.json",
  "id": "forest_mask",
  "inputs":  {"height": "field", "landcover": "labels"},
  "outputs": {"forest": "mask"},
  "params":  {"tree_line_m": {"to": ["below_tree_line.limit"], "value": 1800}},
  "nodes":   {"below_tree_line": {"type": "mask.below", "note": "..."}},
  "links":   [["inputs.height", "below_tree_line.value"], ["below_tree_line.mask", "outputs.forest"]],
  "ui":      {"below_tree_line": {"x": 40, "y": 80}}
}
```

- `inputs`/`outputs`: the graph's interface; in links they are the reserved nodes `inputs.<name>`/`outputs.<name>`.
- `params`: exposed parameters - one name sets one or more node parameters; `value` optional.
- `"type": "graph:<id>"`: a subgraph used as one node; its ports are that graph's inputs/outputs, its parameters that
  graph's exposed parameters.
- `extends` + `nodes` (new or replacing, links stay), `remove` (nodes with their links), `links` (added), `unlink`.
- `ui` is for the editor only; `note` fields replace comments (JSON has none).
- Written stably (`GraphFile.dumps`): same document = same text; defaults are omitted.

JSON because editors work natively with it, reading and writing is lossless and a JSON schema
(`automapgen schema`) gives validation and completion (ADR 0002).

## 3. Parameter sets (TOML)

```toml
graph = "alpine_map"                 # optional, checked
extends = ["../presets/alpine_realistic.toml"]   # optional: sets below this one, relative to this file
[params]                             # exposed parameters of the graph
tree_line_m = 1900
[nodes."forest/below_tree_line"]     # one node, path through subgraph instances
limit = 1850
```

Layers in order (e.g. preset, project, GUI run), later wins; a file names its lower layers with `extends`. Tables are
merged key by key, so a set that extends another names only what differs, down to one value inside a table (a preset
"Alpine South" = "Alpine Realistic" with other materials on a few surfaces); other values, lists too, are replaced. A node
parameter's value: node default < document < `defaults` of the sets < exposed parameter < node path in the parameter
set - tables merged key by key at each of these layers too, so `[nodes."draft/water"] banks = { passes = 3 }` changes
one value of `banks`. Unknown names in `params` and `nodes` are problems. Paths inside parameter values are relative to the working
directory (the project folder).

**Presets use `[defaults]`:** named values (`forest_classes`, `biome_limits_m`, `crops` ...) that a graph takes where it
exposes a parameter of that name and that other graphs ignore. A preset therefore never names inner nodes of a graph:
graph variants (OSM fields vs. parcels) wire the same preset value to different nodes (found in N4: node paths in the
preset broke as soon as a variant rebuilt the cropland).

Where they live: `configs/presets/<preset>.toml` (values of a climate preset, e.g. biome bands, landcover classes of
the import layers, `climate` = the look of the climate mod), `configs/landscapes/<landscape>.toml` (a preset plus a
`transition` to a second look, whose preset is given as its differences to the first),
`configs/projects/<project>.toml` (a project = one map: `map_name`, region, map format, changed settings, `extends`
its preset or landscape; `automapgen run --project <id>`, the map name is the file prefix). A preset extends the values
it shares with others: `configs/industries/<climate>.toml`, `configs/publishing.toml` (names, author, images of the
published mods), `configs/mod_texts/<texts>.toml` (texts and picture of the climate mod), `configs/waterfall.toml`
(waterfall brushes), `configs/map_mods.toml` (the map's own mods: settings, titles and texts; the map's name in them
is the project's `map_name`). Pictures of the published mods (the author's own) are in `assets/pictures/`; paths in
the configs are relative to the working directory. Optional parts of a value
are switched off with `enabled = false` (their values stay in the file) or left out. A set that extends
another may be made for another graph (its own `graph` wins), e.g. `golden_test_parcels.toml` (graph `map_parcels`)
extends `golden_test.toml` (graph `map`).

## 4. Compilation

`GraphCompiler.compile(document, parameters)`:
1. resolve `extends` (recursively);
2. dissolve subgraphs into node paths (`forest/below`), wiring their borders through aliases;
3. migrate old node parameter versions, merge parameter values, validate them against the node schemas;
4. check links: endpoints exist, types fit, inputs get the right number of links, outputs are set exactly once;
5. order producers before consumers (document order otherwise), reject cycles;
6. raise one `GraphError` with every problem, or return the `ExecutionPlan`.

## 5. Execution

`Engine(cache, listener, previews).run(plan, inputs, cancel)`:
- **Lineage keys:** each output's key = hash(node type, version, code hash of the function, parameters, keys of its
  inputs); graph inputs by fingerprint. Keys are known before anything runs.
- **Only what is needed runs:** producers of the graph outputs and side-effect nodes, stopping at cached results.
  After a parameter change only the affected nodes run again. A run can name **targets** (node ids, e.g. the sinks of
  the processing steps ticked in the GUI); then only these nodes and their producers run.
- **Memory:** a value is dropped after its last consumer has run; large results can live in the `DiskCache`.
- **Observability:** `RunListener` (started/finished/messages/progress), `PreviewWriter` (PNG/text of node outputs),
  `CancelToken` (between nodes and inside long nodes), `NodeFailedError` naming the node.
- **Run settings:** `OutputTarget(folder, prefix)` - where sinks write; nodes ask `context.output_file(name)`. The
  graph names files only, so the same graph writes any project into any folder. `downloads` - where sources keep
  downloaded data between runs (`context.download_folder()`, e.g. OSM region files). `game` - the game's files
  (`context.game_folder()`; CLI `--game` or `local/settings.toml`, never in git, ADR 0005). Named destinations of
  the `OutputTarget` receive copies of chosen files: a sink's `copy_to` names one, the run says which folder it is or
  has none (no copy); the CLI maps the game's user data folder (`--user-data`, `local/settings.toml`
  `user_data_folder`, `--no-copy`) to `heightmaps`, `biomes`, `towns_industries`, `staging_area` (ADR 0008).
  `secrets` - named secrets of this computer such as API keys (`context.secret(name)`; CLI: table `[secrets]` of
  `local/settings.toml`); never parameters, never in cache keys (ADR 0009).
- **A value is the same fresh and after its codec:** what flows between nodes must behave exactly like what the cache
  (or a file) gives back. JSON documents therefore normalise their data on creation (Python numbers instead of NumPy
  scalars). Found in N5c: NumPy 2 computes `float32 array - np.float64` in float64 but `float32 array - float` in
  float32 - the heightmap differed by one grey value in 146 pixels between a fresh and a cached run.

## 6. Publishing constraints

- Original game files are never in git; access only through a source port (`GameFiles`, the run setting `game`:
  the installation or a local copy with the layout of `base/content` - a setting, not a rebuild; the installation's
  `climates.zip` equals the legacy project's copy byte for byte). Unit tests use own small sample files (ADR 0005).
- Data licenses (OSM ODbL, ESA WorldCover CC BY 4.0, elevation sources): attributions are added to generated output.
- Local paths and API keys only in user settings outside the repository.
