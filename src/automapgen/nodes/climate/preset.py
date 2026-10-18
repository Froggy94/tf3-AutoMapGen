"""The climate preset: a complete, data-defined description of a landscape type for the climate mod - height rules,
import layers on the game's layer slots, surfaces (where a kind of ground lies and how it looks), how own layers extend
the base climate's ground rules, trees and rocks - and the transition between two looks on one map.

A value of the preset files (`[defaults.climate]`). Optional parts can be switched off with `enabled = false` (their
values stay in the file) or left out; parts in tables keyed by name (layers, surfaces, patches) likewise.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from automapgen.graph import merged_tables

SLOT_COUNT = 12
"""TF3 fact: layer IDs 0-11 are read on import, higher IDs are silently ignored."""

MIN_RESOLUTION_M = 4
"""TF3 fact: layers stored finer than 4 m crash the game (generation_util::Blit)."""

METERS_PER_DEGREE_LATITUDE = 111_195

CROP_LAYER_PREFIX = "crop_"
"""Crop layers are subsets of the cropland layer."""


def _switched(value: Any) -> Any:
    """A table with `enabled = false` is absent; `enabled = true` is dropped."""
    if isinstance(value, Mapping) and "enabled" in value:
        return {k: v for k, v in value.items() if k != "enabled"} if value["enabled"] else None
    return value


def _switched_entries(value: Any) -> Any:
    """Entries of a table keyed by name without the switched-off ones; an empty list counts as an empty table."""
    if isinstance(value, list) and not value:
        return {}
    if isinstance(value, Mapping):
        return {k: v for k, v in ((k, _switched(v)) for k, v in value.items()) if v is not None}
    return value


type Optional[T] = Annotated[T | None, BeforeValidator(_switched)]
type Named[T] = Annotated[dict[str, T], BeforeValidator(_switched_entries)]


class _Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


# --- patterns -------------------------------------------------------------------------------------------------------


class SteepnessRange(_Value):
    """Ramp in the game's steepness measure (gradient_map "X^2 + Y^2", assumed tan²): 0 below `start`, 1 from `full`."""

    start: float
    """Steepness where the ramp starts (about tan² of the slope; 0.03 ~ 10°, 0.1 ~ 18°, 0.33 ~ 30°)."""
    full: float
    """Steepness of full strength; must be above start."""

    @model_validator(mode="after")
    def _rising(self) -> SteepnessRange:
        if self.full <= self.start:
            raise ValueError(f"steepness full ({self.full}) must be above start ({self.start})")
        return self


class NoiseSpec(_Value):
    """Settings of the game's fractal_noise_map."""

    frequency: float
    """Noise frequency: larger = smaller patches."""
    lacunarity: float
    """Frequency factor between noise octaves."""
    octaves: int
    """Number of noise octaves (detail levels)."""
    gain: float
    """Strength factor between noise octaves."""


class ConstantIntensity(_Value):
    kind: Literal["constant"] = "constant"
    value: float
    """Material strength inside a patch (0..1, below 1 the ground below shows through)."""


class VaryingIntensity(_Value):
    """Intensity varies by a second noise between `low` and `high`."""

    kind: Literal["varying"] = "varying"
    low: float
    """Lowest material strength inside a patch (0..1)."""
    high: float
    """Highest material strength inside a patch (0..1)."""
    noise: NoiseSpec
    """Noise that varies the strength."""


class SlopeLimit(_Value):
    """Only up to `max_deg` slope, faded out over `fade_deg` below it (degrees via the tan² assumption)."""

    max_deg: float
    """Patches only up to this slope in game (degrees; tan² assumption unverified)."""
    fade_deg: float
    """Patches fade out over this many degrees below the maximum."""

    def steepness(self) -> tuple[float, float]:
        """Steepness of full patches and of none."""
        low = math.tan(math.radians(max(self.max_deg - self.fade_deg, 0))) ** 2
        return low, max(math.tan(math.radians(self.max_deg)) ** 2, low + 1e-3)


class PatchSpec(_Value):
    """Noise patches covering about `share` of an area.

    The noise threshold is t = 1 - 2 * share; coverage ramps from 0 to the intensity over a window of width `edge`
    centred at t + `center_offset`.
    """

    share: float
    """About the area share of the patches (0 = none)."""
    noise: NoiseSpec
    """Noise that forms the patches."""
    center_offset: float
    """Shift of the patch edge in the noise (technical, keep)."""
    edge: float
    """Softness of the patch edge (width of the transition in the noise; 0.1 = hard)."""
    intensity: Annotated[ConstantIntensity | VaryingIntensity, Field(discriminator="kind")]
    """Material strength inside the patches."""
    slope_limit: Optional[SlopeLimit] = None
    """Patches only on gentle slopes (absent: on every slope)."""


