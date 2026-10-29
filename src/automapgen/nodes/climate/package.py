"""The climate mod around the trees: names, climate definition, generators, pictures, files and texts.

One climate mod per landscape, reference height and map scale; its name says both, e.g.
"DHE Alpine Realistic 540m Scale 0.5 BETA" (ID "DHE-Alpine-Realistic-540m-Scale-0_5-BETA").
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from PIL import Image as PilImage
from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeParams, data_type, node, param
from automapgen.nodes.climate.definition import ClimaDefinitionEditor, ClimateIdentity, GeneratorEditor
from automapgen.nodes.climate.heights import GameHeights
from automapgen.nodes.climate.preset import ClimatePreset, EconomySettings, Landscape, Transition
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.gamefiles import LuaData, NodeTreeData
from automapgen.nodes.images import Image
from automapgen.nodes.maps import MapFormat
from automapgen.nodes.mods import (
    CONTENT,
    ImageStamper,
    ModFiles,
    ModInfo,
    PublishingSettings,
    Translations,
    tga_bytes,
)


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class BilingualText(_Value):
    de: str
    """German."""
    en: str
    """English."""


class EconomyTexts(_Value):
    standard: BilingualText
    """Paragraph for the game's economy."""
    custom: BilingualText
    """Paragraph for the own economy."""


class ClimateTexts(_Value):
    """Texts and pictures of a published climate mod (a value of configs/mod_texts/<texts>.toml)."""

    summary: str
    """Short description in the mod hub (placeholders {factor}, {ref})."""
    tags: tuple[str, ...]
    """Tags in the mod hub."""
    description: BilingualText
    """Mod description; placeholders {label} {ref} {factor} {factor_de} {conifer_start} {conifer_full} {tree_line}
    {snow_start} {snow_full} {economy_de} {economy_en} {name} (landscape) {prefix} {beta}."""
    economy: EconomyTexts
    """Paragraph about the economy ({economy_de}, {economy_en} in the description)."""
    climate_description: BilingualText
    """In-game climate description (placeholders {factor}, {ref})."""
    strings_de: dict[str, str] = {}
    """German translations of further in-game texts."""
    picture: str
    """Picture of the mod thumbnail and the climate tile (relative to the working directory)."""
    icon_size: tuple[int, int] = (700, 400)
    """Size of the climate tile when creating a map (px)."""


@data_type("climate_identity", "Names and IDs of one climate mod: mod ID, label, in-game description, base climate.",
           codec=JsonDocumentCodec(lambda data: ClimateIdentityData(data)), preview=lambda i: i.data["label"])
@dataclass(frozen=True)
class ClimateIdentityData(JsonDocument):
    """{mod_id, label, description, base_climate}."""

    def identity(self) -> ClimateIdentity:
        return ClimateIdentity(**self.data)


@dataclass(frozen=True)
class ClimateNaming:
    """E.g. label "DHE Alpine Realistic 540m Scale 0.4 BETA", ID "DHE-Alpine-Realistic-540m-Scale-0_4-BETA"; with
    real heights (`height_scale` 1 at scale 1:2) "DHE Alpine Realistic 540m Scale 0.5 Heights 1 BETA".

    The ID is used as folder, mod ID and climate ID: no spaces, ':' or '/', and '.' becomes '_' (a '.' next to
    '.clima' in file names confuses the game).
    """

    prefix: str
    name: str
    beta_tag: str
    reference_m: float
    scale: float
    height_scale: float
    """Real metres per game metre of height (`MapFormat.height_scale`)."""

    @property
    def factor(self) -> str:
        """Scale as decimal game metres per real metre: 1:2.5 -> "0.4", 1:1.5 -> "0.667"."""
        return f"{1 / self.scale:.3g}"

    @property
    def height_factor(self) -> str:
        """Height scale as decimal game metres per real metre, like `factor`."""
        return f"{1 / self.height_scale:.3g}"

    @property
    def _heights_differ(self) -> bool:
        return self.height_factor != self.factor

    @property
    def ref(self) -> int:
        return round(self.reference_m)

    @property
    def label(self) -> str:
        heights = f" Heights {self.height_factor}" if self._heights_differ else ""
        return f"{self.prefix} {self.name} {self.ref}m Scale {self.factor}{heights} {self.beta_tag}".strip()

    @property
    def mod_id(self) -> str:
        return self.label.replace(" ", "-").replace("/", "-").replace(":", "-").replace(".", "_")

    @property
    def stamp(self) -> str:
        """Text printed on the mod's pictures, e.g. "540m" / "0.4" (third line: the height factor if it differs)."""
        heights = f"\n{self.height_factor}" if self._heights_differ else ""
        return f"{self.ref}m\n{self.factor}{heights}"


