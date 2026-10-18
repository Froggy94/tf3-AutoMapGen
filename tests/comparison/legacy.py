"""The legacy project (functional template) next to this one: its map runs and the comparison of a map graph's output
folder with a legacy output folder.

A graph run reads the elevation model, the OSM files and the game files of the legacy project; WorldCover needs
network access. Every file of the legacy run must be reproduced byte-equal, except the differences listed in
docsAI/notes/legacy_differences.md, which are allowed only where they are expected.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt

from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.engine import Engine
from automapgen.graph.library import FolderLibrary
from automapgen.graph.node import OutputTarget
from automapgen.graph.paramsets import ParameterSet
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT.parent / "TF3 Hightmap"
LEGACY_PYTHON = LEGACY / ".venv" / "Scripts" / "python.exe"
DEM = LEGACY / "data" / "INS4_GEDTM30.tif"
PROJECTS = ROOT / "configs" / "projects"
PROJECT = "GoldenTest"

# settings of the legacy golden-master run (tests/golden/map_run.py of the legacy project): nothing is copied into
# the game folders; the small region gets at most 3 towns and a road connection for every place
LEGACY_SETTINGS: dict[str, dict[str, Any]] = {
    "local": {"local.user_data_dir": ""},
    "towns": {"towns.plan.max_towns": 3, "towns.plan.min_distance_game_m": 800},
    "roads": {"roads.places.min_population": 0},
}
LEGACY_STEPS = "heightmap,rivers,paths,osm_roads,towns,industries,mod,roads,names,path_streets"

NOT_COMPARED = {
    f"{PROJECT}_rivers_preview.png": "English labels",
    f"{PROJECT}_rivers_profiles.png": "English labels",
    f"{PROJECT}_resources_preview.png": "English labels",
    f"{PROJECT}_sites_preview.png": "English labels",
    f"{PROJECT}_industries_preview.png": "English labels",
    f"{PROJECT}_resources.npz": "not written (no consumer)",
    "mods": "the map's own mods, compared on their own (compared_mod_files)",
    f"{PROJECT}_roads_plan.json": "road network redesigned (ADR 0016): junctions, node heights",
    f"{PROJECT}_roads_plan.png": "road network redesigned (ADR 0016): no gaps at branches",
}
RENAMED = {   # legacy name -> name here
    f"{PROJECT}_industries_staedte.lua": f"{PROJECT}_industries_towns_only.lua",   # English name (2026-10-18)
}
NOT_COMPARED_MODS = {
    "DHE_goldentest_roads/": "road network redesigned (ADR 0016): the mod builds heights, structures, junctions",
    "DHE_goldentest_path_streets/content/dhe_path_streets/dhe_path_streets.script.lua":
        "gaps near the legacy run's planned roads; map and map_parcels plan no roads (ADR 0016)",
}
WATERFALL_PREVIEW = "dhe_waterfall_preview_2x2.png"   # legacy: next to the mods; here: <project>_waterfall_preview.png
FOREST_CORNER = f"{PROJECT}_forest.png"              # legacy bug at the map corner
PATHS_PREVIEW = f"{PROJECT}_paths_preview.png"       # drawn from the plan rounded to 7 decimals: a few pixels
CLIMA_SUFFIX = ".clima.lua"                          # import layers named by key (= mask file), legacy: labels
_LAYERS = "\tlayers = {"                             # the import layers (the biomes before them keep their names)
_LAYER_NAME = re.compile(r'(\t+key = "(\w+)",\n\t+displayName = _\(")[^"]*("\),)')


def legacy_available() -> bool:
    return (LEGACY / "tf3gen").is_dir()


def run_graph(graph_id: str, project: str, folder: Path) -> Path:
    """Runs a built-in graph with a project of configs/projects (elevation model of the legacy project); returns the
    output folder."""
    parameters = ParameterSet.load(PROJECTS / f"{project}.toml")
    parameters = ParameterSet(parameters.graph, {**parameters.params, "dem_path": str(DEM)}, parameters.nodes,
                              "test", parameters.defaults)
    library = FolderLibrary([GRAPHS_DIR])
    document = library.get(graph_id)
    assert document is not None
    plan = GraphCompiler(NodeCatalog.from_modules(BUILTIN_MODULES), library).compile(document, parameters)
    Engine(output=OutputTarget(folder, PROJECT), downloads=LEGACY / "cache", game=legacy_game_folder()).run(plan, {})
    return folder


def run_legacy(folder: Path, settings: Mapping[str, Mapping[str, Any]]) -> Path:
    """Runs the legacy generate.py with all steps on its golden-master region and extra settings (by section, the
    GUI's format); returns the project's output folder."""
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    merged = {section: {**values} for section, values in LEGACY_SETTINGS.items()}
    for section, values in settings.items():
        merged.setdefault(section, {}).update(values)
    env = dict(os.environ, TF3_PROJECT=PROJECT, TF3_MAP_CONFIG="golden_test", TF3_REGION="golden_test",
               TF3_OUTPUT_DIR=str(folder), TF3_SETTINGS=json.dumps(merged), PYTHONIOENCODING="utf-8")
    log = folder.parent / f"{folder.name}.log"
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run([str(LEGACY_PYTHON), "generate.py", "--steps", LEGACY_STEPS], cwd=LEGACY, env=env,
                                stdout=stream, stderr=subprocess.STDOUT, check=False)
    assert result.returncode == 0, f"legacy generate.py failed, see {log}"
    return folder / PROJECT


def run_legacy_python(code: str, *args: str) -> str:
    """Runs Python code with the legacy project's classes (its venv, its folder as working directory); returns what
    the code prints."""
    result = subprocess.run([str(LEGACY_PYTHON), "-c", code, *args], cwd=LEGACY, capture_output=True, text=True,
                            encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"), check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout


def legacy_game_folder() -> Path | None:
    """The game installation the legacy project uses (its configs/local.toml)."""
    local = LEGACY / "configs" / "local.toml"
    if not local.is_file():
        return None
    game = tomllib.loads(local.read_text(encoding="utf-8")).get("game_dir")
    return Path(game) if game else None


def compared_files(legacy_folder: Path) -> list[str]:
    """The files of a legacy output folder a map graph must reproduce."""
    return sorted(p.name for p in legacy_folder.iterdir() if p.name not in NOT_COMPARED) \
        if legacy_folder.is_dir() else []


def our_name(legacy_name: str) -> str:
    """The name of a legacy output file here."""
    return RENAMED.get(legacy_name, legacy_name)


def compared_mod_files(legacy_mods: Path) -> list[str]:
    """Files of the mods of a legacy run (paths below its mods folder) a map graph must reproduce."""
    if not legacy_mods.is_dir():
        return []
    paths = (p.relative_to(legacy_mods).as_posix() for p in legacy_mods.rglob("*")
             if p.is_file() and p.name != WATERFALL_PREVIEW)
    return sorted(p for p in paths if not any(p.startswith(folder) for folder in NOT_COMPARED_MODS))


def assert_file_equal(ours: Path, theirs: Path) -> None:
    """Byte-equal, except the documented differences."""
    assert ours.is_file(), f"{ours.name} is not written"
    if ours.name == FOREST_CORNER:
        _assert_pixels(ours, theirs, lambda shape: distance_transform_edt(np.ones(shape, dtype=bool)) <= 4)
    elif ours.name == PATHS_PREVIEW:
        _assert_pixels(ours, theirs, None, max_share=0.0001)
    elif ours.name.endswith(CLIMA_SUFFIX):
        biomes, layers = theirs.read_text(encoding="utf-8").split(_LAYERS)
        named_by_key = _LAYER_NAME.sub(lambda m: f"{m[1]}{m[2]}{m[3]}", layers)
        assert ours.read_text(encoding="utf-8") == f"{biomes}{_LAYERS}{named_by_key}"
    else:
        assert ours.read_bytes() == theirs.read_bytes()


def _assert_pixels(ours: Path, theirs: Path, allowed: object, max_share: float = 0.0) -> None:
    a, b = np.asarray(Image.open(ours)), np.asarray(Image.open(theirs))
    assert a.shape == b.shape
    differ = a != b if a.ndim == 2 else (a != b).any(axis=2)
    if callable(allowed):
        differ &= ~allowed(differ.shape)
    assert differ.mean() <= max_share, f"{differ.mean() * 100:.4f} % of the pixels differ"
