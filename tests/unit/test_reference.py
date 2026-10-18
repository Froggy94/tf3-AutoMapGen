"""The generated reference for AI assistants: field lines from schemas, and the committed files are current."""

from __future__ import annotations

from pathlib import Path

from automapgen.app.reference import GraphReference, NodeReference, SchemaText
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.library import FolderLibrary
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES

REFERENCE = Path(__file__).resolve().parents[2] / "docsAI" / "reference"


def test_field_lines_name_type_default_unit_limits_and_links() -> None:
    text = SchemaText({"Inner": {"properties": {"a": {"type": "number"}}},
                       "Maybe": {"anyOf": [{"$ref": "#/$defs/Inner"}, {"type": "null"}]}})
    schema = {"required": ["limits"], "properties": {
        "limits": {"type": "array", "items": {"type": "number"}, "description": "Borders\n  between classes."},
        "radius_m": {"type": "number", "default": 80.0, "unit": "m", "exclusiveMinimum": 0},
        "inner": {"$ref": "#/$defs/Maybe", "default": None},
        "mode": {"enum": ["box", "nearest"], "default": "box"}}}

    assert text.field_lines(schema) == [
        "- `limits` (list of number, required): Borders between classes.",
        "- `radius_m` (number, default `80.0`, unit m, > 0)",
        "- `inner` ([Inner](#inner) \\| none, default `null`)",
        '- `mode` ("box" \\| "nearest", default `"box"`)']


def test_committed_reference_is_current() -> None:
    """If this fails: `automapgen reference` and commit docsAI/reference/."""
    nodes = NodeReference(NodeCatalog.discover(BUILTIN_MODULES)).markdown()
    graphs = GraphReference(FolderLibrary([GRAPHS_DIR])).markdown()

    assert (REFERENCE / "nodes.md").read_text(encoding="utf-8") == nodes
    assert (REFERENCE / "graphs.md").read_text(encoding="utf-8") == graphs
