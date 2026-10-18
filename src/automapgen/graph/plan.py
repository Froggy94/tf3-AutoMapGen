"""The execution plan: a compiled graph - flat (subgraphs dissolved), typed, checked and in execution order."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from automapgen.graph.data import DataType
from automapgen.graph.document import GRAPH_INPUTS
from automapgen.graph.node import NodeSpec
from automapgen.graph.params import NodeParams


@dataclass(frozen=True)
class PortRef:
    """An output of a node (`node` = its path) or a graph input (`node` = "inputs")."""

    node: str
    port: str

    @property
    def is_graph_input(self) -> bool:
        return self.node == GRAPH_INPUTS

    def __str__(self) -> str:
        return f"{self.node}.{self.port}"


@dataclass(frozen=True)
class PlannedNode:
    """A node to run: path (`forest/below` = node `below` in subgraph instance `forest`), type, parameters and the
    sources of each input (in link order)."""

    id: str
    spec: NodeSpec
    params: NodeParams
    inputs: Mapping[str, tuple[PortRef, ...]]

    def sources(self) -> list[PortRef]:
        return [ref for refs in self.inputs.values() for ref in refs]


@dataclass(frozen=True)
class ParamRef:
    """A parameter of a planned node."""

    node: str
    name: str


@dataclass(frozen=True)
class ParameterSlot:
    """A value a parameter set can give and the node parameters it reaches.

    `owner` "" = an exposed parameter of the graph (`[params]`); otherwise a node path (`[nodes."<owner>"]`) - of a
    subgraph instance (`name` is one of its exposed parameters) or of a node (`name` is its parameter). A node
    parameter belongs to the highest slot that sets it, so it is in no slot below that one. `scope` is the id of the
    graph document `owner` is a node of, `local` its id there (the same node in two instances of a subgraph has the
    same scope and local id)."""

    owner: str
    name: str
    scope: str
    local: str
    description: str
    targets: tuple[ParamRef, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    graph_id: str
    inputs: Mapping[str, DataType[Any]]
    outputs: Mapping[str, PortRef]
    nodes: tuple[PlannedNode, ...]
    slots: tuple[ParameterSlot, ...] = ()
    """What parameter sets can set, in document order (for forms: the GUI's advanced settings)."""

    def node(self, node_id: str) -> PlannedNode:
        return self._by_id[node_id]

    @cached_property
    def _by_id(self) -> dict[str, PlannedNode]:
        return {n.id: n for n in self.nodes}
