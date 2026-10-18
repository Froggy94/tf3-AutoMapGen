"""The planned road network as a document: nodes with their game height, ways of one kind each."""

from __future__ import annotations

from dataclasses import dataclass

from automapgen.graph import data_type
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec


@data_type("road_plan", "Planned road network: {targets: [{name, kind, size, u, v}], nodes: [[u, v, game height], "
           "...], ways: [{kind: normal|bridge|tunnel, nodes: [node index, ...]}]}; ways sharing a node meet in a "
           "junction.", codec=JsonDocumentCodec(lambda data: RoadPlan(data)),
           preview=lambda r: f"{len(r.data['ways'])} ways")
@dataclass(frozen=True)
class RoadPlan(JsonDocument):
    def lines(self) -> list[list[tuple[float, float]]]:
        """Every way as a line of map fractions (u, v)."""
        nodes = self.data["nodes"]
        return [[(nodes[i][0], nodes[i][1]) for i in way["nodes"]] for way in self.data["ways"]]
