"""The climate mod's parts against the legacy project, byte-equal: the trees of Alpine Realistic against the
golden-master mod (reference height 540 m, scale 1:2); what the golden master does not contain - the Brenner
transition, its mask, Classic Trees, the own economy - against what the legacy classes make for it."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from rasterio.crs import CRS

from automapgen.graph import NodeContext, merged_tables
from automapgen.graph.paramsets import ParameterSet
from automapgen.nodes.climate import classic_trees, economy, trees, zones
from automapgen.nodes.climate.package import ClimateIdentityData
from automapgen.nodes.climate.preset import ClimatePreset, EconomySettings, Transition
from automapgen.nodes.industries import IndustrySettings
from automapgen.nodes.maps import MapFormat, MapFrame, Rotation, SourceBounds
from tests.comparison.legacy import (
    LEGACY,
    LEGACY_PYTHON,
    ROOT,
    legacy_available,
    legacy_game_folder,
    run_legacy_python,
)

MOD_ID = "DHE-Alpine-Realistic-540m-Scale-0_5-BETA"
CLIMATE = LEGACY / "tests" / "golden" / "run" / "mods" / MOD_ID / "content" / "climates" / MOD_ID.lower()
GAME = legacy_game_folder()

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not legacy_available() or not CLIMATE.is_dir() or GAME is None
                                 or not LEGACY_PYTHON.is_file(),
                                 reason="legacy project with its venv and a golden-master run, or the game, not found")]

FORMAT = MapFormat(3.072, 3.072, 2, 2)
CLASSIC = {"enabled": True}          # switches on the Classic Trees whose values the preset keeps

LEGACY_TREES = """
import dataclasses
import sys
from pathlib import Path

from tf3gen.core.lua.node_graph import NodeGraph
from tf3gen.core.units import Elevation
from tf3gen.landscape.vegetation import ClassicTrees
from tf3gen.mods.climate.classic_trees import ClassicTreesScript
from tf3gen.mods.climate.game_heights import GameHeights
from tf3gen.mods.climate.import_tree import ImportTreeBuilder
from tf3gen.mods.climate.material_tree import MaterialPaths, MaterialTreeBuilder
from tf3gen.mods.climate.settings import ClimateSettingsFiles
from tf3gen.mods.climate.temperate_adapter import TemperateAdapter
from tf3gen.sources.game import GameInstall

landscape_id, classic, out = sys.argv[1], sys.argv[2] == "classic", Path(sys.argv[3])
settings, landscape = ClimateSettingsFiles(Path("configs")).load(None, landscape_id or None)
if classic:   # the values of the preset file (enabled = false there)
    trees = ClassicTrees({"dhe_classic_pine": (20, ["sct_pn_01"]),
                          "dhe_classic_broadleaf": (21, ["eu_lnd_01", "sgr_mpl_01", "shgl_oak_01"])},
                         "dhe_classic_pine", "dhe_classic_broadleaf", 0.3, Elevation(1400), 0.35, 0.03)
    primary = landscape.primary
    primary = dataclasses.replace(primary, forest=dataclasses.replace(primary.forest, classic=trees))
    landscape = dataclasses.replace(landscape, primary=primary)
    ClassicTreesScript(trees).write(out)
adapter = TemperateAdapter()
game = GameInstall(Path("."), settings.originals_dir / adapter.base_climate)
material = NodeGraph(game.original(adapter.originals.material_tree))
imports = NodeGraph(game.original(adapter.originals.import_tree))
heights = GameHeights(Elevation(540), 0.5)
p = landscape.primary
adapter.apply_height_limits(material, imports, heights, p.heights, p.ground.steep_rock, p.top)
MaterialTreeBuilder(material, adapter, landscape, MaterialPaths(sys.argv[4])).build()
ImportTreeBuilder(imports, adapter, landscape, heights, 0.5).build()
material.document.write(out / "material.tree.lua")
imports.document.write(out / "import.tree.lua")
"""

LEGACY_ECONOMY = """
import sys
from pathlib import Path

from tf3gen.mods.climate.economy import EconomyPatcher
from tf3gen.mods.climate.params import EconomyParams
from tf3gen.planning.industry_mod import industry_mod_file
from tf3gen.sources.game import GameInstall

