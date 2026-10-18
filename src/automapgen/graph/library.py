"""Where graph documents come from: folders (project before built-in) or memory (tests, editor)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from automapgen.graph.document import GraphDocument, GraphFile
from automapgen.graph.errors import GraphError, Problem


class GraphLibrary(Protocol):
    def get(self, graph_id: str) -> GraphDocument | None: ...


class MemoryLibrary:
    """Graph documents held in memory."""

    def __init__(self, documents: Iterable[GraphDocument]) -> None:
        self._documents = {d.id: d for d in documents}

    def get(self, graph_id: str) -> GraphDocument | None:
        return self._documents.get(graph_id)


class FolderLibrary:
    """Graph documents `<id>.graph.json` in folders, searched in the given order (the first folder wins)."""

    def __init__(self, folders: Sequence[Path]) -> None:
        self._folders = tuple(folders)
        self._loaded: dict[str, GraphDocument] = {}

    def get(self, graph_id: str) -> GraphDocument | None:
        if graph_id not in self._loaded:
            path = next((p for f in self._folders if (p := f / f"{graph_id}{GraphFile.SUFFIX}").is_file()), None)
            if path is None:
                return None
            document = GraphFile.load(path)
            if document.id != graph_id:
                raise GraphError([Problem(str(path), f"file name says '{graph_id}', document id is '{document.id}'")])
            self._loaded[graph_id] = document
        return self._loaded[graph_id]

    def ids(self) -> list[str]:
        return sorted({p.name.removesuffix(GraphFile.SUFFIX) for f in self._folders
                       for p in f.glob(f"*{GraphFile.SUFFIX}")})
