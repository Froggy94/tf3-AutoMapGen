"""Parameter sets: parameter values from outside a graph document, in layers (e.g. preset, project, GUI run).

A parameter set file (TOML, maintained by hand, comments welcome):

    graph = "alpine_map"                 # optional: the graph the set is made for (checked)
    extends = ["../presets/alpine_realistic.toml"]   # optional: sets below this one (paths relative to this file)

    [defaults]                           # named values for any graph: used where a graph exposes a parameter of
    forest_classes = [10]                # that name, ignored elsewhere (climate presets)

    [params]                             # exposed parameters of the graph (checked: unknown names are errors)
    tree_line_m = 1900

    [nodes."forest/below_tree_line"]     # parameters of one node; the path leads through subgraph instances
    limit = 1850

Later layers win; tables are merged key by key (a preset that extends another names only what differs, down to a
single value inside a table), other values - lists too - are replaced. Within one compile, a node parameter gets: node
default < document value < default of the set < exposed parameter < node path from a parameter set.

`defaults` keep presets independent of the inner structure of graphs: a preset names values ("forest_classes",
"crops"), each graph decides by its exposed parameters which of them it uses and where.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automapgen.graph.errors import GraphError, Problem

type Values = Mapping[str, Any]

_KEYS = ("graph", "extends", "defaults", "params", "nodes")


@dataclass(frozen=True)
class ParameterSet:
    graph: str | None = None
    params: Values = field(default_factory=dict)
    nodes: Mapping[str, Values] = field(default_factory=dict)
    source: str = "parameters"
    defaults: Values = field(default_factory=dict)

    def given_for(self, exposed: Sequence[str]) -> dict[str, Any]:
        """Values of the exposed parameters of a graph: its defaults where exposed, then the checked params."""
        return {**{k: v for k, v in self.defaults.items() if k in exposed}, **self.params}

    @staticmethod
    def load(path: Path) -> ParameterSet:
        """The set in the file on top of the sets it extends (recursively; later wins)."""
        return ParameterSet._load(path.resolve(), ())

    @staticmethod
    def of(data: Values, folder: Path, source: str = "parameters") -> ParameterSet:
        """A set given as data (the content of a file), on top of the sets it extends (relative to `folder`)."""
        return ParameterSet._read(data, folder.resolve() / source, ())

    @staticmethod
    def _load(path: Path, chain: tuple[Path, ...]) -> ParameterSet:
        if path in chain:
            raise GraphError([Problem(path.name, "extends itself: " + " -> ".join(p.name for p in (*chain, path)))])
        if not path.is_file():
            raise GraphError([Problem(chain[-1].name if chain else path.name, f"parameter set {path} not found")])
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise GraphError([Problem(path.name, f"not valid TOML: {e}")]) from e
        return ParameterSet._read(data, path, chain)

    @staticmethod
    def _read(data: Values, path: Path, chain: tuple[Path, ...]) -> ParameterSet:
        unknown = set(data) - set(_KEYS)
        nodes = data.get("nodes", {})
        problems = [Problem(path.name, f"unknown key '{k}' (allowed: {', '.join(_KEYS)})") for k in sorted(unknown)]
        problems += [Problem(path.name, f"nodes.{k} must be a table of parameters") for k, v in nodes.items()
                     if not isinstance(v, dict)]
        if problems:
            raise GraphError(problems)
        own = ParameterSet(data.get("graph"), data.get("params", {}), nodes, path.name, data.get("defaults", {}))
        bases = [ParameterSet._load((path.parent / base).resolve(), (*chain, path)) for base in data.get("extends", [])]
        return ParameterSet._merged([*bases, own], own.source, own.graph)

    @staticmethod
    def layered(sets: Sequence[ParameterSet], graph_id: str) -> ParameterSet:
        """The sets merged in order (later wins); each set must be made for this graph or for any."""
        problems = [Problem(s.source, f"is made for graph '{s.graph}', not '{graph_id}'") for s in sets
                    if s.graph not in (None, graph_id)]
        if problems:
            raise GraphError(problems)
        return ParameterSet._merged(sets, " + ".join(s.source for s in sets) or "parameters", graph_id)

    @staticmethod
    def _merged(sets: Sequence[ParameterSet], source: str, graph: str | None) -> ParameterSet:
        """Merged values; the graph is `graph` if given (a set may take over values made for another graph, e.g. a
        project for the pilot graph), else the one graph of the sets."""
        graphs = {s.graph for s in sets if s.graph is not None}
        if graph is None and len(graphs) > 1:
            raise GraphError([Problem(source, f"combines sets for different graphs: {', '.join(sorted(graphs))}")])
        params: dict[str, Any] = {}
        defaults: dict[str, Any] = {}
        nodes: dict[str, Any] = {}
        for s in sets:
            params = merged_tables(params, s.params)
            defaults = merged_tables(defaults, s.defaults)
            nodes = merged_tables(nodes, s.nodes)
        return ParameterSet(graph or next(iter(graphs), None), params, nodes, source, defaults)


def merged_tables(lower: Values, upper: Values) -> dict[str, Any]:
    """`upper` on top of `lower`: tables in both are merged key by key, everything else is taken from `upper`."""
    result = dict(lower)
    for key, value in upper.items():
        below = result.get(key)
        if isinstance(below, Mapping) and isinstance(value, Mapping):
            result[key] = merged_tables(below, value)
        else:
            result[key] = value
    return result
