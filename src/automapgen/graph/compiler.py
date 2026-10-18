"""Compiles a graph document into an execution plan.

Compiling = resolve `extends`, dissolve subgraphs into node paths (`forest/below`), merge parameter values, check
types, links and parameters, and sort the nodes. Every problem is collected; a graph with problems never runs. The plan
also lists the parameter slots: which value of a parameter set reaches which node parameters.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.composer import GraphComposer
from automapgen.graph.data import DataType
from automapgen.graph.document import GRAPH_INPUTS, GRAPH_OUTPUTS, GraphDocument, Link, NodeEntry
from automapgen.graph.errors import GraphError, Problem
from automapgen.graph.library import GraphLibrary
from automapgen.graph.node import PORT_NAME, Arity, NodeSpec
from automapgen.graph.params import NodeParams
from automapgen.graph.paramsets import ParameterSet, Values, merged_tables
from automapgen.graph.plan import ExecutionPlan, ParameterSlot, ParamRef, PlannedNode, PortRef

_NODE_PARAMETER = re.compile(r"^(?P<scope>.*), node '(?P<node>[^']*)', parameter '(?P<param>[^']*)'$")

type _Kind = NodeSpec | GraphDocument
"""What a node entry stands for: a node type or a (resolved) subgraph."""
type _Types = Mapping[str, DataType[Any] | None]


class GraphCompiler:
    def __init__(self, catalog: NodeCatalog, library: GraphLibrary) -> None:
        self._catalog = catalog
        self._library = library

    def compile(self, document: GraphDocument, parameters: ParameterSet | None = None) -> ExecutionPlan:
        """The plan of the document with the parameter set applied; raises GraphError listing all problems."""
        return _Compilation(self._catalog, self._library, parameters or ParameterSet()).plan(document)


@dataclass
class _FlatNode:
    path: str
    spec: NodeSpec
    params: NodeParams | None
    inputs: dict[str, list[PortRef]]


@dataclass
class _Slot:
    """A ParameterSlot being collected."""

    owner: str
    name: str
    scope: str
    local: str
    description: str = ""
    targets: list[ParamRef] = field(default_factory=list)

    def frozen(self) -> ParameterSlot:
        return ParameterSlot(self.owner, self.name, self.scope, self.local, self.description, tuple(self.targets))


@dataclass(frozen=True)
class _Level:
    """One graph document being dissolved: at the top (prefix "") or as subgraph instance (prefix "forest/").
    `owners`: the slot of each exposed parameter of the document; `claimed`: node id -> parameter -> the slot of the
    exposed parameter that sets it."""

    doc: GraphDocument
    prefix: str
    where: str
    inputs: _Types
    outputs: _Types
    owners: Mapping[str, _Slot]
    kinds: dict[str, _Kind] = field(default_factory=dict)
    claimed: dict[str, dict[str, _Slot]] = field(default_factory=dict)

    def path(self, node_id: str) -> str:
        return self.prefix + node_id


@dataclass(frozen=True)
class _Target:
    node: str
    port: str
    type: DataType[Any] | None
    arity: Arity


class _Compilation:
    """State of one compile: flat nodes, port aliases at subgraph borders, problems."""

    def __init__(self, catalog: NodeCatalog, library: GraphLibrary, parameters: ParameterSet) -> None:
        self._catalog = catalog
        self._library = library
        self._composer = GraphComposer(library)
        self._parameters = parameters
        self._problems: list[Problem] = []
        self._flat: dict[str, _FlatNode] = {}
        self._aliases: dict[PortRef, PortRef] = {}   # subgraph input/output -> what feeds it
        self._outputs: dict[str, PortRef] = {}
        self._inputs: _Types = {}
        self._paths: set[str] = set()
        self._slots: list[_Slot] = []

    def plan(self, document: GraphDocument) -> ExecutionPlan:
        resolved = self._guarded(lambda: self._composer.resolve(document))
        if resolved is not None:
            owners = {name: self._slot("", name, resolved.id, "", exposed.description)
                      for name, exposed in resolved.params.items()}
            self._dissolve(resolved, "", self._parameters.given_for(list(resolved.params)), self._parameters.source,
                          (resolved.id,), owners)
            self._problems += [Problem(self._parameters.source, f"no node '{p}' in graph '{resolved.id}'")
                               for p in self._parameters.nodes if p not in self._paths]
        order = self._order()
        if self._problems:
            raise GraphError(self._grouped(self._problems))
        nodes = tuple(PlannedNode(f.path, f.spec, f.params, {p: tuple(map(self._resolve, refs))
                                                             for p, refs in f.inputs.items()})
                      for f in order if f.params is not None)
        return ExecutionPlan(document.id, {k: t for k, t in self._inputs.items() if t is not None},
                             {k: self._resolve(ref) for k, ref in self._outputs.items()}, nodes,
                             tuple(s.frozen() for s in self._slots if s.targets))

    # --- one graph level ----------------------------------------------------------------------------------------

    def _dissolve(self, doc: GraphDocument, prefix: str, given: Values, given_by: str, stack: tuple[str, ...],
                  owners: Mapping[str, _Slot]) -> None:
        where = f"graph '{doc.id}'" + (f" in '{prefix[:-1]}'" if prefix else "")
        level = _Level(doc, prefix, where, self._declared(doc.inputs, where, "input"),
                       self._declared(doc.outputs, where, "output"), owners)
        if not prefix:
            self._inputs = level.inputs
        self._problems += [Problem(given_by, f"graph '{doc.id}' has no parameter '{n}'") for n in given
                           if n not in doc.params]
        for node_id, entry in doc.nodes.items():
            if not PORT_NAME.match(node_id) or node_id in (GRAPH_INPUTS, GRAPH_OUTPUTS):
                self._problem(where, f"node id '{node_id}' must be lower case with underscores, not inputs/outputs")
            elif (kind := self._kind(entry, f"{where}, node '{node_id}'", stack)) is not None:
                level.kinds[node_id] = kind
        values = self._values(level, given)
        for node_id, kind in level.kinds.items():
            self._paths.add(level.path(node_id))
            node_where = f"{where}, node '{node_id}'"
            claimed = level.claimed.get(node_id, {})
            if isinstance(kind, NodeSpec):
                params = self._params(kind, values[node_id], node_where)
                self._flat[level.path(node_id)] = _FlatNode(level.path(node_id), kind, params,
                                                            {p: [] for p in kind.inputs})
                for name in kind.params_model.model_fields:
                    slot = claimed.get(name) or self._slot(level.path(node_id), name, doc.id, node_id)
                    slot.targets.append(ParamRef(level.path(node_id), name))
            else:
                inner = {name: claimed.get(name) or self._slot(level.path(node_id), name, doc.id, node_id,
                                                               exposed.description)
                         for name, exposed in kind.params.items()}
                self._dissolve(kind, f"{level.path(node_id)}/", values[node_id], node_where, (*stack, kind.id),
                               inner)
        counts: Counter[tuple[str, str]] = Counter()
        for link in doc.links:
            self._link(level, link, counts)
        self._check_arity(level, counts)

    def _declared(self, ports: Mapping[str, str], where: str, what: str) -> dict[str, DataType[Any] | None]:
        result: dict[str, DataType[Any] | None] = {}
        for name, type_name in ports.items():
            if not PORT_NAME.match(name):
                self._problem(where, f"{what} name '{name}' must be lower case with underscores")
            result[name] = self._catalog.data_types.named(type_name)
            if result[name] is None:
                self._problem(where, f"{what} '{name}' has unknown data type '{type_name}' "
                                     f"(known: {', '.join(self._catalog.data_types.names())})")
        return result

    def _kind(self, entry: NodeEntry, where: str, stack: tuple[str, ...]) -> _Kind | None:
        graph_id = entry.subgraph
        if graph_id is None:
            spec = self._catalog.spec(entry.type)
            if spec is None:
                self._problem(where, f"unknown node type '{entry.type}'")
            return spec
        if graph_id in stack:
            self._problem(where, f"graph uses itself: {' -> '.join((*stack, graph_id))}")
            return None
        try:
            document = self._library.get(graph_id)
            if document is None:
                self._problem(where, f"unknown graph '{graph_id}'")
                return None
            return self._composer.resolve(document)
        except GraphError as e:
            self._problems += e.problems
            return None

    # --- parameters ---------------------------------------------------------------------------------------------

    def _values(self, level: _Level, given: Values) -> dict[str, dict[str, Any]]:
        """Raw parameter values per node: document (migrated) < exposed parameter < node path of the parameter set;
        tables are merged key by key (like the layers of parameter sets). Notes which exposed parameter sets what."""
        doc = level.doc
        values: dict[str, dict[str, Any]] = {
            n: self._migrated(k, doc.nodes[n], f"{level.where}, node '{n}'") if isinstance(k, NodeSpec)
            else dict(doc.nodes[n].params) for n, k in level.kinds.items()}
        for name, exposed in doc.params.items():
            for target in exposed.to:
                node_id, _, param_name = target.partition(".")
                kind = level.kinds.get(node_id)
                if kind is None:
                    if node_id not in doc.nodes:
                        self._problem(level.where, f"parameter '{name}' sets '{target}' of unknown node '{node_id}'")
                elif param_name not in self._param_names(kind):
                    self._problem(level.where, f"parameter '{name}' sets unknown parameter '{target}'")
                else:
                    level.claimed.setdefault(node_id, {}).setdefault(param_name, level.owners[name])
                    if name in given:
                        values[node_id] = merged_tables(values[node_id], {param_name: given[name]})
                    elif exposed.has_value:
                        values[node_id] = merged_tables(values[node_id], {param_name: exposed.value})
        for node_id in level.kinds:
            values[node_id] = merged_tables(values[node_id], self._parameters.nodes.get(level.path(node_id), {}))
        return values

    def _migrated(self, spec: NodeSpec, entry: NodeEntry, where: str) -> dict[str, Any]:
        params: dict[str, Any] = dict(entry.params)
        version = entry.version or spec.version
        if version > spec.version:
            self._problem(where, f"written for version {version} of '{spec.type_id}', known is {spec.version}")
        while version < spec.version:
            migration = spec.migrations.get(version)
            if migration is None:
                self._problem(where, f"parameters of version {version} cannot be migrated to {spec.version}")
                break
            params, version = migration(params), version + 1
        return params

    @staticmethod
    def _param_names(kind: _Kind) -> set[str]:
        return set(kind.params_model.model_fields) if isinstance(kind, NodeSpec) else set(kind.params)

    def _params(self, spec: NodeSpec, raw: dict[str, Any], where: str) -> NodeParams | None:
        try:
            return spec.params_model.model_validate(raw)
        except ValidationError as e:
            for err in e.errors():
                self._problem(f"{where}, parameter '{'.'.join(map(str, err['loc']))}'", err["msg"])
            return None

    def _slot(self, owner: str, name: str, scope: str, local: str, description: str = "") -> _Slot:
        slot = _Slot(owner, name, scope, local, description)
        self._slots.append(slot)
        return slot

    # --- links --------------------------------------------------------------------------------------------------

    def _link(self, level: _Level, link: Link, counts: Counter[tuple[str, str]]) -> None:
        where = f"{level.where}, link {link[0]} -> {link[1]}"
        source = self._source(level, link[0], where)
        target = self._target(level, link[1], where)
        if source is None or target is None:
            return
        ref, source_type = source
        if source_type is not None and target.type is not None and not source_type.fits(target.type):
            self._problem(where, f"'{source_type.name}' does not fit '{target.type.name}'")
        counts[target.node, target.port] += 1
        if target.node == GRAPH_OUTPUTS:
            if level.prefix:
                self._aliases[PortRef(level.prefix[:-1], target.port)] = ref
            else:
                self._outputs[target.port] = ref
        elif isinstance(level.kinds[target.node], NodeSpec):
            self._flat[level.path(target.node)].inputs[target.port].append(ref)
        else:
            self._aliases[PortRef(f"{level.path(target.node)}/{GRAPH_INPUTS}", target.port)] = ref

    def _source(self, level: _Level, text: str, where: str) -> tuple[PortRef, DataType[Any] | None] | None:
        node_id, port = self._split(text, where)
        if node_id == GRAPH_INPUTS:
            if port not in level.inputs:
                self._problem(where, f"graph has no input '{port}'")
                return None
            return PortRef(level.path(GRAPH_INPUTS), port), level.inputs[port]
        kind = self._known(level, node_id, where)
        if isinstance(kind, NodeSpec):
            if port not in kind.outputs:
                self._problem(where, f"'{node_id}' has no output '{port}' (has: {', '.join(kind.outputs) or '-'})")
                return None
            return PortRef(level.path(node_id), port), kind.outputs[port].type
        if kind is not None:
            if port not in kind.outputs:
                self._problem(where, f"subgraph '{node_id}' has no output '{port}'")
                return None
            return PortRef(level.path(node_id), port), self._catalog.data_types.named(kind.outputs[port])
        return None

    def _target(self, level: _Level, text: str, where: str) -> _Target | None:
        node_id, port = self._split(text, where)
        if node_id == GRAPH_OUTPUTS:
            if port not in level.outputs:
                self._problem(where, f"graph has no output '{port}'")
                return None
            return _Target(GRAPH_OUTPUTS, port, level.outputs[port], Arity.ONE)
        kind = self._known(level, node_id, where)
        if isinstance(kind, NodeSpec):
            if port not in kind.inputs:
                self._problem(where, f"'{node_id}' has no input '{port}' (has: {', '.join(kind.inputs) or '-'})")
                return None
            return _Target(node_id, port, kind.inputs[port].type, kind.inputs[port].arity)
        if kind is not None:
            if port not in kind.inputs:
                self._problem(where, f"subgraph '{node_id}' has no input '{port}'")
                return None
            return _Target(node_id, port, self._catalog.data_types.named(kind.inputs[port]), Arity.ONE)
        return None

    def _known(self, level: _Level, node_id: str, where: str) -> _Kind | None:
        if node_id in (GRAPH_INPUTS, GRAPH_OUTPUTS):
            self._problem(where, f"'{node_id}' cannot be used on this side of a link")
        elif node_id not in level.doc.nodes:
            self._problem(where, f"unknown node '{node_id}'")
        return level.kinds.get(node_id)   # None also for nodes whose type is unknown (reported once, above)

    def _split(self, text: str, where: str) -> tuple[str, str]:
        node_id, dot, port = text.partition(".")
        if not dot or not port:
            self._problem(where, f"'{text}' must look like 'node.port'")
        return node_id, port

    def _check_arity(self, level: _Level, counts: Counter[tuple[str, str]]) -> None:
        for node_id, kind in level.kinds.items():
            arities = ({p: i.arity for p, i in kind.inputs.items()} if isinstance(kind, NodeSpec)
                       else dict.fromkeys(kind.inputs, Arity.ONE))
            for port, arity in arities.items():
                n = counts[node_id, port]
                if arity is Arity.ONE and n == 0:
                    self._problem(f"{level.where}, node '{node_id}'", f"input '{port}' is not connected")
                elif arity is not Arity.MANY and n > 1:
                    self._problem(f"{level.where}, node '{node_id}'", f"input '{port}' takes one link, has {n}")
        for name in level.doc.outputs:
            if counts[GRAPH_OUTPUTS, name] != 1:
                self._problem(level.where, f"output '{name}' needs exactly one link, has {counts[GRAPH_OUTPUTS, name]}")

    # --- wiring and order ---------------------------------------------------------------------------------------

    def _resolve(self, ref: PortRef) -> PortRef:
        """Follows subgraph borders to the real producer (a node output or a graph input)."""
        seen = set()
        while ref in self._aliases and ref not in seen:
            seen.add(ref)
            ref = self._aliases[ref]
        return ref

    def _order(self) -> list[_FlatNode]:
        """Producers before consumers, otherwise document order; reports cycles."""
        needs = {path: {self._resolve(r).node for refs in f.inputs.values() for r in refs} & self._flat.keys()
                 for path, f in self._flat.items()}
        ordered: list[_FlatNode] = []
        done: set[str] = set()
        remaining = list(self._flat.values())
        while remaining:
            ready = next((f for f in remaining if needs[f.path] <= done), None)
            if ready is None:
                self._problem("graph", f"links form a cycle through: {', '.join(f.path for f in remaining)}")
                break
            ordered.append(ready)
            done.add(ready.path)
            remaining.remove(ready)
        return ordered

    # --- problems -----------------------------------------------------------------------------------------------

    def _problem(self, where: str, message: str) -> None:
        self._problems.append(Problem(where, message))

    @staticmethod
    def _grouped(problems: list[Problem]) -> list[Problem]:
        """The same problem of one parameter in several nodes of a graph (a value given to all of them, e.g. the
        climate preset) as one problem that lists the nodes."""
        nodes: dict[tuple[str, str, str], list[str]] = {}
        order: list[Problem | tuple[str, str, str]] = []
        for problem in problems:
            match = _NODE_PARAMETER.match(problem.where)
            if match is None:
                order.append(problem)
                continue
            key = (match["scope"], match["param"], problem.message)
            if key not in nodes:
                nodes[key] = []
                order.append(key)
            nodes[key].append(match["node"])

        def merged(scope: str, param: str, message: str) -> Problem:
            names = nodes[(scope, param, message)]
            which = f"node '{names[0]}'" if len(names) == 1 else "nodes " + ", ".join(f"'{n}'" for n in names)
            return Problem(f"{scope}, {which}, parameter '{param}'", message)

        return [item if isinstance(item, Problem) else merged(*item) for item in order]

    def _guarded[T](self, action: Callable[[], T | None]) -> T | None:
        """Runs a lookup that may raise GraphError (bad file, broken `extends`) and collects its problems."""
        try:
            return action()
        except GraphError as e:
            self._problems += e.problems
            return None
