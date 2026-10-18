"""Helpers to build graphs and compilers in tests."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.document import GraphDocument
from automapgen.graph.library import MemoryLibrary


def graph(graph_id: str = "g", **fields: Any) -> GraphDocument:
    """A graph document from keyword arguments, written as in a .graph.json file."""
    return GraphDocument.model_validate({"id": graph_id, **fields})


def compiler(catalog: NodeCatalog, documents: Iterable[GraphDocument] = ()) -> GraphCompiler:
    return GraphCompiler(catalog, MemoryLibrary(documents))