# --- materials ------------------------------------------------------------------------------------------------------


class GameMaterial(_Value):
    """Terrain material of the base game, e.g. "grass_green"."""

    kind: Literal["game"] = "game"
    name: str
    """Terrain material of the game (e.g. grass_green, scree, rock, gr_br_sub)."""


class ModMaterial(_Value):
    """Terrain material shipped in the climate mod: content/terrain/materials/<folder>/<file>.tmat."""

    kind: Literal["mod"] = "mod"
    folder: str
    """Folder of the own material in the mod."""
    file: str
    """File of the own material (without .tmat)."""


type Material = Annotated[GameMaterial | ModMaterial, Field(discriminator="kind")]


class MaterialOutput(_Value):
    """A painted material: paint order (higher on top) and dithering (False = hard edge)."""

    kind: Literal["output"] = "output"
    material: Material
    """Material."""
    order: int
    """Paint order: higher lies on top (game: grass 0-20, forest floor 400, gravel 500, scree 1000/10000, rock 2000,
    snow 12000, riverbed 80000)."""
    dither: bool
    """Dithered blending with the ground below (false = hard edge)."""


# --- areas ----------------------------------------------------------------------------------------------------------


class LayerArea(_Value):
    """An import layer as coverage; `hard` cuts the interpolated 0/1 mask at 0.5 (sharp edge)."""

    kind: Literal["layer"] = "layer"
    key: str
    """Import layer."""
    hard: bool = False
    """Sharp edge (cut the interpolated mask at 0.5)."""


class GameArea(_Value):
    """An area the base climate computes itself (names resolved by the climate adapter): "lowland" (biome 1/2),
    "alpine_grass", "alpine_ground", "rock", "scree"."""

    kind: Literal["game"] = "game"
    name: str
    """Area of the base climate."""


class AreaProduct(_Value):
    """Coverage of `first` x `second`."""

    kind: Literal["product"] = "product"
    first: Area
    """First area."""
    second: Area
    """Second area."""


class SteepWaterArea(_Value):
    """Inside `water` where the water surface itself is steep (waterfall), soft transition."""

    kind: Literal["steep_water"] = "steep_water"
    water: Area
    """Water area."""
    steepness: SteepnessRange
    """Steepness of the water surface for the waterfall."""
    blur_sigma: float
    """Blur of the steepness for a soft transition (game blur_map, unit probably cells)."""
    blur_size: int
    """Blur kernel size."""
    edge_sigma: float
    """Only slopes inside the water count: blur of the water mask (0 = edge slopes count)."""
    edge_size: int
    """Kernel size of the water mask blur."""
    edge_inner: float
    """Inside = blurred water mask at least this value (closer to 1 = further from the edge)."""
    gain: float
    """Strength = min(1, blurred steepness x gain)."""


type Area = Annotated[LayerArea | GameArea | AreaProduct | SteepWaterArea, Field(discriminator="kind")]


def layers_of(area: LayerArea | GameArea | AreaProduct | SteepWaterArea) -> set[str]:
    """The import layers an area reads."""
    match area:
        case LayerArea():
            return {area.key}
        case AreaProduct():
            return layers_of(area.first) | layers_of(area.second)
        case SteepWaterArea():
            return layers_of(area.water)
    return set()


# --- looks and surfaces ---------------------------------------------------------------------------------------------


class PatchLayer(_Value):
    """Patches of another material on top of a surface (the key in `patches` names the material output)."""

    output: MaterialOutput
    """Material of the patches."""
    patch: PatchSpec
    """Patch pattern."""


class Look(_Value):
    """How a surface looks. `base`: own material output, or a game material replacing the material of the game's own
    output for a game area, or absent (keep the game's look). `patches` lie on top of the surface."""

    base: Optional[Annotated[MaterialOutput | GameMaterial, Field(discriminator="kind")]] = None
    """Base material (absent = keep the game's look)."""
    patches: Named[PatchLayer] = {}
    """Patch layers on top, by name."""


class Surface(_Value):
    """A kind of ground (the key in `surfaces` names it and its base material output)."""

    area: Area
    """Where the surface lies."""
    look: Look = Look()
    """How the surface looks."""


