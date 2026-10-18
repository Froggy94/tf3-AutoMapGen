"""Graphs as pictures: a Mermaid flowchart (shown by GitHub, many editors and docs) of a graph document."""

from __future__ import annotations

from collections.abc import Callable

from automapgen.graph.document import GRAPH_INPUTS, GRAPH_OUTPUTS, GraphDocument


class MermaidDiagram:
    """The document as authored: subgraphs are one box each (with their title, if `titles` knows it), links are
    labelled with their ports (one name when both ends use the same)."""

    def __init__(self, titles: Callable[[str], str] | None = None) -> None:
        self._titles = titles

    def render(self, document: GraphDocument) -> str:
        lines = ["flowchart LR"]
        if document.inputs:
            lines.append(f'  {_id(GRAPH_INPUTS)}[/"inputs<br/>{_ports(document.inputs)}"/]')
        for node_id, entry in document.nodes.items():
            title = self._titles(entry.subgraph) if entry.subgraph and self._titles else ""
            if title:
                lines.append(f'  {_id(node_id)}[["<b>{title}</b><br/><small>{node_id}</small>"]]')
            else:
                shape = ("[[", "]]") if entry.subgraph else ("[", "]")
                lines.append(f'  {_id(node_id)}{shape[0]}"{node_id}<br/><small>{entry.type}</small>"{shape[1]}')
        if document.outputs:
            lines.append(f'  {_id(GRAPH_OUTPUTS)}[\\"outputs<br/>{_ports(document.outputs)}"\\]')
        labels: dict[tuple[str, str], list[str]] = {}       # one arrow per pair of nodes, all ports on it
        for source, target in document.links:
            source_node, _, source_port = source.partition(".")
            target_node, _, target_port = target.partition(".")
            label = source_port if source_port == target_port else f"{source_port} -> {target_port}"
            labels.setdefault((source_node, target_node), []).append(label)
        for (source_node, target_node), names in labels.items():
            lines.append(f'  {_id(source_node)} -- "{"<br/>".join(names)}" --> {_id(target_node)}')
        return "\n".join(lines) + "\n"

    @staticmethod
    def node_id(node_id: str) -> str:
        """The id of a node of the document in the diagram (e.g. to attach click handlers)."""
        return _id(node_id)


def _id(node_id: str) -> str:
    return f"n_{node_id}"   # prefix: Mermaid keywords like "end" cannot be ids


def _ports(ports: dict[str, str]) -> str:
    return "<br/>".join(f"{name}: {type_name}" for name, type_name in ports.items())
