"""Material tree (ground textures): the preset's surfaces on top of the base climate's height rules."""

from __future__ import annotations

from collections.abc import Sequence

from automapgen.lua import NodeTree, Port
from automapgen.nodes.climate.adapters import ClimateAdapter
from automapgen.nodes.climate.areas import AreaBuilder
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.patches import PatchRule
from automapgen.nodes.climate.preset import (
    GameArea,
    GameMaterial,
    Landscape,
    Look,
    MaterialOutput,
    ModMaterial,
    PatchLayer,
    Surface,
)
from automapgen.nodes.climate.transition import TransitionMixer


class MaterialPaths:
    """materialType paths: game materials via "::/", own materials via "<mod ID>::/"."""

    def __init__(self, mod_id: str) -> None:
        self._mod_id = mod_id

    def of(self, material: GameMaterial | ModMaterial) -> str:
        if isinstance(material, GameMaterial):
            return f"::/terrain/materials/{material.name}/{material.name}.tmat"
        return f"{self._mod_id}::/terrain/materials/{material.folder}/{material.file}.tmat"


class MaterialTreeBuilder:
    """Adds the surfaces of the map's landscape (base materials and patch layers) to the material tree of one climate.

    On a transition map every surface of the transition is painted twice. Patches/soft mix: the primary look on the
    primary part, the secondary look on the secondary part. Overlay: the primary look complete, the secondary look on
    top of it (raised paint order) with the transition strength. Identical parts of both looks are painted once.
    """

    ORIGIN = (0.16, -0.14)

    def __init__(self, tree: NodeTree, adapter: ClimateAdapter, landscape: Landscape, paths: MaterialPaths) -> None:
        self._tree = tree
        self._adapter = adapter
        self._preset = landscape.primary
        self._paths = paths
        self._f = NodeFactory(tree, self.ORIGIN)
        seed = Port(self._f.data("clima data").node, "seed")
        self._patches = PatchRule(self._f, seed, adapter.slope)
        self._layers = {key: Port(self._f.node(f"{key} map", "input_map_data", params={"key": f"lc_{key}"}).node,
                                  "input")
                        for key in self._preset.slots.layers}
        self._mixer: TransitionMixer | None = None
        self._secondary: dict[str, Surface] = {}
        if landscape.transition is not None and landscape.secondary is not None:
            mask = self._layers[landscape.transition.layer.key]
            self._mixer = TransitionMixer(self._f, adapter, landscape.transition, mask, seed)
            self._secondary = dict(landscape.secondary.surfaces)

    def build(self) -> None:
        ground = self._preset.ground
        game_areas = self._adapter.extend_rock_and_scree(self._tree, self._f, self._layers[ground.rock_layer])
        self._areas = AreaBuilder(self._f, self._adapter, self._layers, game_areas)
        for key, surface in self._preset.surfaces.items():
            coverage = self._areas.port(surface.area, key.replace("_", " "))
            if self._mixer is not None and self._mixer.applies_to(key):
                self._paint_mixed(key, surface, self._secondary[key].look, coverage, self._mixer)
            else:
                self._paint_base(key, surface, surface.look.base, coverage, key)
                for layer_id, layer in surface.look.patches.items():
                    self._patch(coverage, layer, layer_id)
        self._adapter.connect_forest_floor(self._tree, self._layers[ground.forest_floor_layer])

    def _paint_base(self, key: str, surface: Surface, base: MaterialOutput | GameMaterial | None, coverage: Port,
                    output_id: str) -> None:
        if isinstance(base, MaterialOutput):
            self._output(output_id, coverage, base)
        elif isinstance(base, GameMaterial):
            self._adapter.set_game_material(self._tree, self._game_area(key, surface), self._paths.of(base))

    def _paint_mixed(self, key: str, surface: Surface, secondary: Look, coverage: Port,
                     mixer: TransitionMixer) -> None:
        f, name = self._f, key.replace("_", " ")
        parts = mixer.parts(key)
        first = coverage if parts.primary is None else f.multiply(f"{name} primary part", coverage, parts.primary)
        second = f.multiply(f"{name} secondary part", coverage, parts.secondary)
        primary_patches, secondary_patches = surface.look.patches, secondary.patches
        dropped = [layer.output.order for i, layer in primary_patches.items() if i not in secondary_patches]
        orders = SecondaryOrders(parts.on_top, dropped)
        self._paint_mixed_base(key, surface, secondary.base, coverage, first, second, orders)
        for layer_id in [*primary_patches, *(i for i in secondary_patches if i not in primary_patches)]:
            mine, theirs = primary_patches.get(layer_id), secondary_patches.get(layer_id)
            if mine == theirs and mine is not None:
                self._patch(coverage, mine, layer_id)
                continue
            if mine is not None:
                self._patch(first, mine, layer_id)
            if theirs is not None:
                order = orders.of(theirs.output.order, mine.output.order if mine else None)
                output = theirs.output.model_copy(update={"order": order})
                self._patch(second, theirs.model_copy(update={"output": output}), f"{layer_id}_secondary")

    def _paint_mixed_base(self, key: str, surface: Surface, secondary_base: MaterialOutput | GameMaterial | None,
                          coverage: Port, first: Port, second: Port, orders: SecondaryOrders) -> None:
        primary_base = surface.look.base
        if primary_base == secondary_base:
            self._paint_base(key, surface, primary_base, coverage, key)
        elif isinstance(primary_base, MaterialOutput) and isinstance(secondary_base, MaterialOutput):
            self._output(key, first, primary_base)
            order = orders.of(secondary_base.order, primary_base.order)
            self._output(f"{key}_secondary", second, secondary_base.model_copy(update={"order": order}))
        elif isinstance(secondary_base, GameMaterial) and (primary_base is None
                                                           or isinstance(primary_base, GameMaterial)):
            area = self._game_area(key, surface)       # primary None: the game's own look on its area
            if primary_base is not None:
                self._adapter.set_game_material(self._tree, area, self._paths.of(primary_base))
            game_order = self._adapter.game_order(self._tree, area)
            self._adapter.split_game_output(self._tree, self._f, area, None if orders.on_top else first, second,
                                            self._paths.of(secondary_base), orders.of(game_order, game_order))
        else:
            raise ValueError(f"surface {key}: the two presets use different kinds of base material")

    @staticmethod
    def _game_area(key: str, surface: Surface) -> str:
        if not isinstance(surface.area, GameArea):
            raise ValueError(f"surface {key}: a game material needs a game area")
        return surface.area.name

    def _patch(self, area: Port, layer: PatchLayer, output_id: str) -> None:
        if layer.patch.share > 0:
            coverage = self._patches.coverage(output_id.replace("_", " "), area, layer.patch)
            self._output(output_id, coverage, layer.output)

    def _output(self, output_id: str, coverage: Port, output: MaterialOutput) -> None:
        self._f.node(f"output {output_id}", "material_index_output", {"material": coverage},
                     {"dither": output.dither, "key": f"lc_{output_id}",
                      "materialType": self._paths.of(output.material), "order": output.order})


class SecondaryOrders:
    """Paint order of the secondary outputs of a surface. Overlay: just above the primary counterpart and above the
    primary patches the secondary look drops (the overlay covers them too), never below its own order. Otherwise
    (side by side) the own order."""

    def __init__(self, on_top: bool, dropped: Sequence[int]) -> None:
        self.on_top = on_top
        self._dropped = tuple(dropped)

    def of(self, own: int, counterpart: int | None) -> int:
        below = [order for order in (counterpart, *self._dropped) if order is not None]
        if not self.on_top or not below:
            return own
        return max(own, max(below) + 1)
