"""The node catalog: every node type and data type the framework knows.

Node types and data types are collected from modules: the built-in node packages and plugins, which register a module
under the entry-point group `automapgen.nodes` in their package metadata:

    [project.entry-points."automapgen.nodes"]
    my_nodes = "my_package.nodes"
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points
from types import ModuleType
from typing import Any

from automapgen.graph.data import SCALARS, DataType, DataTypes, declared_data_type
from automapgen.graph.errors import NodeDefinitionError
from automapgen.graph.node import NodeDeclaration, NodeSpec, declared_node

ENTRY_POINT_GROUP = "automapgen.nodes"


class NodeCatalog:
    """Node types by id and the data types they use."""

    def __init__(self, data_types: DataTypes, specs: Iterable[NodeSpec]) -> None:
        self.data_types = data_types
        self._specs: dict[str, NodeSpec] = {}
        for spec in specs:
            if spec.type_id in self._specs:
                raise NodeDefinitionError(f"node type '{spec.type_id}' is declared twice")
            self._specs[spec.type_id] = spec

    @classmethod
    def from_modules(cls, modules: Iterable[ModuleType]) -> NodeCatalog:
        """All data types and nodes declared in the modules (a member imported by several modules counts once)."""
        types: dict[int, DataType[Any]] = {}
        declarations: dict[int, NodeDeclaration] = {}
        for module in modules:
            for member in vars(module).values():
                if (t := declared_data_type(member)) is not None:
                    types[id(member)] = t
                elif (d := declared_node(member)) is not None:
                    declarations[id(member)] = d
        data_types = DataTypes([*SCALARS, *types.values()])
        return cls(data_types, (NodeSpec.of(d, data_types) for d in declarations.values()))

    @classmethod
    def discover(cls, builtin: Iterable[ModuleType] = ()) -> NodeCatalog:
        """The built-in modules plus every installed plugin."""
        plugins = [ep.load() for ep in entry_points(group=ENTRY_POINT_GROUP)]
        return cls.from_modules([*builtin, *plugins])

    def spec(self, type_id: str) -> NodeSpec | None:
        return self._specs.get(type_id)

    def specs(self) -> list[NodeSpec]:
        return [self._specs[k] for k in sorted(self._specs)]
