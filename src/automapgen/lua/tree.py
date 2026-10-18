"""Node tree of a game generator file (`*.tree.lua`): nodes with typed inputs and parameters.

Replaces text patching: nodes are looked up by name (unknown or duplicate names are errors), inputs are rewired to
ports, parameters are set as values, and `validate` checks that every input points to an existing node.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from automapgen.lua.document import DataDocument
from automapgen.lua.values import LuaNumber, LuaTable, LuaValue

type ParamValue = bool | int | float | str | Sequence[tuple[float, float]]

DEFAULT_COLOR = (0.6, 0.2, 0.6, 0.6)


class NodeTreeError(ValueError):
    """Invalid node graph operation (unknown node, duplicate name, dangling input)."""


@dataclass(frozen=True)
class Port:
    """Output `key` of node `node` (most nodes have "out"; inputs of data maps use "input")."""

    node: str
    key: str = "out"


def lua_value(value: ParamValue) -> LuaValue:
    """Python parameter value -> Lua value; a sequence of pairs becomes a point cloud."""
    if isinstance(value, bool | str):
        return value
    if isinstance(value, int | float):
        return LuaNumber.of(value)
    return LuaTable([(None, LuaTable([(None, LuaNumber.of(x)), (None, LuaNumber.of(y))])) for x, y in value])


def _number_pair(values: tuple[float, float]) -> LuaTable:
    return LuaTable([(None, LuaNumber.of(values[0])), (None, LuaNumber.of(values[1]))])


class TreeNode:
    """View on one node table of the graph."""

    def __init__(self, table: LuaTable) -> None:
        self._table = table

    @classmethod
    def create(cls, name: str, layer_type: str, inputs: Mapping[str, Port] | None = None,
               params: Mapping[str, ParamValue] | None = None, position: tuple[float, float] = (0.0, 0.0),
               color: tuple[float, float, float, float] = DEFAULT_COLOR) -> TreeNode:
        """New node in the editor's field order (keys sorted alphabetically)."""
        input_table = LuaTable([(slot, LuaTable([("key", port.key), ("nodeName", port.node)]))
                                for slot, port in sorted((inputs or {}).items())])
        param_table = LuaTable([(key, lua_value(value)) for key, value in sorted((params or {}).items())])
        return cls(LuaTable([
            ("color", LuaTable([(None, LuaNumber.of(c)) for c in color])),
            ("inputs", input_table),
            ("layerType", layer_type),
            ("name", name),
            ("params", param_table),
            ("position", _number_pair(position)),
        ]))

    @property
    def table(self) -> LuaTable:
        return self._table

    @property
    def name(self) -> str:
        return str(self._table["name"])

    @property
    def layer_type(self) -> str:
        return str(self._table["layerType"])

    def inputs(self) -> dict[str, Port]:
        result: dict[str, Port] = {}
        for slot, value in self._table.table("inputs").keyed():
            if isinstance(value, LuaTable) and "nodeName" in value:
                result[str(slot)] = Port(str(value["nodeName"]), str(value.get("key", "out")))
        return result

    def set_input(self, slot: str, port: Port) -> None:
        """Rewire an existing input slot (a missing slot means the game file changed -> error)."""
        inputs = self._table.table("inputs")
        if slot not in inputs:
            raise NodeTreeError(f"node {self.name!r} has no input {slot!r}")
        inputs[slot] = LuaTable([("key", port.key), ("nodeName", port.node)])

    def param(self, key: str) -> LuaValue:
        return self._table.table("params")[key]

    def set_param(self, key: str, value: ParamValue) -> None:
        """Change an existing parameter (a missing one means the game file changed -> error)."""
        params = self._table.table("params")
        if key not in params:
            raise NodeTreeError(f"node {self.name!r} has no parameter {key!r}")
        params[key] = lua_value(value)


class NodeTree:
    """All nodes of a tree file plus the surrounding document (comments, snap points, preview seed)."""

    def __init__(self, document: DataDocument) -> None:
        self._document = document
        self._nodes = document.root.table("nodes")
        self._by_name: dict[str, TreeNode] = {}
        for value in self._nodes.array():
            if not isinstance(value, LuaTable):
                raise NodeTreeError("nodes must be tables")
            self._register(TreeNode(value))

    @property
    def document(self) -> DataDocument:
        return self._document

    def __iter__(self) -> Iterator[TreeNode]:
        return iter(self._by_name.values())

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def node(self, name: str) -> TreeNode:
        try:
            return self._by_name[name]
        except KeyError:
            raise NodeTreeError(f"node not found: {name!r}") from None

    def add(self, node: TreeNode) -> TreeNode:
        self._register(node)
        self._nodes.append(node.table)
        return node

    def add_all(self, nodes: Sequence[TreeNode]) -> None:
        for node in nodes:
            self.add(node)

    def validate(self) -> None:
        """Every input must point to an existing node."""
        dangling = sorted({f"{node.name}.{slot} -> {port.node}" for node in self for slot, port in node.inputs().items()
                           if port.node not in self._by_name})
        if dangling:
            raise NodeTreeError("inputs without target node: " + "; ".join(dangling))

    def _register(self, node: TreeNode) -> None:
        if node.name in self._by_name:
            raise NodeTreeError(f"duplicate node name: {node.name!r}")
        self._by_name[node.name] = node
