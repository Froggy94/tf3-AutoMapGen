"""Mods of the game: their files as a value, the package around the content (mod.json, content index, mod hub
information, translations, thumbnail) and the sink that writes a mod folder.

A mod folder: `content/` (what the game loads), `mod.json`, `_content.json` (index of `content/`), `strings.json`
(translations), `_metadata/modinfo.json` (mod hub), `image_00.tga` (thumbnail in the local mod list),
`_metadata/0.png` (cover in the mod hub).
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PilImage
from PIL import ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeContext, NodeParams, data_type, node
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.files import copy_to_destination
from automapgen.nodes.images import Image

CONTENT = "content/"


class _ZipCodec:
    suffix = ".zip"

    def save(self, value: ModFiles, path: Path) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            for name, data in value.files:
                z.writestr(name, data)

    def load(self, path: Path) -> ModFiles:
        with zipfile.ZipFile(path) as z:
            return ModFiles(tuple((name, z.read(name)) for name in z.namelist()))


@data_type("mod_files", "Files of a mod or part of one: path below the mod folder -> content.", codec=_ZipCodec(),
           preview=lambda m: "\n".join(name for name, _ in m.files))
@dataclass(frozen=True)
class ModFiles:
    """Sorted by path; a path occurs once."""

    files: tuple[tuple[str, bytes], ...]

    def __post_init__(self) -> None:
        paths = [name for name, _ in self.files]
        twice = sorted({p for p in paths if paths.count(p) > 1})
        if twice:
            raise ValueError(f"mod files given twice: {twice}")
        object.__setattr__(self, "files", tuple(sorted(self.files)))

    @classmethod
    def of(cls, files: Mapping[str, bytes]) -> ModFiles:
        return cls(tuple(files.items()))

    @classmethod
    def merged(cls, parts: Iterable[ModFiles]) -> ModFiles:
        return cls(tuple(entry for part in parts for entry in part.files))

    def paths(self) -> list[str]:
        return [name for name, _ in self.files]


def json_bytes(data: object, ascii_only: bool = True) -> bytes:
    """JSON in the layout of the game's mod files (4 spaces)."""
    return json.dumps(data, indent=4, ensure_ascii=ascii_only).encode("utf-8")


def tga_bytes(image: Image, run_length: bool) -> bytes:
    """TGA, the game's format for mod images (run-length encoded or not)."""
    buffer = io.BytesIO()
    PilImage.fromarray(image.pixels).save(buffer, format="TGA", compression="tga_rle" if run_length else None)
    return buffer.getvalue()


# --- mod hub information ----------------------------------------------------------------------------------------------


@data_type("mod_info", "What the mod hub shows of a mod: author, name, summary, description, tags.",
           codec=JsonDocumentCodec(lambda data: ModInfo(data)), preview=lambda i: json.dumps(i.data)[:2000])
@dataclass(frozen=True)
class ModInfo(JsonDocument):
    """{author, name, summary, description, tags}."""


@data_type("translations", "In-game texts of a mod: English text -> German.",
           codec=JsonDocumentCodec(lambda data: Translations(data)), preview=lambda t: json.dumps(t.data)[:2000])
@dataclass(frozen=True)
class Translations(JsonDocument):
    """{English: German}."""


# --- images -----------------------------------------------------------------------------------------------------------


class _Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class StampSettings(_Settings):
    font: str = "segoeuib.ttf"
    """Font of the text printed on mod images (file name or path; missing = Pillow's default font)."""
    line_height: float = 0.2
    """Font height per line as share of the image height."""


class ThumbnailFiles(_Settings):
    size: tuple[int, int] = (1400, 800)
    """Size of the mod thumbnail (px)."""
    file: str = "image_00.tga"
    """Thumbnail in the mod folder (the game shows it in the local mod list)."""
    cover_file: str = "_metadata/0.png"
    """Cover in the mod folder (mod hub; where the game puts it itself)."""


class PublishingSettings(_Settings):
    """Names, author and images of the published mods (a value of configs/publishing.toml)."""

    prefix: str
    """Prefix of all mod names and IDs."""
    beta_tag: str = ""
    """Tag at the end of the climate mods' names and IDs (empty = none)."""
    author: str
    """Author in the mod hub."""
    thumbnail: ThumbnailFiles = ThumbnailFiles()
    """Mod thumbnail."""
    stamp: StampSettings = StampSettings()
    """Text on the images."""


