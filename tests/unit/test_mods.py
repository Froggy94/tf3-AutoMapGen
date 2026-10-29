"""Mod files, the mod package and folder, the names and texts of a climate mod, and copies into the game."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from automapgen.app.local_settings import LocalSettings
from automapgen.graph import NodeContext
from automapgen.graph.node import OutputTarget
from automapgen.graph.paramsets import ParameterSet
from automapgen.nodes import files, mods
from automapgen.nodes.climate import package as climate
from automapgen.nodes.climate.preset import ClimatePreset
from automapgen.nodes.gamefiles import GameUserData
from automapgen.nodes.images import Image
from automapgen.nodes.maps import MapFormat
from automapgen.nodes.mods import ModFiles, ModInfo, PublishingSettings, Translations
from automapgen.nodes.towns import TextDocument

PRESET = ParameterSet.load(Path(__file__).resolve().parents[2] / "configs" / "presets" / "alpine_realistic.toml")
PUBLISHING = PublishingSettings.model_validate(PRESET.defaults["publishing"])
TEXTS = climate.ClimateTexts.model_validate(PRESET.defaults["climate_texts"])
CLIMATE = ClimatePreset.model_validate(PRESET.defaults["climate"])


def test_mod_files_are_sorted_and_unique(tmp_path: Path) -> None:
    files = ModFiles.merged([ModFiles.of({"b": b"2"}), ModFiles.of({"a": b"1"})])
    codec = mods._ZipCodec()

    codec.save(files, tmp_path / "m.zip")

    assert files.paths() == ["a", "b"]
    assert codec.load(tmp_path / "m.zip") == files
    with pytest.raises(ValueError, match="given twice"):
        ModFiles.merged([files, ModFiles.of({"a": b"3"})])


def test_package_describes_the_content(tmp_path: Path) -> None:
    content = ModFiles.of({"content/climates/x/x.clima.lua": b"x"})
    info = ModInfo({"name": "N", "summary": "S", "description": "D", "tags": ["Temperate"]})
    thumbnail = Image(np.zeros((8, 14, 3), dtype=np.uint8))

    mod = mods.package((content,), "MOD-1", info, mods.PackageParams(publishing=PUBLISHING),
                       Translations({"Alps": "Alpen"}), thumbnail)
    files = dict(mod.files)

    assert sorted(files) == ["_content.json", "_metadata/0.png", "_metadata/modinfo.json",
                             "content/climates/x/x.clima.lua", "image_00.tga", "mod.json", "strings.json"]
    assert json.loads(files["_content.json"]) == {"archives": None, "files": ["climates/x/x.clima.lua"]}
    assert json.loads(files["mod.json"])["modId"] == "MOD-1"
    assert json.loads(files["_metadata/modinfo.json"])["authors"] == [{"name": "DHEModding", "role": "CREATOR"}]
    assert json.loads(files["strings.json"]) == {"de": {"Alps": "Alpen"}}
    with pytest.raises(ValueError, match="outside content/"):
        mods.package((ModFiles.of({"x.lua": b""}),), "M", info, mods.PackageParams(publishing=PUBLISHING))


def test_mod_folder_replaces_an_older_one(tmp_path: Path) -> None:
    context = NodeContext("mod", print, lambda f: None, lambda: False, OutputTarget(tmp_path, "P"))
    (tmp_path / "mods" / "M" / "old.txt").parent.mkdir(parents=True)
    (tmp_path / "mods" / "M" / "old.txt").write_text("old")

    mods.mod_folder(ModFiles.of({"content/a/b.lua": b"b"}), "M", mods.ModFolderParams(), context)

    assert [p.relative_to(tmp_path).as_posix() for p in (tmp_path / "mods").rglob("*") if p.is_file()] == [
        "mods/M/content/a/b.lua"]


def test_climate_naming() -> None:
    naming = climate.ClimateNaming("DHE", "Alpine Realistic", "BETA", 540.4, 2.5, height_scale=2.5)

    assert naming.label == "DHE Alpine Realistic 540m Scale 0.4 BETA"
    assert naming.mod_id == "DHE-Alpine-Realistic-540m-Scale-0_4-BETA"
    assert naming.stamp == "540m\n0.4"
    assert climate.ClimateNaming("DHE", "X", "", 0, 1.5, height_scale=1.5).label == "DHE X 0m Scale 0.667"


def test_climate_naming_with_height_scale() -> None:
    naming = climate.ClimateNaming("DHE", "Alpine Realistic", "BETA", 0, 2, height_scale=1)

    assert naming.label == "DHE Alpine Realistic 0m Scale 0.5 Heights 1 BETA"
    assert naming.mod_id == "DHE-Alpine-Realistic-0m-Scale-0_5-Heights-1-BETA"
    assert naming.stamp == "0m\n0.5\n1"


def test_climate_identity_and_texts() -> None:
    params = climate.ModTextParams(climate=CLIMATE, publishing=PUBLISHING, climate_texts=TEXTS)
    fmt = MapFormat(3.072, 3.072, 2, 2)

    identity = climate.identity(540.0, fmt, params)
    texts = climate.mod_texts(540.0, fmt, params)

    assert identity.identity.identity().clima_file == "dhe-alpine-realistic-540m-scale-0_5-beta_temperate.clima"
    assert identity.stamp == "540m\n0.5"
    assert texts.info.data["name"] == "DHE Alpine Realistic 540m Scale 0.5 BETA"
    assert "Baumgrenze bei 2100 m" in texts.info.data["description"]
    assert "Unchanged temperate economy" in texts.info.data["description"]
    assert texts.translations.data[identity.identity.data["description"]].startswith("Gemäßigt, Maßstab 0,5")


def test_sinks_copy_to_a_named_destination(tmp_path: Path) -> None:
    game = tmp_path / "game"
    target = OutputTarget(tmp_path / "out", "P", GameUserData(game).destinations())
    context = NodeContext("sink", print, lambda f: None, lambda: False, target)
    (game / "staging_area" / "M" / "old.txt").parent.mkdir(parents=True)
    (game / "staging_area" / "M" / "old.txt").write_text("old")

    files.text_file(TextDocument("x"), files.FileParams(name="_towns.lua", copy_to="towns_industries"), context)
    files.text_file(TextDocument("y"), files.FileParams(name="_not_copied.lua"), context)
    files.text_file(TextDocument("z"), files.FileParams(name="_unknown.lua", copy_to="elsewhere"), context)
    mods.mod_folder(ModFiles.of({"content/a.lua": b"a"}), "M", mods.ModFolderParams(copy_to="staging_area"), context)

    assert sorted(p.relative_to(game).as_posix() for p in game.rglob("*") if p.is_file()) == [
        "staging_area/M/content/a.lua", "towns_industries/P_towns.lua"]


def test_local_settings_name_the_user_data_folder(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text('game_folder = "G:/TF3"\nuser_data_folder = "C:/userdata/local"\n', encoding="utf-8")

    assert LocalSettings.load(path) == LocalSettings(Path("G:/TF3"), Path("C:/userdata/local"))
    assert LocalSettings.load(tmp_path / "missing.toml") == LocalSettings()
