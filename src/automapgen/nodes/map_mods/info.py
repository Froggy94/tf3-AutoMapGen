"""Names and mod hub texts of the map's own mods: folder <prefix>_<map>_<kind> (e.g. DHE_brenner_roads), mod ID
<folder>_1, name "<prefix> <title> <map>"."""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeParams, node
from automapgen.nodes.mods import ModInfo, PublishingSettings


class MapModText(BaseModel):
    """Title and mod hub texts of one kind of map mod (a value of configs/map_mods.toml)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    title: str
    """Middle part of the mod name, e.g. "Roads"."""
    summary: str
    """Short text in the mod hub."""
    description: str
    """Description in the mod hub ({map} = the map's name)."""
    tags: tuple[str, ...] = ()
    """Tags in the mod hub."""


class MapModInfoParams(NodeParams):
    kind: str
    """Kind of map mod: key in map_mod_texts and last part of the folder name."""
    map_name: str
    """Name of the map in the names of its mods."""
    publishing: PublishingSettings
    """Prefix and author of the published mods."""
    map_mod_texts: dict[str, MapModText]
    """Titles and texts per kind of map mod."""


class MapModInfo(NamedTuple):
    folder: str
    mod_id: str
    info: ModInfo


@node("mod.map_info")
def map_info(params: MapModInfoParams) -> MapModInfo:
    """Folder, mod ID and mod hub texts of a mod of this map."""
    texts = params.map_mod_texts.get(params.kind)
    if texts is None:
        raise ValueError(f"no texts for the map mod '{params.kind}' (map_mod_texts: {sorted(params.map_mod_texts)})")
    prefix = params.publishing.prefix
    folder = f"{prefix}_{params.map_name.lower()}_{params.kind}"
    return MapModInfo(folder, f"{folder}_1", ModInfo({
        "name": f"{prefix} {texts.title} {params.map_name}", "summary": texts.summary,
        "description": texts.description.replace("{map}", params.map_name), "tags": list(texts.tags)}))