class ImageStamper:
    """Crops pictures to an aspect ratio and prints centred multi-line text (white with dark outline)."""

    def __init__(self, settings: StampSettings) -> None:
        self._settings = settings

    @staticmethod
    def fit(source: PilImage.Image, size: tuple[int, int]) -> PilImage.Image:
        """Crop centred to the aspect ratio of `size`, then scale to `size`."""
        image = source.convert("RGB")
        target_w, target_h = size
        w, h = image.size
        if w / h > target_w / target_h:
            new_w = round(h * target_w / target_h)
            image = image.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
        else:
            new_h = round(w * target_h / target_w)
            image = image.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
        return image.resize(size, PilImage.Resampling.LANCZOS)

    def stamp(self, image: PilImage.Image, text: str) -> PilImage.Image:
        if not text:
            return image
        image = image.copy()
        size = round(image.height * self._settings.line_height)
        try:
            font: ImageFont.FreeTypeFont | ImageFont.ImageFont = ImageFont.truetype(self._settings.font, size)
        except OSError:
            font = ImageFont.load_default(size=size)
        stroke = max(2, size // 12)
        ImageDraw.Draw(image).multiline_text(
            (image.width / 2, image.height / 2), text, font=font, anchor="mm", align="center",
            fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(20, 25, 30), spacing=size // 6)
        return image


# --- package ----------------------------------------------------------------------------------------------------------


class PackageParams(NodeParams):
    publishing: PublishingSettings
    """Author and image files of the published mods."""
    severity_remove: str = "Critical"
    """What removing the mod means for savegames (the game's severity)."""


@node("mod.package", output="mod")
def package(content: tuple[ModFiles, ...], mod_id: str, info: ModInfo, params: PackageParams,
            translations: Translations | None = None, thumbnail: Image | None = None,
            post_run_script: str | None = None) -> ModFiles:
    """The mod folder: its content (paths below `content/`) and the descriptive files around it (strings.json and
    the thumbnail only when given); `post_run_script`: run by the game after loading the mods
    ("<mod id>::/<file>@<function>")."""
    files = ModFiles.merged(content)
    outside = [p for p in files.paths() if not p.startswith(CONTENT)]
    if outside:
        raise ValueError(f"mod content outside {CONTENT}: {outside}")
    index = sorted(p.removeprefix(CONTENT) for p in files.paths())
    described = {
        "_content.json": json_bytes({"archives": None, "files": index}),
        "mod.json": json_bytes({
            "dependencies": None, "incompatibilities": None, "modId": mod_id, "options": None, "params": None,
            "postRunScript": {"fileName": post_run_script or ""}, "preRunScript": {"fileName": ""}, "revision": 1,
            "runScript": {"fileName": ""}, "severityAdd": "None", "severityRemove": params.severity_remove}),
        "_metadata/modinfo.json": json_bytes(_mod_info(info.data, params.publishing.author), ascii_only=False),
    }
    if translations is not None:
        described["strings.json"] = json_bytes({"de": translations.data}, ascii_only=False)
    if thumbnail is not None:
        cover = io.BytesIO()
        PilImage.fromarray(thumbnail.pixels).save(cover, format="PNG")
        described[params.publishing.thumbnail.file] = tga_bytes(thumbnail, run_length=False)
        described[params.publishing.thumbnail.cover_file] = cover.getvalue()
    return ModFiles.merged([files, ModFiles.of(described)])


def _mod_info(info: Mapping[str, Any], author: str) -> dict[str, Any]:
    return {"authors": [{"name": author, "role": "CREATOR"}], "description": info["description"],
            "name": info["name"], "summary": info["summary"], "tags": list(info["tags"]), "url": ""}


class ModFolderParams(NodeParams):
    folder: str = "mods"
    """Folder below the run's output folder that takes the mod folders."""
    copy_to: str = ""
    """Named destination of the run that also receives the mod folder, e.g. "staging_area" (empty = none)."""


@node("file.mod", side_effects=True)
def mod_folder(mod: ModFiles, name: str, params: ModFolderParams, context: NodeContext) -> None:
    """Writes a mod folder `<output>/<folder>/<name>` (an older one of the same name is replaced); the name is the
    mod ID or the mod ID without its revision."""
    target = context.output_path(f"{params.folder}/{name}")
    if target.exists():
        shutil.rmtree(target)
    for name, data in mod.files:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    context.log(f"mod written: {target}")
    copy_to_destination(target, params.copy_to, context)