extra = sys.argv[1] == "extra"
mod = industry_mod_file(Path("configs")).load()
params = EconomyParams(True, extra, "Alps (all industries)" if extra else "Alps", "3.0 / 41.0", ["oil_platform"],
                       mod.extra_industries, mod.extra_cargos, mod.name)
sys.stdout.write(EconomyPatcher(params, GameInstall(Path(sys.argv[2]), Path("."))).patched())
"""


def _context(name: str, game: Path | None = None) -> NodeContext:
    return NodeContext(name, lambda message: None, lambda f: None, lambda: False, game=game)


def _defaults(name: str) -> dict[str, Any]:
    return dict(ParameterSet.load(ROOT / "configs" / name).defaults)


def _legacy_defaults(name: str) -> dict[str, Any]:
    """The values of a preset or landscape file with the alpine ground of the legacy project (changed
    2026-10-17): gr_br_sub with alpine grass and rock patches; the southern look keeps the base. Without a transition
    the legacy climate keeps the base climate's river layer (left out since 2026-10-18). The Brenner landscape's
    waterfall area was a setting of the legacy Brenner region (moved into the landscape 2026-10-18)."""
    defaults = copy.deepcopy(_defaults(name))
    look = defaults["climate"]["surfaces"]["alpine_ground"]["look"]
    grass, scree = look["patches"]["subarctic_grass"], look["patches"]["ground_scree"]
    look["base"] = {"kind": "game", "name": "gr_br_sub"}
    look["patches"] = {"alpine_grass": _with_material(grass, "grass_alpine"),
                       "alpine_rock": _with_material(scree, "rock")}
    if "transition" not in defaults:
        defaults["climate"]["slots"]["original"] = {"rivers": 0, "mountains": 1}
    if "transition" in defaults:
        preset = _defaults("presets/alpine_realistic.toml")["climate"]["surfaces"]["waterfall"]["area"]
        defaults["climate"]["surfaces"]["waterfall"]["area"] = copy.deepcopy(preset)
        south = defaults["transition"]["secondary"]["surfaces"]["alpine_ground"]["look"]
        del south["base"]
        south["patches"] = {"alpine_grass": south["patches"]["subarctic_grass"],
                            "alpine_rock": south["patches"]["ground_scree"]}
    return defaults


def _with_material(patch: dict[str, Any], material: str) -> dict[str, Any]:
    return merged_tables(patch, {"output": {"material": {"kind": "game", "name": material}}})


@pytest.fixture(scope="module")
def climate() -> ClimatePreset:
    return ClimatePreset.model_validate(_legacy_defaults("presets/alpine_realistic.toml")["climate"])


@pytest.fixture(scope="module")
def originals(climate: ClimatePreset) -> trees.ClimateOriginals:
    return trees.originals(trees.OriginalsParams(climate=climate), _context("originals", GAME))


def _trees(originals: trees.ClimateOriginals, params: trees.ClimateParams) -> tuple[str, str]:
    material = trees.material_tree(originals.material_tree, 540.0, FORMAT, MOD_ID, params, _context("material"))
    imports = trees.import_tree(originals.import_tree, 540.0, FORMAT, params, _context("import"))
    return material.text, imports.text


def _legacy_trees(folder: Path, landscape: str = "", classic: bool = False) -> tuple[str, str]:
    run_legacy_python(LEGACY_TREES, landscape, "classic" if classic else "-", str(folder), MOD_ID)
    return ((folder / "material.tree.lua").read_text(encoding="utf-8"),
            (folder / "import.tree.lua").read_text(encoding="utf-8"))


def test_trees_equal_the_legacy_golden_master(climate: ClimatePreset, originals: trees.ClimateOriginals) -> None:
    material, imports = _trees(originals, trees.ClimateParams(climate=climate))

    assert material == (CLIMATE / f"{MOD_ID.lower()}_clima.tree.lua").read_text(encoding="utf-8")
    assert imports == (CLIMATE / f"{MOD_ID.lower()}_import_gen.tree.lua").read_text(encoding="utf-8")


def test_transition_trees_equal_legacy(originals: trees.ClimateOriginals, tmp_path: Path) -> None:
    landscape = _legacy_defaults("landscapes/brenner_north_south.toml")
    params = trees.ClimateParams(climate=ClimatePreset.model_validate(landscape["climate"]),
                                 transition=Transition.model_validate(landscape["transition"]))

    material, imports = _trees(originals, params)

    assert "lc meadow transition strength" in material            # the transition is really there
    assert "lc transition ramp" in imports
    assert (material, imports) == _legacy_trees(tmp_path, "brenner_north_south")


def test_classic_trees_equal_legacy(originals: trees.ClimateOriginals, tmp_path: Path) -> None:
    preset = _legacy_defaults("presets/alpine_realistic.toml")["climate"]
    climate = ClimatePreset.model_validate(merged_tables(preset, {"forest": {"classic": CLASSIC}}))

    material, imports = _trees(originals, trees.ClimateParams(climate=climate))
    script = classic_trees.classic_trees(MOD_ID, classic_trees.ClassicTreesParams(climate=climate))

    assert "lc classic pine sel" in imports
    assert (material, imports) == _legacy_trees(tmp_path, classic=True)
    assert script.post_run_script == f"{MOD_ID}::/dhe_classic_trees.script@postRunFn"
    legacy = next(tmp_path.glob("*_classic_trees.script.lua")).read_bytes()   # the legacy script has its own prefix
    assert dict(script.files.files)["content/dhe_classic_trees.script.lua"] == re.sub(
        rb"\w+_classic_trees", b"dhe_classic_trees", legacy)


@pytest.mark.parametrize("extra", [False, True])
def test_own_economy_equals_legacy(originals: trees.ClimateOriginals, extra: bool) -> None:
    identity = ClimateIdentityData({"mod_id": MOD_ID, "label": "", "description": "", "base_climate": "temperate"})
    params = economy.EconomyParams(
        economy=EconomySettings(custom=True, extra_industries=extra),
        industries=IndustrySettings.model_validate(_defaults("industries/temperate.toml")["industries"]))

    result = economy.economy(originals.clima, identity, params, _context("economy", GAME))

    assert result.path == f"{MOD_ID}::/economy/{MOD_ID.lower()}.eco"
    ours = dict(result.files.files)[f"content/economy/{MOD_ID.lower()}.eco.lua"].decode("utf-8")
    assert ours == run_legacy_python(LEGACY_ECONOMY, "extra" if extra else "-", str(GAME))


LEGACY_ZONE_MASK = """
import sys