class GameGroundRules(_Value):
    """How own layers extend the base climate's own ground rules: the rock layer adds rock (steep) and scree (flat)
    to the game's rock and scree; the forest floor follows the forest layer."""

    rock_layer: str
    """Layer that adds rock (steep) and scree (flat)."""
    forest_floor_layer: str
    """Layer that defines the forest floor."""
    steep_rock: SteepnessRange
    """Rock on steep slopes (game rule; original 0.7 / 0.75; smaller = rock already on gentler slopes)."""


# --- layers ---------------------------------------------------------------------------------------------------------


class LayerSpec(_Value):
    """An import layer of the climate. The key in `layers` is both the suffix of the mask file `<map>_<key>.png` and
    the layer's name in the game's import dialog, which lists the layers sorted by key like the files."""

    color: tuple[float, float, float]
    """Colour of the layer in the import dialog (r, g, b 0..1)."""
    slot: int
    """Layer slot ID 0-11 (the game ignores higher IDs)."""
    resolution: int
    """Stored resolution in m per pixel (at least 4; 8 saves a lot of memory on large maps)."""


class SlotPlan(_Value):
    """Own layers plus the original layers of the base climate on the 12 slots."""

    layers: Named[LayerSpec]
    """Own import layers by key."""
    original: dict[str, int]
    """Layers of the base climate that keep their slot."""

    @model_validator(mode="after")
    def _valid_slots(self) -> SlotPlan:
        slots = [layer.slot for layer in self.layers.values()] + list(self.original.values())
        keys = [*self.layers, *self.original]
        problems = []
        if len(set(keys)) != len(keys):
            problems.append(f"layer keys are not unique: {sorted(keys)}")
        if len(set(slots)) != len(slots):
            problems.append(f"layer slots are used twice: {sorted(slots)}")
        outside = sorted(s for s in slots if not 0 <= s < SLOT_COUNT)
        if outside:
            problems.append(f"layer slots {outside} are outside 0-{SLOT_COUNT - 1} (the game ignores them)")
        too_fine = sorted(key for key, layer in self.layers.items() if layer.resolution < MIN_RESOLUTION_M)
        if too_fine:
            problems.append(f"layers {too_fine} finer than {MIN_RESOLUTION_M} m crash the game")
        if any(key.startswith(CROP_LAYER_PREFIX) for key in self.layers) and "cropland" not in self.layers:
            problems.append("crop layers need the layer cropland")
        if problems:
            raise ValueError("; ".join(problems))
        return self


# --- vegetation and height rules ------------------------------------------------------------------------------------


class HeightLimits(_Value):
    """Real elevations of the climate's height rules in m a.s.l. (start = transition begins, full = 100 %)."""

    scree_start: float
    """Scree on flat ground starts."""
    scree_full: float
    """Scree on flat ground full."""
    rock_start: float
    """Rock starts (always also on steep slopes)."""
    rock_full: float
    """Rock full."""
    snow_hollow_start: float
    """Snow in hollows starts."""
    snow_hollow_full: float
    """Snow in hollows full."""
    snow_start: float
    """Snow on surfaces starts."""
    snow_full: float
    """Snow on surfaces full."""
    trees_dense_until: float
    """Dense mountain forest up to here."""
    trees_sparse: float
    """Sparse forest."""
    tree_line: float
    """Tree line."""
    mountains_min: float
    """Mountain mask only from here."""


class ClassicTrees(_Value):
    """TF2 trees of the base game mixed into the forest (sets named by a startup script)."""

    sets: dict[str, tuple[int, tuple[str, ...]]]
    """Set name -> (index in the forest output, tree models)."""
    pine_set: str
    """Set used for Scots pine."""
    broadleaf_set: str
    """Set used for the broadleaf mix."""
    pine_share: float
    """Share of Scots pine in the conifer forest (below pine_max)."""
    pine_max: float
    """Scots pine only below this elevation (m a.s.l.)."""
    broadleaf_share: float
    """Share of lime/maple/oak in the broadleaf forest."""
    noise_frequency: float
    """Stands instead of single trees (larger = smaller stands)."""


class ForestParams(_Value):
    density: float
    """Tree probability per cell in the forest mask (original game about 0.06-0.1)."""
    single_trees_outside: float
    """Share of the game's single trees kept outside the forest mask (0 = none)."""
    index_lowland: int
    """Tree set index for broadleaf in biome 1 (6 = temperate_plains)."""
    index_hills: int
    """Tree set index for broadleaf in biome 2 (3 = temperate_hills)."""
    index_conifer: int
    """Tree set index for conifers (7 = temperate_conifer)."""
    conifer_start: float
    """Below this elevation mixed forest with conifer_share_below conifers (m a.s.l.)."""
    conifer_full: float
    """From this elevation only conifers (m a.s.l.)."""
    conifer_share_below: float
    """Conifer share below conifer_start."""
    max_steepness: float
    """No trees from this steepness (keep slightly below the steep rock start)."""
    classic: Optional[ClassicTrees] = None
    """Classic Trees (TF2 trees of the base game) mixed in; absent: off."""


