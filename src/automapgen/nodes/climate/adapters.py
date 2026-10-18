"""Knowledge about the original nodes of the game's base climates: the only place that knows their names.

The tree builders only talk to a `ClimateAdapter`; which one is used follows from the preset's `base_climate`. A game
update that renames nodes only affects the adapter; unknown names fail loudly through `NodeTree`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from automapgen.lua import LuaNumber, LuaTable, NodeTree, Port
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.heights import GameHeights
from automapgen.nodes.climate.preset import HeightLimits, SteepnessRange


@dataclass(frozen=True)
class OriginalFiles:
    """The base climate's files below the game's base/content."""

    material_tree: str
    import_tree: str
    clima: str
    import_generator: str
    random_generator: str
    icon: str


class ClimateAdapter(Protocol):
    """Original node names, ports and curve shapes of one base climate's material and import trees."""

    @property
    def base_climate(self) -> str: ...

    @property
    def originals(self) -> OriginalFiles: ...

    @property
    def slope(self) -> Port: ...

    @property
    def height(self) -> Port: ...

    @property
    def biome2(self) -> Port: ...

    @property
    def original_river_trees(self) -> Port: ...

    def apply_material_heights(self, material: NodeTree, game: GameHeights, limits: HeightLimits,
                               steep_rock: SteepnessRange, top: float) -> None: ...

    def apply_import_heights(self, imports: NodeTree, game: GameHeights, limits: HeightLimits, top: float) -> None: ...

    def game_area(self, name: str, factory: NodeFactory) -> Port: ...

    def biome_mask(self, biome: int) -> Port: ...

    def set_game_material(self, material: NodeTree, area: str, material_type: str) -> None: ...

    def game_order(self, material: NodeTree, area: str) -> int: ...

    def split_game_output(self, material: NodeTree, factory: NodeFactory, area: str, first: Port | None,
                          second: Port, second_material_type: str, second_order: int) -> None: ...

    def extend_rock_and_scree(self, material: NodeTree, factory: NodeFactory,
                              rock_layer: Port) -> dict[str, Port]: ...

    def neutralize_original_layer(self, imports: NodeTree, factory: NodeFactory, key: str) -> None: ...

    def connect_forest_floor(self, material: NodeTree, forest: Port) -> None: ...

    def connect_forest(self, imports: NodeTree, trees: Port, extra_sets: Sequence[tuple[int, str]]) -> None: ...

    def connect_rock_assets(self, imports: NodeTree, rocks: Port, sets: Sequence[str]) -> None: ...


