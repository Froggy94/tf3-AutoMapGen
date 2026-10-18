"""Climate definition (`*.clima.lua`) and generators (`*.gen.lua`) of a climate mod, edited as Lua objects."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from automapgen.lua import DataDocument, LuaCall, LuaNumber, LuaTable
from automapgen.nodes.climate.preset import LayerSpec

_BASE_GAME_FOLDERS = ("/economy/", "/names/", "/climates/")
_ECONOMY = re.compile(r"^::/economy/(\w+)\.eco$")


def _translated(text: str) -> LuaCall:
    return LuaCall("_", (text,))


@dataclass(frozen=True)
class ClimateIdentity:
    """IDs and names of one climate: `mod_id` (= climate ID), `file_id` (lower case, used for files and folders),
    `base_climate` = folder of the base game climate it is made from (e.g. "temperate")."""

    mod_id: str
    label: str
    description: str
    base_climate: str

    @property
    def file_id(self) -> str:
        return self.mod_id.lower()

    @property
    def clima_file(self) -> str:
        """Must end in "<base climate>.clima": the game recognises the base climate by the file name
        (base/base_mod.lua matchCategory, e.g. level crossings, "tropical.clima")."""
        return f"{self.file_id}_{self.base_climate}.clima"

    @property
    def icon_file(self) -> str:
        """Climate tile in "New map" without extension (the game adds @2x for the large one)."""
        return f"{self.file_id}_{self.base_climate}"

    @property
    def folder(self) -> str:
        """The climate's folder below the mod's content."""
        return f"climates/{self.file_id}"


class ClimaDefinitionEditor:
    """Turns the original climate definition of the base climate into the mod's climate definition."""

    def __init__(self, document: DataDocument, identity: ClimateIdentity) -> None:
        self._doc = document
        self._root = document.root
        self._identity = identity

    def apply(self, layers: Mapping[str, LayerSpec], economy: str | None, map_levels: tuple[float, float],
              original_layers: Mapping[str, int]) -> DataDocument:
        """`economy`: own economy path, or None for the base climate's economy. `map_levels`: game heights of the
        middle and top colour of the map preview (cosmetic). `original_layers`: original layers of the base climate
        that keep their slot (key -> slot as in the original; a transition mask may take the slot of one of them)."""
        i = self._identity
        self._root["nodeTree"] = f"{i.file_id}_clima.tree"
        self._root["id"] = i.mod_id
        desc = self._root.table("desc")
        desc["name"] = _translated(i.label)
        # without icon the game uses a default path that mod climates do not have (purple tile)
        desc["icon"] = f"{i.mod_id}::/climates/{i.file_id}/{i.icon_file}.tga"
        desc["description"] = _translated(i.description)
        self._root["environment"] = _in_base_climate(i.base_climate, str(self._root["environment"]))
        self._root["order"] = LuaNumber.of(11)
        self._point_to_base_game(self._root)
        self._set_economy(economy)
        self._set_map_levels(map_levels)
        self._set_layers(layers, original_layers)
        return self._doc

    def _point_to_base_game(self, table: LuaTable) -> None:
        """Paths "/economy/...", "/names/...", "/climates/..." would be looked up inside the mod -> "::/..."."""
        for key, value in table.keyed():
            if isinstance(value, str) and value.startswith(_BASE_GAME_FOLDERS):
                table[key] = "::" + value
        for _, value in table.entries():
            if isinstance(value, LuaTable):
                self._point_to_base_game(value)

    @staticmethod
    def base_economy(document: DataDocument) -> str:
        """Name of the game economy an original climate definition uses, e.g. "temperate" or "tropic"."""
        match = re.match(r"^/economy/(\w+)\.eco$", str(document.root.get("economy")))
        if match is None:
            raise ValueError(f"climate: unexpected economy {document.root.get('economy')!r} (game update?)")
        return match.group(1)

    def _set_economy(self, economy: str | None) -> None:
        if not _ECONOMY.match(str(self._root.get("economy"))):
            raise ValueError("climate: reference to the base game economy is missing (game update?)")
        if economy is not None:
            self._root["economy"] = economy

    def _set_map_levels(self, levels: tuple[float, float]) -> None:
        entries = [e for e in self._root.table("mapColoring").table("levels").array() if isinstance(e, LuaTable)]
        for entry, height in zip(entries[1:3], levels, strict=True):
            entry["height"] = LuaNumber.of(height)

    def _set_layers(self, layers: Mapping[str, LayerSpec], keep: Mapping[str, int]) -> None:
        """Import dialog layers: own + kept original ones, sorted by key and named by key, so that each layer has the
        name of its mask file `<map>_<key>.png` and stands at the place of the file in the dialog's file list. The
        kept original layers must exist in the original with the same slot (preset `slots.original`)."""
        table = self._root.table("dataMaps").table("layers")
        entries: dict[str, LuaTable] = {}
        slots: dict[str, int] = {}
        for entry in table.array():
            if isinstance(entry, LuaTable):
                entries[str(entry["key"])] = entry
                slot = entry["id"]
                slots[str(entry["key"])] = int(slot.value) if isinstance(slot, LuaNumber) else -1
        wrong = {key: slot for key, slot in keep.items() if slots.get(key) != slot}
        if wrong:
            raise ValueError(f"preset slots.original {wrong} do not match the base climate's layers {slots}")
        entries = {key: entry for key, entry in entries.items() if key in keep}
        for key, entry in entries.items():
            entry["displayName"] = _translated(key)
        for key, layer in layers.items():
            entries[key] = LuaTable([
                ("key", key),
                ("displayName", _translated(key)),
                ("color", LuaTable([(None, LuaNumber(str(c))) for c in layer.color])),
                ("id", LuaNumber.of(layer.slot)),
            ])
        self._root.table("dataMaps")["layers"] = LuaTable([(None, entries[k]) for k in sorted(entries)])


def _in_base_climate(base_climate: str, file: str) -> str:
    """A file the original names relative to its own folder, e.g. "temperate.env" -> base game path."""
    return file if file.startswith("::") else f"::/climates/{base_climate}/{file}"


class GeneratorEditor:
    """Import generator and normal generator (the game menu needs at least one normal generator per climate)."""

    def __init__(self, identity: ClimateIdentity) -> None:
        self._identity = identity

    def import_generator(self, document: DataDocument) -> DataDocument:
        root = document.root
        root["nodeTree"] = f"{self._identity.file_id}_import_gen.tree"
        root["climate"] = self._identity.clima_file
        root.table("desc")["name"] = _translated(f"{self._identity.label} (Import)")
        return document

    def random_generator(self, document: DataDocument) -> DataDocument:
        """Uses the game's random generator tree; irrelevant for heightmap import."""
        root = document.root
        root["nodeTree"] = _in_base_climate(self._identity.base_climate, str(root["nodeTree"]))
        root["climate"] = self._identity.clima_file
        root.table("desc")["name"] = _translated(f"{self._identity.label} (Zufall)")
        return document
