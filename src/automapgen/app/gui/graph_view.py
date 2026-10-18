"""The graphs in the GUI: which graphs a project can use, a graph as diagram, and the check of a project against its
graph before a run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from automapgen.app.gui.settings import GraphChoice
from automapgen.app.project_checks import ProjectChecks
from automapgen.app.projects import ProjectStore
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.composer import GraphComposer
from automapgen.graph.document import GraphDocument
from automapgen.graph.errors import GraphError
from automapgen.graph.library import GraphLibrary
from automapgen.graph.render import MermaidDiagram


class GraphView:
    """Graph list, diagram and check for the page; knows the graph library, not the nodes' meaning."""

    def __init__(self, choices: Sequence[GraphChoice], catalog: NodeCatalog, library: GraphLibrary,
                 projects: ProjectStore, checks: ProjectChecks | None = None) -> None:
        self._choices = choices
        self._library = library
        self._projects = projects
        self._compiler = GraphCompiler(catalog, library)
        self._checks = checks or ProjectChecks(lambda: None)

    def choices(self) -> list[dict[str, str]]:
        """The graphs offered for projects (configs/gui.toml [[graphs]]) with their descriptions."""
        return [{"id": c.id, "label": c.label, "description": self._document(c.id).description}
                for c in self._choices]

    def diagram(self, graph_id: str) -> dict[str, Any]:
        """The graph as authored, with what it extends filled in: a Mermaid flowchart (subgraphs are one box each)
        and the diagram ids of the subgraph boxes, so the page can open them."""
        document = GraphComposer(self._library).resolve(self._document(graph_id))
        subgraphs = {MermaidDiagram.node_id(node_id): entry.subgraph
                     for node_id, entry in document.nodes.items() if entry.subgraph}
        diagram = MermaidDiagram(lambda sub: self._document(sub).title)
        return {"id": graph_id, "title": document.title, "description": document.description,
                "mermaid": diagram.render(document),
                "subgraphs": subgraphs, "nodes": len(document.nodes), "links": len(document.links)}

    def check(self, project: Mapping[str, Any]) -> list[str]:
        """Problems of a project document with its graph (empty: it compiles and its game materials exist)."""
        try:
            parameters = self._projects.parameters(dict(project))
            plan = self._compiler.compile(self._document(parameters.graph or "map"), parameters)
        except GraphError as error:
            return [str(p) for p in error.problems]
        except ValueError as error:
            return [str(error)]
        return [str(p) for p in self._checks.problems(plan)]

    def warnings(self, project: Mapping[str, Any]) -> list[str]:
        """What a run would do that the map maker may not want (does not stop the run)."""
        try:
            merged = self._projects.parameters(dict(project)).defaults
        except (GraphError, ValueError):
            return []           # reported by check
        return self._checks.warnings(project, merged)

    def _document(self, graph_id: str) -> GraphDocument:
        document = self._library.get(graph_id)
        if document is None:
            raise ValueError(f"unknown graph {graph_id!r}")
        return document