class TemperateAdapter:
    """Original node names, ports and curve shapes of `temperate_clima.tree` and `temperate_import_gen.tree`."""

    base_climate = "temperate"
    originals = OriginalFiles(material_tree="climates/temperate/temperate_clima.tree.lua",
                              import_tree="climates/temperate/temperate_import_gen.tree.lua",
                              clima="climates/temperate/temperate.clima.lua",
                              import_generator="climates/temperate/temperate_import.gen.lua",
                              random_generator="climates/temperate/temperate.gen.lua",
                              icon="climates/temperate/temperate@2x.tga")

    # --- shared --------------------------------------------------------------------------------------------------
    slope = Port("gradient_map0")                               # steepness, "X^2 + Y^2"

    # --- material tree -------------------------------------------------------------------------------------------
    steep_rock_rule = "map_clamp_map5"                          # rock on steep slopes (original 0.7 / 0.75)
    steep_rock = Port(steep_rock_rule)
    height_rock = Port("New Compare map #85")                   # rock by height
    height_scree = Port("Compare map  #0")                      # scree by height
    biome_masks = (Port("biome_1_mask", "input"), Port("biome_2_mask", "input"))   # valley + mountain forest
    biome_mask_nodes: ClassVar[dict[int, str]] = {0: "biome_0_mask", 1: "biome_1_mask", 2: "biome_2_mask",
                                                  3: "biome_3mask", 4: "biome_4_mask"}
    game_outputs: ClassVar[dict[str, str]] = {"alpine_grass": "grass alpine", "alpine_ground": "grass_gravel",
                                              "rock": "rock", "scree": "scree_top"}
    alpine_grass_area = Port("New Multiply map #96")            # output "grass alpine" (biome 3/4)
    alpine_ground_area = Port("New Multiply map  #1")           # output "grass_gravel" (biome 4 x mountain mask)
    rock_output = "rock"
    scree_output = "scree_top"
    forest_ground_output = "forest_ground"

    # --- import tree ---------------------------------------------------------------------------------------------
    height = Port("Heightmap Input")
    biome2 = Port("Biome2 #1")
    original_river_trees = Port("add temperate river trees")
    assets_output = "assets_out"
    forest_list_anchor = "temperate_river"                      # own tree sets are listed after this set
    original_layer_consumers: ClassVar[dict[str, tuple[tuple[str, str], ...]]] = {
        "rivers": (("RIvers Mask", "in2"), ("New Remap Map #251", "in1"))}

    def apply_material_heights(self, material: NodeTree, game: GameHeights, limits: HeightLimits,
                               steep_rock: SteepnessRange, top: float) -> None:
        """Move the material tree's height rules (scree, rock, snow) to real elevations; steep rock."""
        g = game.of
        material.node("rock height profile      #0").set_param(       # scree, original 180/200
            "values", [(-10, 0), (g(limits.scree_start), 0), (g(limits.scree_full), 1), (top, 1)])
        material.node("New Constant point cloud #8").set_param(       # rock, original 330/350
            "values", [(-500, 0), (g(limits.rock_start), 0), (g(limits.rock_full), 1), (top, 1)])
        material.node("rock height profile     #0").set_param(        # snow in hollows, original 330/390
            "values", [(-10, 0), (g(limits.snow_hollow_start), 0), (g(limits.snow_hollow_full), 1), (top, 1)])
        material.node("Constant point cloud  #0").set_param(          # snow, original 380/430
            "values", [(-500, 0), (g(limits.snow_start), 0), (g(limits.snow_full), 1), (top, 1)])
        rule = material.node(self.steep_rock_rule)
        rule.set_param("from_x", steep_rock.start)
        rule.set_param("from_y", steep_rock.full)

    def apply_import_heights(self, imports: NodeTree, game: GameHeights, limits: HeightLimits, top: float) -> None:
        """Move the import tree's height rules (tree densities, tree line, mountain mask) to real elevations."""
        g = game.of
        dense, sparse, line = g(limits.trees_dense_until), g(limits.trees_sparse), g(limits.tree_line)
        imports.node("heightrange and density").set_param(            # original 220/250/310
            "values", [(0, 0), (1, 0), (2, 0.08), (dense, 0.06), (sparse, 0.03), (line, 0), (top, 0)])
        imports.node("heightrange and density #0").set_param(         # original 220/310
            "values", [(0, 0), (1, 0), (2, 0.08), (dense, 0.05), (dense, 0.03), (line, 0), (top, 0)])
        mountains = imports.node("New Mask Map #376")                 # original 100 / 1000
        mountains.set_param("interval_x", g(limits.mountains_min))
        mountains.set_param("interval_y", top)
        imports.node("New Constant Number #246").set_param("value", -1000)   # rivers may lie deeper

    def game_area(self, name: str, factory: NodeFactory) -> Port:
        """Areas the base climate computes: "lowland" (biome 1/2), "alpine_grass" (output "grass alpine", biome 3/4),
        "alpine_ground" (output "grass_gravel" = biome 4 x mountain mask)."""
        if name == "lowland":
            total = factory.add("lowland sum", *self.biome_masks)
            return factory.clamp("lowland", total, (0, 1), (0, 1))
        if name == "alpine_grass":
            return self.alpine_grass_area
        if name == "alpine_ground":
            return self.alpine_ground_area
        raise ValueError(f"unknown game area {name!r} for climate {self.base_climate}")

    def biome_mask(self, biome: int) -> Port:
        if biome not in self.biome_mask_nodes:
            raise ValueError(f"biome {biome} unknown (0..4)")
        return Port(self.biome_mask_nodes[biome], "input")

    def _game_output(self, area: str) -> str:
        if area not in self.game_outputs:
            raise ValueError(f"game area {area!r} has no own material output in {self.base_climate}")
        return self.game_outputs[area]

    def set_game_material(self, material: NodeTree, area: str, material_type: str) -> None:
        """Replace the material the base climate paints on one of its own areas."""
        material.node(self._game_output(area)).set_param("materialType", material_type)

    def game_order(self, material: NodeTree, area: str) -> int:
        """Paint order of the base climate's output on one of its own areas."""
        order = material.node(self._game_output(area)).param("order")
        if not isinstance(order, LuaNumber):
            raise ValueError(f"expected a number, got {order!r}")
        return int(order.value)

    def split_game_output(self, material: NodeTree, factory: NodeFactory, area: str, first: Port | None,
                          second: Port, second_material_type: str, second_order: int) -> None:
        """A copy of the game's own output paints `second` with another material and order. The original paints
        only `first` (None: it keeps its whole area)."""
        original = material.node(self._game_output(area))
        if first is not None:
            original.set_input("material", first)
        factory.node(f"output {area} secondary", "material_index_output", {"material": second},
                     {"dither": bool(original.param("dither")), "key": f"{original.param('key')}_secondary",
                      "materialType": second_material_type, "order": second_order})

    def extend_rock_and_scree(self, material: NodeTree, factory: NodeFactory, rock_layer: Port) -> dict[str, Port]:
        """The rock layer adds rock where steep and scree where flat to the game's height-based rock and scree.
        Returns the coverage of the game areas "rock" and "scree"."""
        rock = factory.maximum("rock", self.height_rock, factory.multiply("rock steep", rock_layer, self.steep_rock))
        flat = factory.invert("flat", self.steep_rock)
        scree = factory.maximum("scree", self.height_scree, factory.multiply("rock flat", rock_layer, flat))
        material.node(self.rock_output).set_input("material", rock)
        material.node(self.scree_output).set_input("material", scree)
        return {"rock": rock, "scree": scree}

    def neutralize_original_layer(self, imports: NodeTree, factory: NodeFactory, key: str) -> None:
        """The original nodes reading an original layer get an empty map (its slot is used by an own layer)."""
        if key not in self.original_layer_consumers:
            raise ValueError(f"original layer {key!r} cannot be replaced in {self.base_climate}")
        zero = factory.constant(f"no {key}", 0)
        for node, slot in self.original_layer_consumers[key]:
            imports.node(node).set_input(slot, zero)

    def connect_forest_floor(self, material: NodeTree, forest: Port) -> None:
        material.node(self.forest_ground_output).set_input("material", forest)

    def connect_forest(self, imports: NodeTree, trees: Port, extra_sets: Sequence[tuple[int, str]]) -> None:
        """Trees output; own tree sets are added to the forest set list (after the anchor set)."""
        output = imports.node(self.assets_output)
        output.set_input("forest", trees)
        if not extra_sets:
            return
        forest = output.table.table("params").table("forest")
        anchor = next(i for i, entry in enumerate(forest.array())
                      if isinstance(entry, LuaTable) and entry.array()[1:] == [self.forest_list_anchor])
        for offset, (index, name) in enumerate(extra_sets, start=1):
            forest.insert_array(anchor + offset, LuaTable([(None, LuaNumber.of(index)), (None, name)]))

    def connect_rock_assets(self, imports: NodeTree, rocks: Port, sets: Sequence[str]) -> None:
        """Rock asset output (index 1..n) and its set list (replaces the original single "granite" set)."""
        output = imports.node(self.assets_output)
        output.set_input("assets", rocks)
        params = output.table.table("params")
        params["assets"] = LuaTable([(None, LuaTable([(None, LuaNumber.of(i)), (None, name)]))
                                     for i, name in enumerate(sets, start=1)])


ADAPTERS: dict[str, type[ClimateAdapter]] = {"temperate": TemperateAdapter}
"""Base climate -> adapter (new base climates register here)."""


def adapter_for(base_climate: str) -> ClimateAdapter:
    if base_climate not in ADAPTERS:
        raise ValueError(f"base climate {base_climate!r} unknown (known: {sorted(ADAPTERS)})")
    return ADAPTERS[base_climate]()
