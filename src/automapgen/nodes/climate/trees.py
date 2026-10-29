"""Nodes of the climate's generator trees: the base climate's original files, and the material and import trees of a
climate made from them for one reference height and map scale."""

from __future__ import annotations

from typing import NamedTuple

from automapgen.graph import NodeContext, NodeParams, node
from automapgen.nodes.climate.adapters import adapter_for
from automapgen.nodes.climate.heights import GameHeights
from automapgen.nodes.climate.import_tree import ImportTreeBuilder
from automapgen.nodes.climate.material_tree import MaterialPaths, MaterialTreeBuilder
from automapgen.nodes.climate.preset import ClimatePreset, Landscape, Transition
from automapgen.nodes.gamefiles import GameFiles, LuaData, NodeTreeData
from automapgen.nodes.maps import MapFormat


class OriginalsParams(NodeParams):
    climate: ClimatePreset
    """The climate preset (the map's landscape type)."""


class ClimateParams(OriginalsParams):
    transition: Transition | None = None
    """A second look on part of the map (absent: one look)."""

    def landscape(self) -> Landscape:
        return Landscape(self.climate, self.transition)


class ClimateOriginals(NamedTuple):
    material_tree: NodeTreeData
    import_tree: NodeTreeData
    clima: LuaData
    import_generator: LuaData
    random_generator: LuaData


@node("climate.originals")
def originals(params: OriginalsParams, context: NodeContext) -> ClimateOriginals:
    """The base climate's original trees, climate definition and generators (game files)."""
    files = GameFiles.at(context.game_folder())
    paths = adapter_for(params.climate.base_climate).originals
    return ClimateOriginals(NodeTreeData(files.text(paths.material_tree)), NodeTreeData(files.text(paths.import_tree)),
                            LuaData(files.text(paths.clima)), LuaData(files.text(paths.import_generator)),
                            LuaData(files.text(paths.random_generator)))


@node("climate.material_tree", output="tree")
def material_tree(original: NodeTreeData, reference_m: float, map_format: MapFormat, mod_id: str,
                  params: ClimateParams, context: NodeContext) -> NodeTreeData:
    """Ground textures: the base climate's height rules moved to real elevations, the preset's surfaces on top (own
    materials addressed through the mod ID)."""
    landscape = params.landscape()
    adapter = adapter_for(landscape.primary.base_climate)
    tree = original.tree()
    preset = landscape.primary
    adapter.apply_material_heights(tree, GameHeights(reference_m, map_format.height_game_per_real), preset.heights,
                                   preset.ground.steep_rock, preset.top)
    MaterialTreeBuilder(tree, adapter, landscape, MaterialPaths(mod_id)).build()
    return NodeTreeData.of(tree.document)


@node("climate.import_tree", output="tree")
def import_tree(original: NodeTreeData, reference_m: float, map_format: MapFormat, params: ClimateParams,
                context: NodeContext) -> NodeTreeData:
    """Import: the landcover layers stored for the material tree, trees and rocks; height rules at real elevations."""
    landscape = params.landscape()
    adapter = adapter_for(landscape.primary.base_climate)
    game = GameHeights(reference_m, map_format.height_game_per_real)
    tree = original.tree()
    adapter.apply_import_heights(tree, game, landscape.primary.heights, landscape.primary.top)
    ImportTreeBuilder(tree, adapter, landscape, game, map_format.game_per_real).build()
    return NodeTreeData.of(tree.document)
