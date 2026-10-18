"""Lua files against the legacy project: the writer gives every Lua data file of the legacy golden-master mod in the
same layout (materials, which the legacy project writes from a text template: the same content), and the game files
read through `GameFiles` (installation) equal the legacy project's copy of the original climates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from automapgen.lua import DataDocument, TreeSignature, to_plain
from automapgen.nodes.gamefiles import GameFiles, LuaData
from tests.comparison.legacy import LEGACY, legacy_available, legacy_game_folder

MODS = LEGACY / "tests" / "golden" / "run" / "mods"
ORIGINALS = LEGACY / "default generator Scripte"
GAME = legacy_game_folder()

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not legacy_available(), reason="legacy project not next to this one")]

MOD_LUA = sorted(p for p in MODS.rglob("*.lua")) if MODS.is_dir() else []
ORIGINAL_FILES = sorted(p for p in (ORIGINALS / "climates").rglob("*") if p.is_file()) if ORIGINALS.is_dir() else []


@pytest.mark.parametrize("path", MOD_LUA, ids=lambda p: p.name)
def test_writer_reproduces_the_legacy_mod_files(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    if path.name.endswith(".tmat.lua"):
        assert to_plain(LuaData(text).document().root) == to_plain(DataDocument.parse(text).root)
    else:
        assert LuaData(text).text == text
    if path.name.endswith(".tree.lua"):
        document = DataDocument.parse(text)
        assert TreeSignature(DataDocument.parse(document.to_lua())).signature() == TreeSignature(document).signature()


@pytest.mark.skipif(GAME is None or not GAME.is_dir(), reason="game installation not found")
@pytest.mark.parametrize("path", ORIGINAL_FILES, ids=lambda p: p.relative_to(ORIGINALS).as_posix())
def test_game_installation_equals_the_legacy_copy_of_the_originals(path: Path) -> None:
    assert GAME is not None
    assert GameFiles.at(GAME).read(path.relative_to(ORIGINALS).as_posix()) == path.read_bytes()
