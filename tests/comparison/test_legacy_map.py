"""Comparison with the legacy project's map runs on its golden-master region.

- `map`: with the stored golden-master run of the legacy project (its
  tests/golden/run).
- `map_parcels`: with a fresh legacy run whose fields come from the landcover cut into parcels (setting
  terrain.landcover.fields.source = "raster"), including the farm grid _parcels.json.
Both with their climate mod and its waterfall preview and the map's own mods (path streets, industry
names; legacy: in the project folder, here: next to the climate mod); the road network is redesigned (ADR 0016)
and not compared.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.comparison.legacy import (
    LEGACY,
    LEGACY_PYTHON,
    PROJECT,
    WATERFALL_PREVIEW,
    assert_file_equal,
    compared_files,
    compared_mod_files,
    legacy_available,
    run_graph,
    run_legacy,
)

GOLDEN = LEGACY / "tests" / "golden" / "run" / PROJECT
GOLDEN_MODS = GOLDEN.parent / "mods"
GOLDEN_MAP_MODS = GOLDEN / "mods"

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not legacy_available() or not GOLDEN.is_dir() or not LEGACY_PYTHON.is_file(),
                                 reason="legacy project with its venv and a golden-master run not next to this one")]


@pytest.fixture(scope="module")
def map_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return run_graph("map", "golden_test", tmp_path_factory.mktemp("map"))


@pytest.fixture(scope="module")
def parcels_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return run_graph("map_parcels", "golden_test_parcels", tmp_path_factory.mktemp("map_parcels"))


@pytest.fixture(scope="module")
def legacy_parcels(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return run_legacy(tmp_path_factory.mktemp("legacy") / "run",
                      {"terrain": {"terrain.landcover.fields.source": "raster"}})


@pytest.mark.parametrize("name", compared_files(GOLDEN))
def test_map_equals_legacy_golden_master(map_run: Path, name: str) -> None:
    assert_file_equal(map_run / name, GOLDEN / name)


@pytest.mark.parametrize("path", compared_mod_files(GOLDEN_MODS))
def test_map_mods_equal_legacy_golden_master(map_run: Path, path: str) -> None:
    assert_file_equal(map_run / "mods" / path, GOLDEN_MODS / path)


@pytest.mark.parametrize("path", compared_mod_files(GOLDEN_MAP_MODS))
def test_map_own_mods_equal_legacy_golden_master(map_run: Path, path: str) -> None:
    assert_file_equal(map_run / "mods" / path, GOLDEN_MAP_MODS / path)


def test_map_waterfall_preview_equals_legacy_golden_master(map_run: Path) -> None:
    assert_file_equal(map_run / f"{PROJECT}_waterfall_preview.png", GOLDEN_MODS / WATERFALL_PREVIEW)


@pytest.mark.parametrize("name", [*compared_files(GOLDEN), f"{PROJECT}_parcels.json"])
def test_map_parcels_equals_legacy(parcels_run: Path, legacy_parcels: Path, name: str) -> None:
    assert (legacy_parcels / name).is_file(), f"the legacy run did not write {name}"
    assert_file_equal(parcels_run / name, legacy_parcels / name)


@pytest.mark.parametrize("path", compared_mod_files(GOLDEN_MODS))
def test_map_parcels_mods_equal_legacy(parcels_run: Path, legacy_parcels: Path, path: str) -> None:
    assert_file_equal(parcels_run / "mods" / path, legacy_parcels.parent / "mods" / path)


@pytest.mark.parametrize("path", compared_mod_files(GOLDEN_MAP_MODS))
def test_map_parcels_own_mods_equal_legacy(parcels_run: Path, legacy_parcels: Path, path: str) -> None:
    assert_file_equal(parcels_run / "mods" / path, legacy_parcels / "mods" / path)


def test_legacy_parcels_run_writes_no_other_files(legacy_parcels: Path) -> None:
    assert set(compared_files(legacy_parcels)) == {*compared_files(GOLDEN), f"{PROJECT}_parcels.json"}
