"""Import tree (runs once on map import): stores the landcover layers for the material tree, places trees and rocks."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from automapgen.lua import NodeTree, Port
from automapgen.nodes.climate.adapters import ClimateAdapter
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.heights import GameHeights
from automapgen.nodes.climate.patches import PatchRule
from automapgen.nodes.climate.preset import (
    ClimatePreset,
    ConstantIntensity,
    Landscape,
    NoiseSpec,
    PatchSpec,
    SteepWaterArea,
)
from automapgen.nodes.climate.transition import ZoneRamp

_CONIFER_MIX_NOISE = NoiseSpec(frequency=0.01, lacunarity=3, octaves=3, gain=0.5)
_NOISE_CLAMP = ((-0.5, 0.5), (0.01, 0.99))        # keep noise inside (0, 1) so shares 0 and 1 are exact


class LandcoverLayers:
    """Reads every import layer, stores it for the material tree, and provides binary masks.
    `derived`: layers stored as a map computed from the raw layer (e.g. the transition ramp)."""

    def __init__(self, factory: NodeFactory, preset: ClimatePreset,
                 derived: Mapping[str, Callable[[Port], Port]] | None = None) -> None:
        self._f = factory
        self._top = preset.top
        self._inputs: dict[str, Port] = {}
        derived = derived or {}
        for key, layer in preset.slots.layers.items():
            source = factory.node(f"{key} in", "input_biomes", params={"layerKey": key, "type": "Layer"})
            self._inputs[key] = Port(source.node, "input")
            stored = derived[key](self._inputs[key]) if key in derived else self._inputs[key]
            factory.node(f"{key} out", "output_map_data", {"output": stored},
                         {"key": f"lc_{key}", "persistent": True, "resolutionx": layer.resolution,
                          "resolutiony": layer.resolution})

    def raw(self, key: str) -> Port:
        return self._inputs[key]

    def binary(self, name: str, key: str) -> Port:
        return self._f.mask(name, self._inputs[key], 0.5, self._top)


class ImportTreeBuilder:
    """Adds trees and rock assets to the import tree of one climate."""

    ORIGIN = (-0.33, -0.18)

    def __init__(self, tree: NodeTree, adapter: ClimateAdapter, landscape: Landscape, game: GameHeights,
                 distance_game_per_real: float) -> None:
        self._tree = tree
        self._adapter = adapter
        self._landscape = landscape
        self._preset = landscape.primary
        self._game = game
        self._distance_game_per_real = distance_game_per_real
        self._f = NodeFactory(tree, self.ORIGIN)
        self._seed = Port(self._f.data("data").node, "seed")
        self._layers = LandcoverLayers(self._f, self._preset, self._derived_layers())
        self._patches = PatchRule(self._f, self._seed, adapter.slope)

    def _derived_layers(self) -> dict[str, Callable[[Port], Port]]:
        """The transition mask marks only the area (the game reads layers as yes/no); stored is the rising ramp."""
        transition = self._landscape.transition
        if transition is None:
            return {}
        ramp = ZoneRamp(self._f, self._preset.top)
        length = transition.zone.real_length_m * self._distance_game_per_real
        return {transition.layer.key: lambda layer: ramp.build("transition ramp", layer, length)}

    def build(self) -> None:
        f, p = self._f, self._preset
        self._adapter.neutralize_unused_original_layers(self._tree, f, p.slots.original)
        forest = self._layers.binary("forest bin", "forest")
        water = self._layers.binary("water bin", "water")
        shore = self._layers.binary("shore bin", "shore")
        above_water = f.mask("above water", self._adapter.height, p.no_cover_below, p.top)
        dry = f.multiply("dry", f.invert("no water", water), above_water)
        dither = f.dither("dither", self._seed)
        not_steep = f.clamp("not steep", self._adapter.slope,
                            (p.forest.max_steepness, p.forest.max_steepness + 0.05), (1, 0))
        trees = ForestBuilder(f, self._adapter, p, self._game, self._seed).build(
            forest, water, shore, dry, dither, not_steep, self._layers.raw("riverbed"))
        classic = p.forest.classic
        extra_sets = [(index, name) for name, (index, _) in classic.sets.items()] if classic else []
        self._adapter.connect_forest(self._tree, trees, extra_sets)

        rocks = p.rocks
        if rocks.mountain > 0 or rocks.forest > 0 or rocks.shore > 0:
            builder = RockAssetBuilder(f, self._adapter, p, self._game, self._layers, self._patches)
            assets = builder.build(forest, water, shore, dry, dither, not_steep, above_water)
            self._adapter.connect_rock_assets(self._tree, assets, [name for name, _ in rocks.sets])


class ForestBuilder:
    """Tree index per cell: forest mask x density, species by biome and height, none on steep/wet/high ground."""

    def __init__(self, factory: NodeFactory, adapter: ClimateAdapter, preset: ClimatePreset, game: GameHeights,
                 seed: Port) -> None:
        self._f = factory
        self._adapter = adapter
        self._forest = preset.forest
        self._preset = preset
        self._game = game
        self._seed = seed

    def build(self, forest: Port, water: Port, shore: Port, dry: Port, dither: Port, not_steep: Port,
              riverbed: Port) -> Port:
        f, p, top = self._f, self._forest, self._preset.top
        density = f.greater("density", f.constant("density value", p.density), dither)
        cells = f.multiply("forest cells", forest, density)
        conifer_index, broadleaf_index = self._species()
        trees_raw = f.multiply("trees raw", cells, f.select("idx", self._conifer(), conifer_index, broadleaf_index,
                                                            0.5))
        below_line = f.mask("below tree line", self._adapter.height, -top,
                            self._game.of(self._preset.heights.tree_line))
        allowed_slope = f.multiply("tree allowed slope", not_steep, below_line)
        no_riverbed = f.mask("tree no riverbed", riverbed, -top, 0.1)   # layer is read coarsely: any share counts
        tree_dry = f.multiply("tree dry", dry, no_riverbed)
        allowed = f.multiply("tree allowed terrain", allowed_slope, tree_dry)
        trees = f.select("trees", allowed, trees_raw, f.constant("zero", 0), 0.5)
        if p.single_trees_outside <= 0:
            return trees
        return self._keep_single_trees(trees, forest, water, shore, dither, tree_dry)

    def _species(self) -> tuple[Port, Port]:
        f, p = self._f, self._forest
        lowland = f.constant("idx lowland", p.index_lowland)
        hills = f.constant("idx hills", p.index_hills)
        conifer: Port = f.constant("idx conifer", p.index_conifer)
        broadleaf = f.select("idx 12", self._adapter.biome2, hills, lowland, 0.5)
        if p.classic is None:
            return conifer, broadleaf
        c = p.classic
        noise = f.clamp("classic noise", f.noise("classic noise raw", self._seed,
                                                 NoiseSpec(frequency=c.noise_frequency, lacunarity=3, octaves=3,
                                                           gain=0.5)), *_NOISE_CLAMP)
        pine = f.greater("classic pine sel", f.constant("classic pine share", c.pine_share), noise)
        low = f.mask("classic pine low", self._adapter.height, -self._preset.top, self._game.of(c.pine_max))
        conifer = f.select("idx conifer mix", f.multiply("classic pine use", pine, low),
                           f.constant("classic pine idx", c.sets[c.pine_set][0]), conifer, 0.5)
        broad = f.greater("classic broad sel", noise, f.constant("classic broad min", 1 - c.broadleaf_share))
        broadleaf = f.select("idx 12 mix", broad, f.constant("classic broad idx", c.sets[c.broadleaf_set][0]),
                             broadleaf, 0.5)
        return conifer, broadleaf

    def _conifer(self) -> Port:
        """Conifer where the height-dependent share beats a coarse noise (stands instead of single trees)."""
        f, p, top, g = self._f, self._forest, self._preset.top, self._game.of
        steps = f.point_cloud("conifer steps", [(-top, p.conifer_share_below), (g(p.conifer_start),
                                                p.conifer_share_below), (g(p.conifer_full), 1), (top, 1)])
        share = f.piecewise_linear("conifer share", self._adapter.height, steps)
        noise = f.clamp("mix noise", f.noise("mix noise raw", self._seed, _CONIFER_MIX_NOISE), *_NOISE_CLAMP)
        return f.greater("conifer", share, noise)

    def _keep_single_trees(self, trees: Port, forest: Port, water: Port, shore: Port, dither: Port,
                           tree_dry: Port) -> Port:
        """Keep a share of the game's single trees outside forest, water and shore."""
        f = self._f
        keep = f.greater("keep", f.constant("keep value", self._forest.single_trees_outside), dither)
        occupied = f.maximum("forest water shore", f.maximum("forest water", forest, water), shore)
        outside = f.multiply("keep outside dry", f.multiply("keep outside", keep, f.invert("no forest", occupied)),
                             tree_dry)
        single = f.multiply("outside trees", self._adapter.original_river_trees, outside)
        return f.select("final forest", trees, trees, single, 0)


