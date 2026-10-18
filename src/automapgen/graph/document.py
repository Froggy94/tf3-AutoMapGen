"""Graph documents: the composition as data, written by hand or by a graph editor (JSON, `<id>.graph.json`).

    {
      "$schema": "../schemas/graph.schema.json",
      "id": "forest_mask",
      "inputs":  {"height": "field", "landcover": "labels"},
      "outputs": {"forest": "mask"},
      "params":  {"tree_line_m": {"to": ["below_tree_line.limit"], "value": 1800}},
      "nodes":   {"below_tree_line": {"type": "mask.below", "params": {"limit": 1800}, "note": "..."}},
      "links":   [["inputs.height", "below_tree_line.value"], ["below_tree_line.mask", "outputs.forest"]],
      "ui":      {"below_tree_line": {"x": 40, "y": 80}}
    }

- `inputs` / `outputs` are the graph's interface; in links they are reserved node ids: `inputs.<name>` reads a graph
  input, `outputs.<name>` sets a graph output.
- `params` exposes parameters to the outside (parameter sets, GUI, a parent graph): one name sets one or more node
  parameters (`node.param`); `value` is optional.
- A node of type `graph:<id>` is another graph used as one node (subgraph): its ports are that graph's inputs and
  outputs, its parameters that graph's exposed parameters.
- `extends` names a base graph; the document then holds only differences: new or replaced `nodes`, `remove`d nodes
  (with their links), additional `links`, `unlink`ed links (see GraphComposer).
- `title` is a short display name for diagrams and editors; `description` says what the graph does, where and how.
- `ui` belongs to the editor only (positions, groups); it has no effect on results.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from automapgen.graph.errors import GraphError, Problem

GRAPH_INPUTS = "inputs"
GRAPH_OUTPUTS = "outputs"
SUBGRAPH_PREFIX = "graph:"

type Link = tuple[str, str]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeEntry(_Strict):
    """One node of a graph: its type (node type id or `graph:<id>`), parameter values and a note for readers."""

    type: str
    version: int | None = None
    """Node type version the parameters were written for; missing = the current one."""
    params: dict[str, JsonValue] = Field(default_factory=dict)
    note: str = ""

    @property
    def subgraph(self) -> str | None:
        return self.type.removeprefix(SUBGRAPH_PREFIX) if self.type.startswith(SUBGRAPH_PREFIX) else None


class ExposedParam(_Strict):
    """A parameter the graph offers to the outside; it sets the node parameters listed in `to` (`node.param`)."""

    to: list[str]
    description: str = ""
    value: JsonValue = None
    """Value used when nobody sets it; only if given (an absent value leaves the node parameters as they are)."""

    @property
    def has_value(self) -> bool:
        return "value" in self.model_fields_set


class GraphDocument(_Strict):
    """A graph: interface, nodes, links, exposed parameters - or, with `extends`, the differences to a base graph."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal[1] = 1
    id: str
    title: str = ""
    """Short display name (diagrams, editor), e.g. "Terraforming"; the id is shown when it is empty."""
    description: str = ""
    extends: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    params: dict[str, ExposedParam] = Field(default_factory=dict)
    nodes: dict[str, NodeEntry] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    unlink: list[Link] = Field(default_factory=list)
    ui: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _differences_need_a_base(self) -> GraphDocument:
        if self.extends is None and (self.remove or self.unlink):
            raise ValueError("'remove' and 'unlink' are only allowed together with 'extends'")
        return self


class GraphFile:
    """Reads and writes graph documents; writing is stable (same document = same text) for clean diffs."""

    SUFFIX = ".graph.json"

    @staticmethod
    def load(path: Path) -> GraphDocument:
        try:
            return GraphDocument.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as e:
            raise GraphError([Problem(f"{path.name} {'.'.join(map(str, err['loc']))}".strip(), err["msg"])
                              for err in e.errors()]) from e

    @staticmethod
    def dumps(document: GraphDocument) -> str:
        return document.model_dump_json(by_alias=True, exclude_defaults=True, indent=2) + "\n"

    @classmethod
    def save(cls, document: GraphDocument, path: Path) -> None:
        path.write_text(cls.dumps(document), encoding="utf-8")

    @staticmethod
    def json_schema() -> dict[str, object]:
        """JSON schema of graph documents (editor validation and completion)."""
        return GraphDocument.model_json_schema(by_alias=True)