class WaterfallRocks(_Value):
    """Rock assets where the waterfall surface lies (its steepness ramp and inner mask, without blur)."""

    strength: float
    """Rock strength on waterfalls (x max_density; 0 = none)."""
    surface: str
    """Surface whose steep water area places the rocks."""


class RockAssetParams(_Value):
    max_density: float
    """Rock probability per cell at strength 1 (original subarctic: tundra 0.1, coast 0.45)."""
    mountain: float
    """Strength in the mountains (above the tree line or rock layer, without water)."""
    forest: float
    """Strength in the forest."""
    shore: float
    """Strength on the shore (when not like the riverbed)."""
    riverbed: float
    """Strength in the riverbed (without patches; 1.0 was too dense)."""
    waterfall_test: bool = False
    """Diagnostic: rocks on ALL painted water at full density."""
    shore_like_riverbed: bool
    """Shore gets the riverbed density without patches."""
    sets: tuple[tuple[str, float], ...]
    """Rock sets of the game (categoryList) with their share of the mix."""
    patch_share: float
    """Rocks in patches: about the area share (1 = everywhere, evenly)."""
    patch_frequency: float
    """Noise frequency of the rock patches (larger = smaller patches)."""
    patch_edge: float
    """Width of the transition at the patch edge (0 = hard)."""
    waterfall: Optional[WaterfallRocks] = None
    """Rocks on waterfalls; absent: none."""


# --- the preset -----------------------------------------------------------------------------------------------------


class ClimatePreset(_Value):
    """Base game climate, height rules, layers on slots, surfaces, game ground rules, trees and rocks."""

    name: str
    """Name of the landscape type (used in the climate mod's name)."""
    base_climate: str
    """Game climate the mod is based on (temperate)."""
    heights: HeightLimits
    """Height rules."""
    slots: SlotPlan
    """Import layers on the 12 layer slots."""
    surfaces: Named[Surface]
    """Surfaces by key: where each kind of ground lies and how it looks (painted in this order)."""
    ground: GameGroundRules
    """How own layers extend the game's rock, scree and forest floor."""
    forest: ForestParams
    """Trees."""
    rocks: RockAssetParams
    """Rock assets."""
    no_cover_below: float
    """No trees and rocks below this game height (0 = water line; m)."""
    top: float
    """'Infinitely high' in curves (game metres)."""
    map_preview_level: float
    """Middle colour level of the map preview (m a.s.l., cosmetic)."""

    @model_validator(mode="after")
    def _consistent(self) -> ClimatePreset:
        used = {self.ground.rock_layer, self.ground.forest_floor_layer}
        for surface in self.surfaces.values():
            used |= layers_of(surface.area)
        missing = sorted(used - set(self.slots.layers))
        if missing:
            raise ValueError(f"surfaces use layers without slot: {missing}")
        if self.rocks.waterfall is not None:
            self.steep_water(self.rocks.waterfall.surface)
        return self

    def steep_water(self, surface_key: str) -> SteepWaterArea:
        """Steep water area of a surface (used by the waterfall rocks)."""
        surface = self.surfaces.get(surface_key)
        if surface is None or not isinstance(surface.area, SteepWaterArea):
            raise ValueError(f"no surface {surface_key!r} with a steep water area")
        return surface.area


# --- economy ---------------------------------------------------------------------------------------------------------


class EconomySettings(_Value):
    """The climate's economy (a value of the preset)."""

    custom: bool = False
    """Own economy (no oil platforms, refinery at oil wells, glass works at sand pits); false = the game's economy."""
    extra_industries: bool = False
    """Also unlock industries and cargos of other climates (needs the mod DHE Industries)."""
    water_industry_fraction: str = "3.0 / 41.0"
    """Share of water industries among new industries (temperate: 6 / 41)."""
    name: str = "Alps"
    """Name of the own economy."""
    name_with_extra_industries: str = "Alps (all industries)"
    """Name of the own economy with extra industries."""
    removed_industries: tuple[str, ...] = ("oil_platform",)
    """Industries the own economy leaves out."""