class LandscapeParams(NodeParams):
    climate: ClimatePreset
    """The climate preset (the map's landscape type)."""
    transition: Transition | None = None
    """A second look on part of the map (absent: one look)."""

    def landscape(self) -> Landscape:
        return Landscape(self.climate, self.transition)


class IdentityParams(LandscapeParams):
    publishing: PublishingSettings
    """Prefix and tag of the mod names."""
    climate_texts: ClimateTexts
    """Texts of the climate mod."""


class Identity(NamedTuple):
    identity: ClimateIdentityData
    mod_id: str
    stamp: str


def _naming(reference_m: float, map_format: MapFormat, params: IdentityParams) -> ClimateNaming:
    return ClimateNaming(params.publishing.prefix, params.landscape().name, params.publishing.beta_tag, reference_m,
                         map_format.scale, height_scale=map_format.height_scale)


@node("climate.identity")
def identity(reference_m: float, map_format: MapFormat, params: IdentityParams) -> Identity:
    """Names and IDs of the climate mod for this reference height and scale."""
    naming = _naming(reference_m, map_format, params)
    description = params.climate_texts.climate_description.en.format(factor=naming.factor, ref=naming.ref)
    data = {"mod_id": naming.mod_id, "label": naming.label, "description": description,
            "base_climate": params.climate.base_climate}
    return Identity(ClimateIdentityData(data), naming.mod_id, naming.stamp)


class ReferenceParams(NodeParams):
    step_m: float = param(100.0, ge=0, unit="m")
    """Round the map's lowest land to this step (0 = exact), so maps with a similar lowest land share one mod."""


@node("climate.reference", output="reference_m")
def reference(land_min_m: float, params: ReferenceParams) -> float:
    """Reference height of the climate mod: the map's lowest land rounded to the nearest step (550 and 560 -> 600).
    The heightmap keeps the exact lowest land; the mod's height rules shift by the difference (at most half a step
    of real elevation; rounded up, they appear lower on the map)."""
    if params.step_m == 0:
        return land_min_m
    return math.floor(land_min_m / params.step_m + 0.5) * params.step_m


@node("climate.definition", output="clima")
def definition(clima: LuaData, identity: ClimateIdentityData, reference_m: float, map_format: MapFormat,
               economy_path: str, params: LandscapeParams) -> LuaData:
    """The climate definition: names, own trees, import layers on their slots, map preview levels, the own economy
    (empty path: the game's economy)."""
    preset = params.landscape().primary
    game = GameHeights(reference_m, map_format.height_game_per_real)
    levels = (game.of(preset.map_preview_level), game.of(preset.heights.snow_start))
    document = ClimaDefinitionEditor(clima.document(), identity.identity()).apply(
        preset.slots.layers, economy_path or None, levels, original_layers=preset.slots.original)
    return LuaData.of(document)


class Generators(NamedTuple):
    import_generator: LuaData
    random_generator: LuaData


@node("climate.generators")
def generators(import_generator: LuaData, random_generator: LuaData, identity: ClimateIdentityData) -> Generators:
    """The import generator (own import tree) and a random generator (the game needs one per climate)."""
    editor = GeneratorEditor(identity.identity())
    return Generators(LuaData.of(editor.import_generator(import_generator.document())),
                      LuaData.of(editor.random_generator(random_generator.document())))