from tf3gen.core.geo import GeoBounds
from tf3gen.core.rotation import Rotation
from tf3gen.landscape.transition import GradientZone
from tf3gen.landscape.zones import ZoneMask

w, s, e, n, rotation, width, height, start, end = sys.argv[1:]
mask = ZoneMask(GeoBounds(float(w), float(s), float(e), float(n)), Rotation(int(rotation)), int(width),
                int(height)).compute(GradientZone(float(start), float(end)))
print("".join("1" if v else "0" for v in mask.ravel()))
"""


@pytest.mark.parametrize("rotation", list(Rotation))
def test_transition_mask_equals_legacy(rotation: Rotation) -> None:
    transition = Transition.model_validate(
        merged_tables(_defaults("landscapes/brenner_north_south.toml")["transition"],
                      {"zone": {"start_lat": 47.28, "end_lat": 46.716}}))     # the start line crosses the crop
    bounds = SourceBounds(11.4193, 47.2524, 11.5007, 47.3077, True)
    frame = MapFrame(97, 61, bounds, rotation, CRS.from_epsg(4326).to_wkt())

    ours = zones.transition_mask(frame, zones.TransitionMaskParams(transition=transition),
                                 NodeContext("mask", lambda m: None, lambda f: None, lambda: False)).values

    theirs = run_legacy_python(LEGACY_ZONE_MASK, *map(str, (bounds.left, bounds.bottom, bounds.right, bounds.top,
                                                           rotation.value, 97, 61, 47.28, 46.716))).strip()
    assert 0 < ours.mean() < 1
    assert "".join("1" if v else "0" for v in ours.ravel()) == theirs