class RockAssetBuilder:
    """Rock asset index 1..n per cell (0 = none): densities per area, patches, riverbed, waterfalls, set choice."""

    def __init__(self, factory: NodeFactory, adapter: ClimateAdapter, preset: ClimatePreset, game: GameHeights,
                 layers: LandcoverLayers, patches: PatchRule) -> None:
        self._f = factory
        self._adapter = adapter
        self._p = preset.rocks
        self._preset = preset
        self._game = game
        self._layers = layers
        self._patches = patches

    def build(self, forest: Port, water: Port, shore: Port, dry: Port, dither: Port, not_steep: Port,
              above_water: Port) -> Port:
        density = self._area_density(forest, water, shore, dry, not_steep)
        if self._p.riverbed > 0:
            density = self._f.maximum("stone density", density, self._riverbed(shore, dry, not_steep))
        waterfall = self._p.waterfall
        if waterfall is not None and waterfall.strength > 0:
            area = self._preset.steep_water(waterfall.surface)
            density = self._f.maximum("stone density waterfall", density,
                                      self._waterfall(water, above_water, waterfall.strength, area))
        return self._set_index(density, dither)

    def _area_density(self, forest: Port, water: Port, shore: Port, dry: Port, not_steep: Port) -> Port:
        """Mountains (above the tree line or rock layer, no water), forest and shore, each with its own density."""
        f, p, top = self._f, self._p, self._preset.top
        above_line = f.mask("stone above tree line", self._adapter.height,
                            self._game.of(self._preset.heights.tree_line), top)
        mountain_raw = f.maximum("stone mountain raw", above_line, self._layers.binary("stone rock bin", "rock"))
        mountain = f.multiply("stone mountain", mountain_raw, f.invert("stone no water", water))
        mountain_density = f.multiply("stone mountain density", mountain,
                                      f.constant("stone mountain value", p.mountain * p.max_density))
        forest_density = f.multiply("stone forest density", forest,
                                    f.constant("stone forest value", p.forest * p.max_density))
        shore_density = f.multiply("stone shore density", shore,
                                   f.constant("stone shore value", p.shore * p.max_density))
        density = f.maximum("stone density all", f.maximum("stone density raw", mountain_density, forest_density),
                            shore_density)
        steep_limited = f.multiply("stone density steep", f.multiply("stone density dry", density, dry), not_steep)
        if p.patch_share < 1:
            edge = max(p.patch_edge, 0.001)
            spec = PatchSpec(share=p.patch_share,
                             noise=NoiseSpec(frequency=p.patch_frequency, lacunarity=2.5, octaves=3, gain=0.5),
                             center_offset=edge / 2, edge=edge, intensity=ConstantIntensity(value=1))
            return self._patches.coverage("stone density patched", steep_limited, spec)
        return f.clamp("stone density patched", steep_limited, (0, 1), (0, 1))

    def _riverbed(self, shore: Port, dry: Port, not_steep: Port) -> Port:
        """Riverbed (and shore, if configured) with its own density, without patches."""
        f, p = self._f, self._p
        bed = self._layers.binary("stone riverbed bin", "riverbed")
        if p.shore_like_riverbed:
            bed = f.maximum("stone riverbed shore bin", bed, shore)
        raw = f.multiply("stone riverbed raw", bed, f.constant("stone riverbed value", p.riverbed * p.max_density))
        return f.multiply("stone riverbed density", f.multiply("stone riverbed dry", raw, dry), not_steep)

    def _waterfall(self, water: Port, above_water: Port, strength: float, w: SteepWaterArea) -> Port:
        """Rocks in painted water where the waterfall texture lies (same steepness ramp, without blur)."""
        f, p = self._f, self._p
        if p.waterfall_test:   # diagnostic: all painted water at full density
            ramp, peak = (-2.0, -1.0), p.max_density
        else:
            ramp, peak = (w.steepness.start, w.steepness.full), strength * p.max_density
        steep = f.clamp("stone waterfall steep", self._adapter.slope, ramp, (0, peak))
        rocks = f.multiply("stone waterfall", f.multiply("stone waterfall above", steep, above_water), water)
        if w.edge_sigma > 0 and not p.waterfall_test:
            blurred = f.blur("stone waterfall water blur", water, w.edge_sigma, w.edge_size)
            inner = f.clamp("stone waterfall inner", blurred, (w.edge_inner - 0.05, w.edge_inner), (0, 1))
            rocks = f.multiply("stone waterfall in", rocks, inner)
        return rocks

    def _set_index(self, density: Port, dither: Port) -> Port:
        """Rock where dither > 1 - density (trees use the low end, so never both in one cell); the set index is the
        number of thresholds exceeded within that interval."""
        f = self._f
        total = sum(share for _, share in self._p.sets)
        rest = 1.0
        steps: list[Port] = []
        for i, (_, share) in enumerate(self._p.sets):
            threshold = f.clamp(f"stone threshold {i}", density, (0, 1), (1, 1 - rest))
            steps.append(f.greater(f"stone step {i}", dither, threshold))
            rest -= share / total
        index = steps[0]
        for i, step in enumerate(steps[1:], start=1):
            index = f.add(f"stone sum {i}", index, step)
        return index