# --- transition between two looks -----------------------------------------------------------------------------------


class BiomeFactor(_Value):
    """Maximum share of the secondary look: `default` everywhere unless overridden for a biome ID (0..4)."""

    default: float
    """Share of the secondary look where the transition mask is full (0..1)."""
    by_biome: dict[int, float] = {}
    """Different share per biome ID (1 = valley, 2 = mountain forest, 3 = subalpine, 4 = alpine)."""


class PatchMix(_Value):
    """The secondary look appears in noise patches; `edge` = softness of the patch border (0 = hard)."""

    kind: Literal["patches"] = "patches"
    noise: NoiseSpec
    """Noise of the patches (frequency: larger = smaller patches)."""
    edge: float
    """Softness of the patch border in noise units (0 = hard)."""


class SoftMix(_Value):
    """Both looks are blended by the share everywhere (no patches)."""

    kind: Literal["soft"] = "soft"


class OverlayMix(_Value):
    """The primary look stays complete; the secondary look is painted on top of it on the whole surface with the
    share as strength, varied by a large noise."""

    kind: Literal["overlay"] = "overlay"
    noise: NoiseSpec
    """Noise that varies the strength (frequency: larger = smaller variations)."""
    variation: float
    """Relative variation of the strength (0.3 = share x 0.7 .. 1.3; 0 = even)."""


type MixSpec = Annotated[PatchMix | SoftMix | OverlayMix, Field(discriminator="kind")]


class SurfaceTransition(_Value):
    factor: BiomeFactor
    """Share of the secondary look."""
    mix: Optional[MixSpec] = None
    """Own mix function for this surface (absent = the transition's mix)."""


class GradientZone(_Value):
    """Transition by latitude: 0 north of `start_lat`, rising linearly to 1 at `end_lat`, 1 beyond."""

    kind: Literal["gradient"] = "gradient"
    start_lat: float
    """Latitude where the transition starts (mask 0), degrees."""
    end_lat: float
    """Latitude where the transition is complete (mask 1), degrees."""

    @model_validator(mode="after")
    def _apart(self) -> GradientZone:
        if self.start_lat == self.end_lat:
            raise ValueError("gradient zone: start_lat and end_lat must differ")
        return self

    @property
    def real_length_m(self) -> float:
        """Real distance from the start to the end of the transition (north-south)."""
        return abs(self.start_lat - self.end_lat) * METERS_PER_DEGREE_LATITUDE


class TransitionLayer(LayerSpec):
    """The import layer of the transition mask."""

    key: str
    """Layer key in the climate (the map graph writes the mask as <map>_transition_mask.png)."""


class Transition(_Value):
    """A second look on part of the map: the secondary preset is the primary one with the differences in `secondary`
    (merged table by table, like parameter sets)."""

    name: str
    """Name of the landscape with this transition (used in the climate mod's name instead of the preset's)."""
    secondary: dict[str, Any]
    """Differences of the secondary preset to the primary one."""
    zone: GradientZone
    """Where the secondary look applies."""
    layer: TransitionLayer
    """Import layer of the transition mask."""
    mix: MixSpec
    """How the two looks are mixed."""
    surfaces: Named[SurfaceTransition]
    """Surfaces that change, by key, and their shares."""


class Landscape:
    """What a map's climate uses: the primary preset, optionally a secondary one with a transition. With a transition
    the primary preset gets the mask layer (on a free slot)."""

    def __init__(self, primary: ClimatePreset, transition: Transition | None = None) -> None:
        self.transition = transition
        self.secondary: ClimatePreset | None = None
        self.name = primary.name if transition is None else transition.name
        self.primary = primary if transition is None else self._with_mask_layer(primary, transition)
        if transition is None:
            return
        self.secondary = ClimatePreset.model_validate(merged_tables(primary.model_dump(), transition.secondary))
        if self.secondary.base_climate != primary.base_climate:
            raise ValueError("both presets of a transition need the same base climate")
        missing = sorted(set(transition.surfaces) - (set(primary.surfaces) & set(self.secondary.surfaces)))
        if missing:
            raise ValueError(f"transition surfaces {missing} must exist in both presets")

    @staticmethod
    def _with_mask_layer(preset: ClimatePreset, transition: Transition) -> ClimatePreset:
        layer = LayerSpec(**transition.layer.model_dump(exclude={"key"}))
        slots = SlotPlan(layers={**preset.slots.layers, transition.layer.key: layer}, original=preset.slots.original)
        return preset.model_copy(update={"slots": slots})
