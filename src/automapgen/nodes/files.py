"""Sinks: write results into the run's output folder (side effects - they always run and are never cached).

The folder and the prefix of every file name (the project) are settings of the run (Engine output); the graph only
names the files. A sink also copies its file to a named destination of the run (`copy_to`, e.g. the game's
heightmaps folder) if the run has that destination.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.documents import JsonDocument
from automapgen.nodes.images import Image
from automapgen.nodes.towns import TextDocument


class FileParams(NodeParams):
    name: str
    """File name after the run's prefix, e.g. "_biomes.png" -> "GoldenTest_biomes.png"."""
    copy_to: str = ""
    """Named destination of the run that also receives the file, e.g. "heightmaps" (empty = none)."""


def copy_to_destination(path: Path, destination: str, context: NodeContext) -> None:
    """Copies a written file or folder to a named destination of the run (an older copy is replaced); nothing when
    no destination is named or the run has none of that name."""
    folder = context.copy_destination(destination) if destination else None
    if folder is None:
        return
    target = folder / path.name
    if path.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(path, target)
    else:
        shutil.copyfile(path, target)
    context.log(f"copied to {target}")


class PngParams(FileParams):
    skip_empty: bool = False
    """Write nothing when the image is all black (an optional layer the map does not use)."""


@node("file.png", side_effects=True)
def png(image: Image, params: PngParams, context: NodeContext) -> None:
    """Writes the image as PNG."""
    if params.skip_empty and not image.pixels.any():
        context.log(f"{params.name}: empty - not written")
        return
    path = context.output_file(params.name)
    path.write_bytes(image.png_bytes())
    context.log(f"saved {path}")
    copy_to_destination(path, params.copy_to, context)


@node("file.height_range", side_effects=True)
def height_range(black_m: float, land_min_m: float, white_m: float, params: FileParams, context: NodeContext) -> None:
    """Writes the heightmap's height references (black, lowest land, white) as JSON for later steps."""
    path = context.output_file(params.name)
    data = {"black_m": black_m, "min_height_m": land_min_m, "max_height_m": white_m}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    context.log(f"saved {path}")
    copy_to_destination(path, params.copy_to, context)


class JsonParams(FileParams):
    indent: int | None = None
    """Indentation (None = one line)."""
    compact: bool = False
    """No spaces after separators (smaller files)."""


@node("file.json", side_effects=True)
def json_file(document: JsonDocument, params: JsonParams, context: NodeContext) -> None:
    """Writes a JSON document (river plan, farm grid, path plan ...)."""
    path = context.output_file(params.name)
    separators = (",", ":") if params.compact else None
    path.write_text(json.dumps(document.data, ensure_ascii=False, indent=params.indent, separators=separators),
                    encoding="utf-8")
    context.log(f"saved {path}")
    copy_to_destination(path, params.copy_to, context)


@node("file.text", side_effects=True)
def text_file(document: TextDocument, params: FileParams, context: NodeContext) -> None:
    """Writes a text document (e.g. a Lua import file)."""
    path = context.output_file(params.name)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(document.text)
    context.log(f"saved {path}")
    copy_to_destination(path, params.copy_to, context)