class PicturesParams(NodeParams):
    publishing: PublishingSettings
    """Thumbnail size and the text on the pictures."""
    climate_texts: ClimateTexts
    """The picture and the climate tile's size."""


class Pictures(NamedTuple):
    icon: Image
    thumbnail: Image


@node("climate.pictures")
def pictures(stamp: str, params: PicturesParams) -> Pictures:
    """The climate tile in "New map" and the mod thumbnail: the picture with reference height and scale on it."""
    stamper = ImageStamper(params.publishing.stamp)
    with PilImage.open(params.climate_texts.picture) as source:
        icon = stamper.stamp(stamper.fit(source, params.climate_texts.icon_size), stamp)
        thumbnail = stamper.stamp(stamper.fit(source, params.publishing.thumbnail.size), stamp)
    return Pictures(Image(np.asarray(icon)), Image(np.asarray(thumbnail)))


@node("climate.files", output="files")
def files(identity: ClimateIdentityData, material_tree: NodeTreeData, import_tree: NodeTreeData, clima: LuaData,
          import_generator: LuaData, random_generator: LuaData, icon: Image) -> ModFiles:
    """The climate's files in the mod (content/climates/<id>/)."""
    i = identity.identity()
    folder = f"{CONTENT}{i.folder}/"
    return ModFiles.of({
        f"{folder}{i.clima_file}.lua": clima.text.encode("utf-8"),
        f"{folder}{i.file_id}_clima.tree.lua": material_tree.text.encode("utf-8"),
        f"{folder}{i.file_id}_import_gen.tree.lua": import_tree.text.encode("utf-8"),
        f"{folder}{i.file_id}_import.gen.lua": import_generator.text.encode("utf-8"),
        f"{folder}{i.file_id}.gen.lua": random_generator.text.encode("utf-8"),
        f"{folder}{i.icon_file}@2x.tga": tga_bytes(icon, run_length=True),
    })


class ModTextParams(IdentityParams):
    economy: EconomySettings = EconomySettings()
    """Economy of the climate (the description names it)."""


class ModTexts(NamedTuple):
    info: ModInfo
    translations: Translations


@node("climate.mod_texts")
def mod_texts(reference_m: float, map_format: MapFormat, params: ModTextParams) -> ModTexts:
    """What the mod hub shows (name, summary, description with the climate's heights) and the German texts."""
    naming = _naming(reference_m, map_format, params)
    texts, preset, pub = params.climate_texts, params.climate, params.publishing
    economy = texts.economy.custom if params.economy.custom else texts.economy.standard
    values: Mapping[str, object] = {
        "prefix": pub.prefix, "beta": pub.beta_tag, "label": naming.label, "ref": naming.ref, "factor": naming.factor,
        "factor_de": naming.factor.replace(".", ","), "conifer_start": _meters(preset.forest.conifer_start),
        "conifer_full": _meters(preset.forest.conifer_full), "tree_line": _meters(preset.heights.tree_line),
        "snow_start": _meters(preset.heights.snow_start), "snow_full": _meters(preset.heights.snow_full),
        "economy_de": economy.de, "economy_en": economy.en, "name": naming.name, "climate_name": naming.name}
    description = texts.description.de.format(**values) + "\n\n----------\n\n" + texts.description.en.format(**values)
    info = ModInfo({"name": naming.label, "summary": texts.summary.format(factor=naming.factor, ref=naming.ref),
                    "description": description, "tags": list(texts.tags)})
    climate_en = texts.climate_description.en.format(factor=naming.factor, ref=naming.ref)
    climate_de = texts.climate_description.de.format(factor=naming.factor.replace(".", ","), ref=naming.ref)
    return ModTexts(info, Translations({**texts.strings_de, climate_en: climate_de}))


def _meters(value: float) -> int | float:
    """Elevation for texts: whole metres without decimals."""
    return int(value) if value.is_integer() else value
