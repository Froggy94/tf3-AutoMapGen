"""Composition in layers: a graph that `extends` another holds only its differences.

Resolving a document = its base (resolved first, so bases may extend bases) plus the differences:
- `inputs`, `outputs`, `params`, `ui`: merged by name, the extending document wins;
- `nodes`: `remove`d nodes leave together with their links; an entry with an existing id replaces that node (links
  stay, so a node can be swapped for another node type or a subgraph with the same ports); new ids are added;
- `links`: `unlink`ed links leave, the document's links are added.
A map graph thus follows improvements of the standard graph it extends instead of being a copy of it.
"""

from __future__ import annotations

from automapgen.graph.document import GraphDocument, Link
from automapgen.graph.errors import GraphError, Problem
from automapgen.graph.library import GraphLibrary


class GraphComposer:
    def __init__(self, library: GraphLibrary) -> None:
        self._library = library

    def resolve(self, document: GraphDocument) -> GraphDocument:
        """The complete graph described by the document (without `extends`)."""
        return self._resolve(document, ())

    def _resolve(self, document: GraphDocument, chain: tuple[str, ...]) -> GraphDocument:
        if document.extends is None:
            return document
        where = f"graph '{document.id}'"
        if document.extends in (*chain, document.id):
            loop = " -> ".join((*chain, document.id, document.extends))
            raise GraphError([Problem(where, f"extends itself: {loop}")])
        base_document = self._library.get(document.extends)
        if base_document is None:
            raise GraphError([Problem(where, f"extends unknown graph '{document.extends}'")])
        base = self._resolve(base_document, (*chain, document.id))
        return self._apply(base, document, where)

    @staticmethod
    def _apply(base: GraphDocument, diff: GraphDocument, where: str) -> GraphDocument:
        problems = [Problem(where, f"removes unknown node '{n}'") for n in diff.remove if n not in base.nodes]
        problems += [Problem(where, f"unlinks unknown link {list(link)}") for link in diff.unlink
                     if link not in base.links]
        if problems:
            raise GraphError(problems)
        removed = set(diff.remove)

        def kept(link: Link) -> bool:
            touched = {end.split(".", 1)[0] for end in link}
            return link not in diff.unlink and not touched & removed

        nodes = {k: v for k, v in base.nodes.items() if k not in removed} | diff.nodes
        return GraphDocument(
            id=diff.id, title=diff.title or base.title, description=diff.description or base.description,
            inputs=base.inputs | diff.inputs, outputs=base.outputs | diff.outputs, params=base.params | diff.params,
            nodes=nodes, links=[link for link in base.links if kept(link)] + diff.links, ui=base.ui | diff.ui)
