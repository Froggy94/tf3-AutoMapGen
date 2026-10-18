"""Structural signature of a node tree: equal signatures = the same computation, regardless of node names,
editor positions and colours.

Each node's label is a hash of its layer type, parameters and the labels of the nodes feeding its input slots
(a Merkle hash over the tree). The signature of a tree file is the sorted multiset of all node labels plus the rest of
the document (comments, snap points, preview seed).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from automapgen.lua.document import DataDocument
from automapgen.lua.tree import NodeTree, NodeTreeError, TreeNode
from automapgen.lua.values import LuaTable, to_plain

_COSMETIC_KEYS = ("name", "position", "color")


class TreeSignature:
    """Computes node labels and the document signature of one tree file."""

    def __init__(self, document: DataDocument) -> None:
        self._document = document
        self._graph = NodeTree(document)
        self._labels: dict[str, str] = {}
        self._visiting: set[str] = set()

    def signature(self) -> dict[str, Any]:
        labels = sorted(self._label(node.name) for node in self._graph)
        rest = LuaTable([(k, v) for k, v in self._document.root.entries() if k != "nodes"])
        return {"nodes": labels, "document": to_plain(rest)}

    def _label(self, name: str) -> str:
        if name in self._labels:
            return self._labels[name]
        if name not in self._graph:
            return f"missing:{name}"
        if name in self._visiting:
            raise NodeTreeError(f"cycle through node {name!r}")
        self._visiting.add(name)
        node = self._graph.node(name)
        content = {
            "layerType": node.layer_type,
            "params": to_plain(node.table.table("params")),
            "inputs": {slot: [self._label(port.node), port.key] for slot, port in node.inputs().items()},
            "other": self._other_fields(node),
        }
        self._visiting.discard(name)
        label = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        self._labels[name] = label
        return label

    @staticmethod
    def _other_fields(node: TreeNode) -> Any:
        known = ("inputs", "layerType", "params", *_COSMETIC_KEYS)
        return to_plain(LuaTable([(k, v) for k, v in node.table.entries() if k not in known]))
