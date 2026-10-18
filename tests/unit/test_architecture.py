"""Architecture rules that are checked automatically."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "automapgen"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(item, ast.Import):
            names |= {a.name for a in item.names}
        elif isinstance(item, ast.ImportFrom) and item.module:
            names.add(item.module)
    return names


def test_graph_framework_knows_no_domain() -> None:
    """automapgen.graph imports nothing else of automapgen: nodes and applications depend on it, never the reverse."""
    offenders = {f"{p.name}: {name}" for p in (SRC / "graph").glob("*.py") for name in _imports(p)
                 if name.startswith("automapgen") and not name.startswith("automapgen.graph")}
    assert not offenders


def test_node_authors_need_only_the_public_api() -> None:
    """Built-in node packages use the graph framework only through `automapgen.graph` (the public node API)."""
    offenders = {f"{p.relative_to(SRC)}: {name}" for p in (SRC / "nodes").rglob("*.py") for name in _imports(p)
                 if name.startswith("automapgen.graph.")}
    assert not offenders


def test_lua_format_library_stands_alone() -> None:
    """automapgen.lua (the game's Lua data files) knows neither the graph framework nor the nodes."""
    offenders = {f"{p.name}: {name}" for p in (SRC / "lua").glob("*.py") for name in _imports(p)
                 if name.startswith("automapgen") and not name.startswith("automapgen.lua")}
    assert not offenders
