"""Industry planning from the terrain's layers (no real deposits): building sites and resource areas, raw-material
industries per area, processing industries near medium and large towns, demand per town, names; map editor import
files, a JSON list for later steps, previews.

1. Sites: ground roughly level within a radius, no water/shore/glacier/rock, distance to town centres. Kinds:
   factory (small, level), field (large, with fields/pits), compact (small, more tolerant in the mountains).
2. Suitability per resource (0..1) from landcover, height, slope, nearness to water/mountains/towns, a coarse noise
   and the share of fitting sites nearby. Resource areas around the best places.
3. Placement: one raw-material industry per area (agriculture by demand), factories near towns by size class, demand
   per town (one commercial and one industrial cargo), names after the nearest place.
All on a work grid of work_px heightmap pixels. The production rules come from the game's industry files.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from PIL import Image as PilImage
from PIL import ImageDraw
from pydantic import BaseModel, ConfigDict
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
    gaussian_filter,
    label,
    maximum_filter,
    minimum_filter,
)

from automapgen.graph import NodeContext, NodeParams, data_type, node, param
from automapgen.nodes.documents import JsonDocument, JsonDocumentCodec
from automapgen.nodes.gamefiles import GameFiles
from automapgen.nodes.images import Image
from automapgen.nodes.maps import GAME_METERS_PER_PIXEL, MapFormat, MapFrame, MapProjection
from automapgen.nodes.osm import OsmPoints
from automapgen.nodes.rasters import Field, Mask, resize_floats
from automapgen.nodes.rivers import preview_font
from automapgen.nodes.towns import TextDocument, TownList, lua_string

_ANGLES = [0, 1.5708, 3.14159, -1.5708]
_TRADE = "aussenhandel"
_WATER_COLOR = (90, 140, 200)
_GLACIER_COLOR = (235, 240, 250)

type BoolMap = NDArray[np.bool_]
type FloatMap = NDArray[np.float32]
type Rule = tuple[dict[str, float], dict[str, float], float]    # inputs, outputs (per cycle), cycle duration
type Log = Callable[[str], None]


# --- settings -------------------------------------------------------------------------------------------------------


class _Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class SiteKind(_Settings):
    radius_game_m: float
    """Radius of the footprint ..."""
    relief_game_m: float
    """... and the largest height difference in it."""
    color: tuple[int, int, int]
    """Preview colour."""
    label: str
    """Legend of the preview."""


class ResourceSpec(_Settings):
    industries: tuple[str, ...]
    """Industry types of this resource."""
    sites: tuple[str, ...]
    """Site kinds the industries can use."""
    regions: int
    """Number of areas."""
    smooth_game_m: float
    """Smoothing of the suitability."""
    color: tuple[int, int, int]
    """Preview colour."""
    label: str
    """Legend of the preview."""
    peak: float | None = None
    """Own area threshold (default: region_peak_share)."""
    radius_game_m: float | None = None
    """Own area radius (default: region_radius_game_m)."""
    spacing_game_m: float | None = None
    """Own peak spacing (default: region_spacing_game_m)."""
    height_m: tuple[float, float, float, float] = (-1e6, -1e6, 1e6, 1e6)
    """Real height band of the suitability in m a.s.l. (from 0, full from, full to, to 0); unused by fish."""


class SiteParams(_Settings):
    work_px: int
    """Work grid: heightmap pixels per cell (4 = 16 game metres)."""
    sites: dict[str, SiteKind]
    """Building sites: footprint and largest height difference per kind (factory, field, compact)."""
    town_clear_game_m: float
    """Distance of sites to town centres."""
    site_near_game_m: float
    """Share of sites within this radius enters the suitability ..."""
    site_full_share: float
    """... fully from this share (weaker below, down to 0)."""
    local_game_m: float
    """Landcover counts as share within this radius."""
    noise: float
    """Suitability x (1 - noise + noise x noise field 0..1) ..."""
    noise_game_m: float
    """... with this structure size."""
    seed: int
    """Random seed of the noise."""
    resources: dict[str, ResourceSpec]
    """Resource areas: industries, site kinds, number, smoothing."""
    region_peak_share: float
    """Area = region above this share of its own peak."""
    region_radius_game_m: float
    """Area at most this far around the peak."""
    region_spacing_game_m: float
    """Minimum distance between the peaks of one resource."""
    region_min_km2: float
    """Smaller areas are dropped (game area)."""


class PlanParams(_Settings):
    seed: int
    """Random seed of the placement."""
    raw_resource: dict[str, str]
    """Raw-material industry -> resource area."""
    intermediate: tuple[str, ...]
    """Intermediate products, preferably in medium towns."""
    complex: tuple[str, ...]
    """Complex products, preferably in large towns."""
    production_per_type: dict[str, int]
    """Count per processing type (default 1; local goods)."""
    medium_town_capacity: float
    """Towns from this start capacity are medium (intermediate products) ..."""
    large_town_capacity: float
    """... and from this large (complex products); below: villages, agriculture."""
    specializations: dict[str, int]
    """Factories per town class (village, medium, large)."""
    equal_town_slots: int
    """Factories per town when the real town size is ignored (all towns medium)."""
    import_cost_game_m: float
    """An input without source on the map (foreign trade) counts as this far."""
    own_allowed: tuple[str, ...]
    """Towns do not demand their own products - except these."""
    trade_points: int
    """Foreign trade points at the map edge (0 = none)."""
    trade_spread_m_per_km: float
    """Distance to other trade points: per km like this many metres lower."""
    trade_edge_game_m: float
    """Trade points at most this far from the (inner) map edge."""
    producer_pref: dict[str, str]
    """Cargo -> preferred producer."""
    town_cargos: dict[str, tuple[int, str]]
    """Town cargo -> (level, "com"/"ind"); levels 0 and 1 at start."""
    far_cargos: frozenset[str]
    """Long-distance cargos."""
    local_only: frozenset[str]
    """Only for towns up to local_max_game_m from the resource area."""
    local_max_game_m: float
    """See local_only."""
    far_min_capacity: float
    """From this start capacity one long-distance cargo (commercial/industrial in turns; all towns when the real
    town size is ignored)."""
    balance_game_m: float
    """Local cargos: per town served already (per producer) this much detour."""
    towns_per_local_producer: int
    """Towns one factory or farm supplies (assumption for the amounts)."""
    towns_per_far_producer: int
    """Towns one long-distance producer supplies."""
    max_per_type: int
    """At most this many per industry type ..."""
    agriculture: frozenset[str]
    """... except agriculture (at least one per area, by demand)."""
    factory_town_game_m: float
    """Factories at most this far from their town."""
    raw_town_game_m: tuple[float, float]
    """Mines etc.: suitability rises with the distance to the next town (from .. to)."""
    agri_town_game_m: tuple[float, float]
    """Agriculture: suitability rises with the nearness to a town."""
    spacing_game_m: float
    """Minimum distance between industries."""
    map_edge_game_m: float
    """Distance to the map edge."""
    region_edge_game_m: float
    """Raw-material industries also this far outside their area."""
    compact: frozenset[str]
    """Compact variant in the mountains (mod buildings)."""
    compact_mountain_share: float
    """Compact if the area is mountains to more than this share ..."""
    compact_field_share: float
    """... or has less than this share of sites for the large variant."""
    fish_shore_game_m: float
    """Fishing grounds this far from the shore."""
    on_water: frozenset[str]
    """Industries placed on water."""


class NamingParams(_Settings):
    place_types: dict[str, float]
    """"<place> <type>" after the nearest OSM place: distance factor per place kind (larger places preferred)."""
    max_game_m: float
    """Farther than this: the nearest planned town."""
    type_names: dict[str, str]
    """Type name per industry (also used by the name mod)."""


class ModFiles(_Settings):
    """The buildings of the industry mod "DHE Industries" and "DHE Foreign Trade" (compact, other climates, trade)."""

    mod_id: str = "DHE_industries_1"
    """Mod ID of the industry mod."""
    folder: str = "industries/dhe"
    """Folder of its constructions below content/."""
    trade_mod_id: str = "DHE_foreign_trade_1"
    """Mod ID of the foreign trade mod."""
    trade_folder: str = "industries/dhe_trade"
    """Folder of its construction below content/."""


class IndustrySettings(_Settings):
    """The industry planning of a climate (a value of the climate preset)."""

    sites: SiteParams
    """Building sites, suitability and resource areas."""
    plan: PlanParams
    """Placement, town specialisation, demand, foreign trade."""
    naming: NamingParams
    """Industry names after the nearest place."""
    standard_only: bool = True
    """Only the game's standard buildings: no compact variants, no industries and cargos of other climates, no foreign
    trade (import without the industry mod)."""
    extra_industries: tuple[str, ...] = ()
    """Industries of other climates in the industry mod."""
    extra_cargos: tuple[str, ...] = ()
    """Cargos of other climates in the industry mod."""
    mod: ModFiles = ModFiles()
    """Building files of the industry mod."""
    preview_width: int = 2000
    """Width of the preview pictures in pixels."""
    short_names: dict[str, str] = {}
    """Short name per industry in the preview."""
    industry_colors: dict[str, tuple[int, int, int]] = {}
    """Preview colour per raw-material industry."""

    def effective(self) -> IndustrySettings:
        """The settings with standard_only applied: without compact variants, industries/cargos of other climates and
        foreign trade."""
        if not self.standard_only:
            return self
        extra, cargos = frozenset(self.extra_industries), frozenset(self.extra_cargos)
        resources = {key: spec.model_copy(update={"sites": tuple(s for s in spec.sites if s != "compact")})
                     for key, spec in self.sites.resources.items() if not set(spec.industries) & extra}
        plan = self.plan.model_copy(update={
            "raw_resource": {n: k for n, k in self.plan.raw_resource.items() if n not in extra and k in resources},
            "intermediate": tuple(n for n in self.plan.intermediate if n not in extra),
            "complex": tuple(n for n in self.plan.complex if n not in extra),
            "town_cargos": {g: v for g, v in self.plan.town_cargos.items() if g not in cargos},
            "far_cargos": self.plan.far_cargos - cargos,
            "compact": frozenset(),
            "trade_points": 0})
        return self.model_copy(update={"sites": self.sites.model_copy(update={"resources": resources}),
                                       "plan": plan})


class IndustryParams(NodeParams):
    industries: IndustrySettings
    """The industry planning (from the climate preset)."""
    ignore_town_size: bool = False
    """industries.plan only: all towns equal (they start at the same size): each is medium with
    plan.equal_town_slots factories and gets one long-distance cargo (commercial/industrial in turns)."""
    industry_density: float = param(1.0, gt=0)
    """Multiplies plan.production_per_type, plan.max_per_type and the number of areas per resource (rounded, at least
    1 each)."""
    all_cargos: bool = False
    """Resources without a fitting area get areas anyway, regardless of land cover and height (unrealistic; fish
    excluded); processing industries whose inputs have no source on the map are placed anyway."""


# --- data -----------------------------------------------------------------------------------------------------------


@dataclass
class TownSpot:
    """A planned town on the work grid (u, v in work cells)."""

    name: str
    x: float
    y: float
    size: list[float]
    capacity: float
    u: float
    v: float
    cls: str = ""
    slots: int = 0
    produces: set[str] = field(default_factory=set)
    industries: list[str] = field(default_factory=list)
    needs: dict[str, str] = field(default_factory=dict)

    def cell(self, h: int, w: int) -> tuple[int, int]:
        return min(h - 1, max(0, round(self.v))), min(w - 1, max(0, round(self.u)))


@data_type("industry_grid", "The terrain on the industry work grid: heights, layers, game water, towns.")
@dataclass(frozen=True)
class WorkGrid:
    """Terrain and layers on the work grid."""

    cell_m: float                              # game metres per cell
    real_h: FloatMap                           # real height m a.s.l.
    game_h: FloatMap                           # game height
    real_water: BoolMap                        # below the game water
    layers: Mapping[str, FloatMap]             # landcover layers 0..1
    towns: tuple[TownSpot, ...]

    @property
    def shape(self) -> tuple[int, int]:
        h, w = self.real_h.shape
        return h, w

    @property
    def slope(self) -> FloatMap:
        gy, gx = np.gradient(self.game_h, self.cell_m)
        result: FloatMap = np.hypot(gx, gy)
        return result

    @property
    def water_any(self) -> BoolMap:
        return np.asarray(self.real_water | (self.layers["water"] > 0.5) | (self.layers["shore"] > 0.5))

    @property
    def blocked(self) -> BoolMap:
        """WorldCover settlement does not block: in game there are only the planned towns."""
        return np.asarray(self.water_any | (self.layers["glacier"] > 0.5) | (self.layers["rock"] > 0.5))

    def dist_town(self) -> NDArray[Any]:
        h, w = self.shape
        town_pts = np.zeros((h, w), bool)
        for t in self.towns:
            town_pts[t.cell(h, w)] = True
        if not self.towns:
            return np.full((h, w), 1e9, np.float32)
        result: NDArray[Any] = distance_transform_edt(~town_pts) * self.cell_m
        return result


@data_type("resource_maps", "Building sites per kind, suitability and resource areas per resource (work grid).")
@dataclass(frozen=True)
class SiteMaps:
    sites: dict[str, BoolMap]
    score: dict[str, FloatMap]
    regions: dict[str, NDArray[np.uint8]]


@data_type("industry_rules", "Production rules of the game's industries: inputs, outputs per cycle, cycle time.",
           codec=JsonDocumentCodec(lambda data: IndustryRules(data)), preview=lambda r: ", ".join(sorted(r.data)))
@dataclass(frozen=True)
class IndustryRules(JsonDocument):
    """{industry: [inputs, outputs, cycle duration, alternative inputs]}; alternative inputs (optional) = the inputs of
    further rules with the same outputs (food factory: fish or meat)."""

    def rule(self, name: str) -> Rule:
        ins, outs, duration = self.data[name][:3]
        return dict(ins), dict(outs), float(duration)

    def input_choices(self, name: str) -> list[dict[str, float]]:
        """The inputs of the first rule, then the alternatives."""
        entry = self.data[name]
        return [dict(ins) for ins in [entry[0], *(entry[3] if len(entry) > 3 else [])]]


@data_type("industry_list", "Planned industries: [{name, type, x, y, fileName, onWater, site}], game metres.",
           codec=JsonDocumentCodec(lambda data: IndustryList(data)), preview=lambda i: f"{len(i.data)} industries")
@dataclass(frozen=True)
class IndustryList(JsonDocument):
    pass


# --- game industry rules ----------------------------------------------------------------------------------------------


@node("game.industry_rules", output="rules")
def industry_rules(context: NodeContext) -> IndustryRules:
    """The first (non-booster) production rule of every industry of the game (base/content/industries/*.zip) and the
    inputs of alternative rules with the same outputs."""
    files = GameFiles.at(context.game_folder())
    rules: dict[str, Any] = {}
    for name in files.packages("industries"):
        try:
            (ins, outs, duration), alternatives = _read_rule(files.text(f"industries/{name}/{name}.con.lua"))
        except (FileNotFoundError, ValueError, AssertionError):
            continue                                   # shared files, platforms without a construction script
        rules[name] = [ins, outs, duration, alternatives]
    context.log(f"{len(rules)} industries of the game read")
    return IndustryRules(rules)


def _read_rule(c: str) -> tuple[Rule, list[dict[str, float]]]:
    """The first rule and the inputs of the further rules with the same outputs."""
    blk = c[c.index("local stockListConfig"):c.index("industrialCapacity")]
    stocks = re.findall(r'type = "(INPUT|OUTPUT)_STOCK",\s*cargoType = "::/cargos/(\w+)/', blk)
    input_names = [g for t, g in stocks if t == "INPUT"]

    def parse(block: str) -> Rule:
        inp = re.search(r"input = \{\s*\{([^}]*)\}", block)
        amounts = [float(v) for v in inp.group(1).split(",") if v.strip()] if inp else []
        ins = {g: a for g, a in zip(input_names, amounts, strict=False) if a > 0}
        outs = {g: float(v) for g, v in re.findall(r'cargos/(\w+)/\w+\.cargo"\]\s*=\s*([\d.]+)', block)}
        capacity = re.search(r"capacity = ([\d.]+)", block)
        assert capacity is not None
        return ins, outs, float(capacity.group(1))

    first, *rest = [parse(b) for b in re.split(r"\n\t\t\t\},\s*\{", blk[blk.index("rules = {"):])]
    return first, [ins for ins, outs, _ in rest if ins and outs == first[1] and ins != first[0]]


# --- sites and resource areas ------------------------------------------------------------------------------------


def ramp(x: NDArray[Any], a: float, b: float) -> NDArray[Any]:
    """0 at a, 1 at b (a > b: falling)."""
    if a == b:
        return (x >= a).astype(np.float32)
    result: NDArray[Any] = np.clip((x - a) / (b - a), 0, 1)
    return result


def band(x: NDArray[Any], lo0: float, lo1: float, hi1: float, hi0: float) -> NDArray[Any]:
    result: NDArray[Any] = ramp(x, lo0, lo1) * ramp(x, hi0, hi1)
    return result


class Areas(NamedTuple):
    grid: WorkGrid
    maps: SiteMaps
    resources_preview: Image
    sites_preview: Image


@node("industries.resource_areas")
def resource_areas(heights: Field, heightmap: Image, black_m: float, map_format: MapFormat, forest: Mask,
                   meadow: Mask, cropland: Mask, rock: Mask, glacier: Mask, water: Mask, shore: Mask, mountains: Mask,
                   towns: TownList, params: IndustryParams, context: NodeContext) -> Areas:
    """Building sites per kind and resource areas from the terrain the game will see (`heights` = decoded heightmap,
    `heightmap` = its pixels, 0 = real water core) and the layers; towns keep their distance."""
    settings = params.industries.effective()
    sp = settings.sites
    px_h, px_w = heights.values.shape
    h, w = -(-px_h // sp.work_px), -(-px_w // sp.work_px)
    cell = sp.work_px * GAME_METERS_PER_PIXEL

    def work(a: NDArray[Any]) -> FloatMap:
        return resize_floats(a, w, h, PilImage.Resampling.BOX)

    real_h = work(heights.values)
    layers = {key: work(mask.values.astype(np.float32)) for key, mask in
              (("forest", forest), ("meadow", meadow), ("cropland", cropland), ("rock", rock), ("glacier", glacier),
               ("water", water), ("shore", shore), ("mountains", mountains))}
    grid = WorkGrid(cell, real_h, (real_h - black_m) * map_format.height_game_per_real,
                    work((heightmap.pixels == 0).astype(np.float32)) > 0.5, layers,
                    _town_spots(towns, px_w, px_h, sp.work_px))
    context.log(f"map {px_w} x {px_h} px, work grid {w} x {h} ({cell:g} m), {len(grid.towns)} towns")
    dist_town = grid.dist_town()
    sites = _SiteFinder(sp, context.log).find(grid, dist_town)
    maps = _ResourceAreaFinder(sp, params.industry_density, params.all_cargos, context.log).find(grid, sites,
                                                                                                  dist_town)
    previews = _Previews(grid, settings.preview_width)
    return Areas(grid, maps, Image(np.asarray(previews.resources(sp.resources, maps.regions))),
                 Image(np.asarray(previews.sites(sp.sites, maps.sites))))


def _town_spots(towns: TownList, width_px: int, height_px: int, work_px: int) -> tuple[TownSpot, ...]:
    """The towns as the map editor's import file gives them (size factors as written, x/y to 0.1 m)."""
    spots = []
    for t in towns.data:
        factor = float(f"{t['capacity'] / 100:g}")
        sf = [factor] * 3
        x, y = float(f"{t['x']:.1f}"), float(f"{t['y']:.1f}")
        spots.append(TownSpot(t["name"], x, y, sf, sum(sf) / len(sf) * 100,
                              (x / GAME_METERS_PER_PIXEL + (width_px - 1) / 2) / work_px,
                              ((height_px - 1) / 2 - y / GAME_METERS_PER_PIXEL) / work_px))
    return tuple(spots)


class _SiteFinder:
    """Building sites per kind."""

    def __init__(self, params: SiteParams, log: Log) -> None:
        self._p = params
        self._log = log

    def find(self, grid: WorkGrid, dist_town: NDArray[Any]) -> dict[str, BoolMap]:
        sites = {}
        blocked = grid.blocked
        for name, kind in self._p.sites.items():
            r = max(1, round(kind.radius_game_m / grid.cell_m))
            yy, xx = np.ogrid[-r:r + 1, -r:r + 1]
            disk = xx ** 2 + yy ** 2 <= r * r
            relief = maximum_filter(grid.game_h, footprint=disk) - minimum_filter(grid.game_h, footprint=disk)
            sites[name] = (relief <= kind.relief_game_m) & ~binary_dilation(blocked, structure=disk) & \
                (dist_town >= self._p.town_clear_game_m)
            self._log(f"sites {name:<8} (radius {kind.radius_game_m:g} m, height difference <= "
                      f"{kind.relief_game_m:g} m): {sites[name].mean() * 100:.1f} % of the map")
        return sites


class _ResourceAreaFinder:
    """Suitability per resource and the resource areas around the best places."""

    def __init__(self, params: SiteParams, density: float, all_cargos: bool, log: Log) -> None:
        self._p = params
        self._density = density
        self._all_cargos = all_cargos
        self._log = log

    def find(self, grid: WorkGrid, sites: dict[str, BoolMap], dist_town: NDArray[Any]) -> SiteMaps:
        p = self._p
        h, w = grid.shape
        suit = _Suitability(p, grid, sites, dist_town)
        rng = np.random.default_rng(p.seed)
        yy, xx = np.mgrid[0:h, 0:w]
        score, regions = {}, {}
        for key, spec in p.resources.items():
            if spec.regions > 0:
                spec = spec.model_copy(update={"regions": max(1, round(spec.regions * self._density))})
            s = suit.of(key, spec.height_m).astype(np.float32)
            if key == "fish":
                score[key] = s
                regions[key] = self._regions(key, spec, s, grid, yy, xx)
                continue
            noise, share = self._site_factors(spec, suit, rng, grid)
            s *= suit.dry
            s *= noise
            s *= share
            score[key] = s
            regions[key] = self._regions(key, spec, s, grid, yy, xx)
            if self._all_cargos and spec.regions > 0 and not regions[key].any():
                score[key] = (suit.dry * noise * share).astype(np.float32)
                regions[key] = self._regions(key, spec, score[key], grid, yy, xx)
                self._log(f"  {spec.label} no fitting area: {int(regions[key].max())} areas without regard to land "
                          "cover and height (all_cargos, unrealistic)")
        return SiteMaps(sites, score, regions)

    def _site_factors(self, spec: ResourceSpec, suit: _Suitability, rng: np.random.Generator,
                      grid: WorkGrid) -> tuple[FloatMap, FloatMap]:
        """Noise factor (a new noise field per call) and the factor of the share of building sites nearby."""
        p = self._p
        noise = gaussian_filter(rng.standard_normal(grid.shape).astype(np.float32), p.noise_game_m / grid.cell_m / 2)
        noise = (noise - noise.min()) / max(float(np.ptp(noise)), 1e-6)
        share = np.max([suit.site_share[n] for n in spec.sites], axis=0)
        return 1 - p.noise + p.noise * noise, ramp(share, 0, p.site_full_share)

    def _regions(self, key: str, spec: ResourceSpec, s: FloatMap, grid: WorkGrid, yy: NDArray[Any],
                 xx: NDArray[Any]) -> NDArray[np.uint8]:
        p = self._p
        sm = gaussian_filter(s, spec.smooth_game_m / grid.cell_m / 2)
        if key == "fish":
            sm = np.where(grid.real_water, sm, 0)
        reg = np.zeros(grid.shape, np.uint8)
        cand = sm.copy()
        areas: list[float] = []
        r_max = (spec.radius_game_m if spec.radius_game_m is not None else p.region_radius_game_m) / grid.cell_m
        r_gap = (spec.spacing_game_m if spec.spacing_game_m is not None else p.region_spacing_game_m) / grid.cell_m
        peak = spec.peak if spec.peak is not None else p.region_peak_share
        while len(areas) < spec.regions and cand.max() > 0.05 * sm.max():
            py, px = np.unravel_index(np.argmax(cand), cand.shape)
            near = (yy - py) ** 2 + (xx - px) ** 2
            lab, _ = label((sm >= peak * sm[py, px]) & (near <= r_max ** 2) & (reg == 0))
            area = lab == lab[py, px]
            cand[near <= r_gap ** 2] = 0
            km2 = area.sum() * grid.cell_m * grid.cell_m / 1e6
            if km2 >= p.region_min_km2:
                reg[area] = len(areas) + 1
                areas.append(km2)
        self._log(f"  {spec.label:<10} {len(areas)} areas: " + ", ".join(f"{a:.0f}" for a in areas) + " km²")
        return reg


class _Suitability:
    """Suitability formulas per resource (real heights in m a.s.l.)."""

    def __init__(self, params: SiteParams, grid: WorkGrid, sites: dict[str, BoolMap], dist_town: NDArray[Any]) -> None:
        self._p = params
        self._grid = grid
        lc = grid.layers
        water_any = grid.water_any
        slope = grid.slope
        self.slope = slope
        self.dist_town = dist_town
        self.dist_water = distance_transform_edt(~water_any) * grid.cell_m
        self.dist_mount = distance_transform_edt(lc["mountains"] < 0.5) * grid.cell_m
        self.flat = ramp(slope, 0.15, 0.03)
        self.forest_l, self.meadow_l, self.crop_l = self.local(lc["forest"]), self.local(lc["meadow"]), \
            self.local(lc["cropland"])
        self.rock_l, self.mount_l = self.local(lc["rock"], 1500), self.local(lc["mountains"], 1500)
        self.open_land = np.clip(self.meadow_l + self.crop_l, 0, 1)
        self.dry = (~water_any) & (lc["glacier"] < 0.5)
        self.site_share = {n: self.local(a, params.site_near_game_m) for n, a in sites.items()}

    def local(self, a: NDArray[Any], radius_m: float | None = None) -> FloatMap:
        radius = self._p.local_game_m if radius_m is None else radius_m
        result: FloatMap = gaussian_filter(a.astype(np.float32), radius / self._grid.cell_m / 2)
        return result

    def of(self, key: str, height_m: tuple[float, float, float, float]) -> Any:
        """Suitability map 0..1 (numpy array); `height_m` = real height band (unused by fish)."""
        slope = self.slope
        height = band(self._grid.real_h, *height_m)
        if key == "farm":
            return self.crop_l * self.flat * height
        if key == "livestock":
            return self.meadow_l * ramp(slope, 0.3, 0.08) * height * (1 - 0.5 * self.crop_l)
        if key == "forest":
            return self.local(self._grid.layers["forest"], 800) * height
        if key == "plantation":
            return np.minimum(self.forest_l, self.open_land) * 2 * self.flat * height
        if key == "coal":
            return ((self.forest_l + self.meadow_l) * height * (1 - self.mount_l)
                    * ramp(slope, 0.4, 0.1))
        if key == "iron_ore":                  # mountains, remote
            return self.mount_l * height * ramp(self.dist_town, 2000, 6000)
        if key == "stone":
            return self.rock_l * height
        if key == "clay":
            return self.open_land * self.flat * height * ramp(self.dist_water, 4000, 500)
        if key == "sand":
            return self.flat * height * ramp(self.dist_water, 1500, 200)
        if key == "oil":                       # lowland in the north, far from the mountains
            return self.flat * height * ramp(self.dist_mount, 4000, 12000) * self.open_land
        if key == "fish":
            return self.local(self._grid.real_water, 400)
        raise KeyError(key)


class _Previews:
    """Hill-shaded background with water and glaciers; resource areas and site kinds on top; towns and a legend."""

    def __init__(self, grid: WorkGrid, width: int) -> None:
        self._grid = grid
        h, w = grid.shape
        self.pw = width
        self.ph = round(width * h / w)
        self.f = width / w
        gy2, gx2 = np.gradient(grid.game_h)
        shade = np.clip(0.75 + (gx2 - gy2) * 0.08, 0.35, 1.0)        # light from the north-west
        gray = (shade * 200).astype(np.uint8)
        bg = np.stack([gray] * 3, axis=2)
        bg[grid.water_any] = _WATER_COLOR
        bg[grid.layers["glacier"] > 0.5] = _GLACIER_COLOR
        self.background = bg
        self.font, self.font_b = preview_font(14), preview_font(15, bold=True)

    def finish(self, img: PilImage.Image, legend: Sequence[tuple[tuple[int, int, int], str]]) -> PilImage.Image:
        img = img.resize((self.pw, self.ph), PilImage.Resampling.NEAREST)
        d = ImageDraw.Draw(img)
        for t in self._grid.towns:
            x, y = t.u * self.f, t.v * self.f
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(220, 0, 0), outline=(255, 255, 255))
            d.text((x + 6, y - 8), t.name, fill=(0, 0, 0), font=self.font, stroke_width=2,
                   stroke_fill=(255, 255, 255))
        y = 10
        for color, text in legend:
            d.rectangle((10, y, 28, y + 14), fill=color, outline=(0, 0, 0))
            d.text((34, y - 1), text, fill=(0, 0, 0), font=self.font_b, stroke_width=2, stroke_fill=(255, 255, 255))
            y += 20
        return img

    def resources(self, resources: Mapping[str, ResourceSpec],
                  regions: Mapping[str, NDArray[np.uint8]]) -> PilImage.Image:
        res = self.background.astype(np.float32)
        edge_all = np.zeros(self._grid.shape, bool)
        for key, spec in resources.items():
            reg = regions[key] > 0
            res[reg] = res[reg] * 0.35 + np.array(spec.color, np.float32) * 0.65
            edge_all |= reg & binary_dilation(~reg, iterations=2)
        res[edge_all] *= 0.5
        return self.finish(PilImage.fromarray(res.astype(np.uint8)), [(s.color, s.label) for s in resources.values()])

    def sites(self, kinds: Mapping[str, SiteKind], sites: Mapping[str, BoolMap]) -> PilImage.Image:
        """Later kinds cover earlier ones: compact only where factory/field are not possible."""
        st = self.background.astype(np.float32)
        shown = np.zeros(self._grid.shape, bool)
        for name in ("field", "factory", "compact"):
            sel = sites[name] & ~shown
            st[sel] = st[sel] * 0.4 + np.array(kinds[name].color, np.float32) * 0.6
            shown |= sites[name]
        return self.finish(PilImage.fromarray(st.astype(np.uint8)), [(k.color, k.label) for k in kinds.values()])


# --- placement ------------------------------------------------------------------------------------------------------


@dataclass
class _Placed:
    name: str
    i: int
    j: int
    compact: bool
    steep: bool                                # no fitting site: the levelest place, check in game
    region: tuple[str, int] | None
    town: str | None
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    angle: float = 0.0


@dataclass
class _Plan:
    placed: list[_Placed]
    towns: list[TownSpot]
    rules: dict[str, Rule]
    producer: dict[str, str]
    town_need: dict[str, float] = field(default_factory=dict)


class Industries(NamedTuple):
    industries: IndustryList
    lua: TextDocument
    towns_lua: TextDocument
    preview: Image


@node("industries.plan")
def plan(grid: WorkGrid, maps: SiteMaps, rules: IndustryRules, places: OsmPoints, frame: MapFrame,
         params: IndustryParams, context: NodeContext) -> Industries:
    """Raw-material industries per resource area, processing industries near medium and large towns (by the cost of
    their inputs), demand per town, names after the nearest place. Outputs the industry list, the map editor's import
    files (towns and industries; towns with their demand only) and a preview."""
    settings = params.industries.effective()
    sp, pp = settings.sites, settings.plan
    towns = [copy.deepcopy(t) for t in grid.towns]     # the planner fills in classes and demand
    planner = _IndustryPlanner(pp, sp.resources, rules, grid, maps, sp.sites["factory"].radius_game_m,
                               sp.town_clear_game_m, grid.dist_town(), params.ignore_town_size, params.industry_density,
                               params.all_cargos, context.log)
    planned = planner.plan(towns)
    width_px, height_px = frame.width_px, frame.height_px
    namer = _Namer(settings.naming, width_px, height_px, sp.work_px, context.log)
    namer.name(planned, namer.places(places, MapProjection(frame).ring_to_uv))
    for q in planned.placed:
        q.angle = float(planner.placer.rng.choice(_ANGLES))
    files = _Files(settings.mod, frozenset(settings.extra_industries))
    field_types = frozenset(n for r in sp.resources.values() if "field" in r.sites for n in r.industries)
    data = [{"name": q.label, "type": q.name, "x": round(q.x, 1), "y": round(q.y, 1), "fileName": files.of(q)[0],
             "onWater": q.name in pp.on_water,
             "site": "compact" if q.compact else "field" if q.name in field_types else "factory"}
            for q in planned.placed]
    _log_balance(planned, planner, pp, context.log)
    preview = _preview(_Previews(grid, settings.preview_width), planned, settings.industry_colors,
                       settings.short_names)
    return Industries(IndustryList(data), TextDocument(_lua(planned, files, pp.on_water, True)),
                      TextDocument(_lua(planned, files, pp.on_water, False)), Image(np.asarray(preview)))


class _Placer:
    """Places the industries on the work grid: best place by preference among free cells; a tiny random value breaks
    ties; each industry claims a circle of spacing_game_m."""

    def __init__(self, params: PlanParams, grid: WorkGrid, factory_radius_game_m: float, away_from_towns: BoolMap,
                 log: Log) -> None:
        self._p = params
        self._grid = grid
        self._log = log
        h, w = grid.shape
        self.h, self.w = h, w
        self.rng = np.random.default_rng(params.seed)
        self.yy, self.xx = np.mgrid[0:h, 0:w]
        cell = grid.cell_m
        r_small = max(1, round(factory_radius_game_m / cell))
        yy0, xx0 = np.ogrid[-r_small:r_small + 1, -r_small:r_small + 1]
        disk = xx0 ** 2 + yy0 ** 2 <= r_small ** 2
        self.relief_small = maximum_filter(grid.game_h, footprint=disk) - minimum_filter(grid.game_h, footprint=disk)
        edge = round(params.map_edge_game_m / cell)            # the footprint must lie fully on the map
        self.inner = np.zeros((h, w), bool)
        self.inner[edge:-edge, edge:-edge] = True
        self.free = ~grid.blocked & away_from_towns & self.inner
        self.on_water_ok = grid.real_water & self.inner        # water industries (fishing grounds)
        self.taken = np.zeros((h, w), bool)
        self.placed: list[_Placed] = []

    def place(self, name: str, cand: BoolMap, pref: NDArray[Any], fallback: BoolMap | None = None,
              compact: bool = False, region: tuple[str, int] | None = None,
              town: str | None = None) -> _Placed | None:
        """Site from cand with the largest pref; else the levelest place in fallback (steep)."""
        ok = cand & (self.free | self.on_water_ok) & ~self.taken
        steep = False
        if not ok.any():
            if fallback is None or not (fallback & self.free & ~self.taken).any():
                self._log(f"  no place for {name}")
                return None
            ok = fallback & self.free & ~self.taken
            pref = -self.relief_small
            steep = True
        p = np.where(ok, pref + self.rng.random((self.h, self.w)) * 1e-3, -np.inf)
        i, j = np.unravel_index(np.argmax(p), p.shape)
        self.placed.append(_Placed(name, int(i), int(j), compact, steep, region, town))
        self._claim(int(i), int(j), self._p.spacing_game_m)
        return self.placed[-1]

    def _claim(self, i: int, j: int, radius_m: float) -> None:
        r = round(radius_m / self._grid.cell_m)
        rows, cols = slice(max(0, i - r), i + r + 1), slice(max(0, j - r), j + r + 1)
        self.taken[rows, cols] |= (self.yy[rows, cols] - i) ** 2 + (self.xx[rows, cols] - j) ** 2 <= r * r

    def near_point(self, i: int, j: int, max_m: float) -> BoolMap:
        return np.asarray((self.yy - i) ** 2 + (self.xx - j) ** 2 <= (max_m / self._grid.cell_m) ** 2)

    def dist_to(self, i: int, j: int) -> NDArray[Any]:
        result: NDArray[Any] = np.sqrt((self.yy - i) ** 2 + (self.xx - j) ** 2) * self._grid.cell_m
        return result


class _IndustryPlanner:
    """Raw materials, specialisation of the towns, foreign trade, demand."""

    def __init__(self, params: PlanParams, resources: Mapping[str, ResourceSpec], rules: IndustryRules,
                 grid: WorkGrid, maps: SiteMaps, factory_radius_game_m: float, town_clear_game_m: float,
                 dist_town: NDArray[Any], equal_towns: bool, density: float, all_cargos: bool, log: Log) -> None:
        self._p = params
        self._equal_towns = equal_towns
        self._density = density
        self._all_cargos = all_cargos
        self._resources = resources
        self._grid = grid
        self._maps = maps
        self._dist_town = dist_town
        self._log = log
        names = sorted(set(params.raw_resource) | set(params.intermediate) | set(params.complex))
        self.rules = {n: rules.rule(n) for n in names}
        self._input_choices = {n: rules.input_choices(n) for n in names}
        self.producer: dict[str, str] = {}
        for n in names:
            for g in self.rules[n][1]:
                self.producer.setdefault(g, n)
        self.producer.update(params.producer_pref)
        self.placer = _Placer(params, grid, factory_radius_game_m, dist_town >= town_clear_game_m, log)
        self._mountain = gaussian_filter(grid.layers["mountains"].astype(np.float32), 1000 / grid.cell_m / 2) > 0.3

    def plan(self, towns: list[TownSpot]) -> _Plan:
        self._raw_materials()
        self._specialise(towns)
        self._trade_points()
        need = self._demand(towns)
        return _Plan(self.placer.placed, towns, self.rules, self.producer, need)

    def rate_out(self, n: str, g: str) -> float:
        _, outs, dur = self.rules[n]
        return outs[g] / dur

    def rate_in(self, n: str, g: str) -> float:
        ins, _, dur = self.rules[n]
        return ins[g] / dur

    def _raw_materials(self) -> None:
        """One industry per area (agriculture all areas, else at most max_per_type x density); none whose outputs no
        planned industry and no town uses (temperate standard: stone only feeds the cement plant of the mod)."""
        p, placer, maps, grid = self._p, self.placer, self._maps, self._grid
        cell = grid.cell_m
        used = {g for choices in self._input_choices.values() for ins in choices for g in ins} | set(p.town_cargos)
        unused = [n for n in p.raw_resource if not set(self.rules[n][1]) & used]
        if unused:
            self._log("  output used by nothing on the map, not placed: " + ", ".join(unused))
        for n, key in p.raw_resource.items():
            if n in unused:
                continue
            reg = maps.regions[key]
            nreg = int(reg.max())
            total = nreg if n in p.agriculture else min(nreg, self._scaled(p.max_per_type))
            if total == 0:
                continue
            area = np.bincount(reg.ravel(), minlength=nreg + 1)[1:].astype(float)
            alloc = np.zeros(nreg, int)
            for _ in range(total):
                alloc[np.argmax(np.sqrt(area) / (alloc + 1))] += 1
            for r_i in range(nreg):
                inside = reg == r_i + 1
                for _ in range(alloc[r_i]):
                    if key == "fish":                  # on real water, away from the shore
                        cand = inside & grid.real_water & (distance_transform_edt(grid.real_water) * cell
                                                            > p.fish_shore_game_m)
                        placer.place(n, cand, maps.score[key], region=(key, r_i + 1))
                        continue
                    in_mountains = self._mountain[inside].mean() > p.compact_mountain_share
                    compact = n in p.compact and (in_mountains or (inside & maps.sites["field"]).mean()
                                                  < p.compact_field_share * inside.mean())
                    cls = maps.sites["compact"] if compact else maps.sites["field"]
                    near = binary_dilation(inside, iterations=round(p.region_edge_game_m / cell))
                    town_f = ramp(self._dist_town, *(p.agri_town_game_m if n in p.agriculture else p.raw_town_game_m))
                    placer.place(n, near & cls & placer.free,
                                 (maps.score[key] * inside + 0.3 * maps.score[key]) * (0.3 + 0.7 * town_f),
                                 fallback=near, compact=compact, region=(key, r_i + 1))
        self._log("raw materials: " + ", ".join(f"{n} {sum(q.name == n for q in placer.placed)}"
                                                for n in p.raw_resource))

    def _scaled(self, count: int) -> int:
        """`count` x industry density, rounded, at least 1."""
        return max(1, round(count * self._density))

    def _sources(self, g: str) -> list[tuple[int, int]]:
        """Cells where cargo g is produced (placed industries)."""
        return [(q.i, q.j) for q in self.placer.placed if q.name in self.rules and g in self.rules[q.name][1]]

    def _town_cost(self, t: TownSpot, n: str) -> float:
        """Sum of distances (game metres) from the town to the nearest source per input (none: foreign trade)."""
        ti, tj = t.cell(*self._grid.shape)
        cell = self._grid.cell_m
        return sum(min((math.hypot(i - ti, j - tj) * cell for i, j in self._sources(g)),
                       default=self._p.import_cost_game_m) for g in self.rules[n][0])

    def _specialise(self, towns: list[TownSpot]) -> None:
        """Medium towns intermediate products, large towns complex products (factory near the town)."""
        p, placer, maps = self._p, self.placer, self._maps
        for t in towns:
            if self._equal_towns:
                t.cls, t.slots = "medium", p.equal_town_slots
            else:
                t.cls = ("large" if t.capacity >= p.large_town_capacity
                         else "medium" if t.capacity >= p.medium_town_capacity else "village")
                t.slots = p.specializations[t.cls]
            t.produces, t.industries = set(), []
        depth = dict.fromkeys(p.raw_resource, 0)
        for _ in range(10):                            # processing level = 1 + deepest level of the inputs
            for n in [*p.intermediate, *p.complex]:
                depth[n] = 1 + max((depth.get(self.producer.get(g, ""), 0) for g in self.rules[n][0]), default=0)
        skip_unsupplied = not self._all_cargos and p.trade_points == 0
        unsupplied = []
        for n in sorted([*p.intermediate, *p.complex], key=lambda n: (depth[n], n)):
            supplied = [ins for ins in self._input_choices[n] if all(self._sources(g) for g in ins)]
            if supplied:                               # the first rule whose inputs all have a source on the map
                _, outs, duration = self.rules[n]
                self.rules[n] = supplied[0], outs, duration
            elif skip_unsupplied:
                unsupplied.append(n)                   # upstream first: a skipped type starves its customers too
                continue
            want = ["medium", "large"] if n in p.intermediate else ["large", "medium"]
            for _ in range(self._scaled(p.production_per_type.get(n, 1))):
                cand: list[TownSpot] = []
                for c in want:                         # preferred size class, else the other
                    cand = [t for t in towns if t.cls == c and t.slots > 0 and n not in t.industries]
                    if cand:
                        break
                if not cand:
                    self._log(f"  no town free for {n}")
                    continue
                t = min(cand, key=lambda t: self._town_cost(t, n))
                ci, cj = t.cell(*self._grid.shape)
                ring = placer.near_point(ci, cj, p.factory_town_game_m)
                q = placer.place(n, ring & maps.sites["factory"] & placer.free, -placer.dist_to(ci, cj), fallback=ring,
                                 town=t.name)
                if q:
                    t.slots -= 1
                    t.industries.append(n)
                    t.produces |= set(self.rules[n][1])
        if unsupplied:
            self._log("  no source for the inputs, not placed: " + ", ".join(unsupplied))

    def _trade_points(self) -> None:
        """Foreign trade at the map edge: low places (valley exits), spread out."""
        p, placer = self._p, self.placer
        edge_band = placer.inner & ~binary_erosion(placer.inner, iterations=round(p.trade_edge_game_m
                                                                                  / self._grid.cell_m))
        trade: list[tuple[int, int]] = []
        for _ in range(p.trade_points):
            spread = (np.min([placer.dist_to(i, j) for i, j in trade], axis=0) if trade
                      else np.zeros(self._grid.shape))
            q = placer.place(_TRADE, edge_band & self._maps.sites["factory"] & placer.free,
                             -self._grid.real_h + spread / 1000 * p.trade_spread_m_per_km, fallback=edge_band)
            if q:
                trade.append((q.i, q.j))

    def _demand(self, towns: list[TownSpot]) -> dict[str, float]:
        """Per town one commercial and one industrial cargo, not from its own production (except own_allowed)."""
        p = self._p
        cell = self._grid.cell_m
        cargo_use = {g: u for g, (_, u) in p.town_cargos.items()}
        prod_cells = {g: self._sources(g) for g in p.town_cargos}
        count = dict.fromkeys(p.town_cargos, 0)
        for k, t in enumerate(sorted(towns, key=lambda t: -t.capacity)):
            ti, tj = t.cell(*self._grid.shape)
            t.needs = {}
            far_use = None
            if self._equal_towns or t.capacity >= p.far_min_capacity:   # one long-distance cargo, com/ind in turns
                far_use = "com" if k % 2 == 0 else "ind"

            def dist_g(g: str, ti: int = ti, tj: int = tj) -> float:
                return min((math.hypot(i - ti, j - tj) * cell for i, j in prod_cells[g]),
                           default=p.import_cost_game_m)

            for use in ("com", "ind"):
                far = use == far_use
                opts = [g for g in p.town_cargos if cargo_use[g] == use and (g in p.far_cargos) == far
                        and (g not in t.produces or g in p.own_allowed)
                        and (g not in p.local_only or dist_g(g) <= p.local_max_game_m)]
                made = [g for g in opts if prod_cells[g]]   # produced on the map preferred (else foreign trade)
                opts = made or opts
                if not opts:
                    continue
                if far:
                    g = min(opts, key=lambda g: (count[g], -dist_g(g)))
                else:
                    g = min(opts, key=lambda g: dist_g(g) + p.balance_game_m * count[g] / max(1, len(prod_cells[g])))
                t.needs[use] = g
                count[g] += 1
        self._log("demand of the towns: " + ", ".join(f"{g} {c}" for g, c in sorted(count.items(),
                                                                                   key=lambda x: -x[1]) if c))
        return {g: c / (p.towns_per_far_producer if g in p.far_cargos else p.towns_per_local_producer)
                * self.rate_out(self.producer[g], g) for g, c in count.items() if c}


class _Namer:
    """"<place> <type>" after the nearest OSM place (only places on this map), numbered when repeated."""

    def __init__(self, params: NamingParams, width_px: int, height_px: int, work_px: int, log: Log) -> None:
        self._p = params
        self._width_px = width_px
        self._height_px = height_px
        self._work_px = work_px
        self._log = log

    def places(self, points: OsmPoints, to_uv: Callable[[Sequence[tuple[float, float]]], list[tuple[float, float]]]
               ) -> dict[str, tuple[float, float, float]]:
        els = [e for e in points.points if e.tags.get("place") in self._p.place_types and e.tags.get("name")]
        out = {}
        if els:
            w1, h1 = (self._width_px - 1) * GAME_METERS_PER_PIXEL, (self._height_px - 1) * GAME_METERS_PER_PIXEL
            for e, (u, v) in zip(els, to_uv([(e.lon, e.lat) for e in els]), strict=True):
                if 0 <= u <= 1 and 0 <= v <= 1:
                    out[e.tags["name"]] = ((u - 0.5) * w1, (0.5 - v) * h1, self._p.place_types[e.tags["place"]])
        self._log(f"places for names: {len(out)}")
        return out

    def game_xy(self, i: int, j: int) -> tuple[float, float]:
        px = GAME_METERS_PER_PIXEL
        return (((j + 0.5) * self._work_px - (self._width_px - 1) / 2) * px,
                ((self._height_px - 1) / 2 - (i + 0.5) * self._work_px) * px)

    def name(self, planned: _Plan, places: Mapping[str, tuple[float, float, float]]) -> None:
        used: dict[str, int] = {}
        for q in planned.placed:
            x, y = self.game_xy(q.i, q.j)
            name, dmin = None, self._p.max_game_m
            for nm, (a, b, fac) in places.items():
                dd = math.hypot(a - x, b - y) * fac
                if dd < dmin:
                    name, dmin = nm, dd
            if name is None:
                name = min(planned.towns, key=lambda t: math.hypot(t.x - x, t.y - y)).name
            label_ = f"{name} {self._p.type_names.get(q.name, q.name)}"
            used[label_] = used.get(label_, 0) + 1
            q.label = label_ + (f" {used[label_]}" if used[label_] > 1 else "")
            q.x, q.y = x, y


class _Files:
    """File names of the buildings: game industries, or the mod's compact/extra/trade buildings."""

    def __init__(self, mod: ModFiles, extra_industries: frozenset[str] = frozenset()) -> None:
        self._mod = mod
        self._extra = extra_industries

    def of(self, q: _Placed) -> tuple[str, str]:
        """(fileName, tag)."""
        n, m = q.name, self._mod
        if q.compact:
            return f"{m.mod_id}::/{m.folder}/dhe_{n}_compact.con", ""
        if n == _TRADE:
            return f"{m.trade_mod_id}::/{m.trade_folder}/dhe_foreign_trade.con", ""
        if n in self._extra:
            return f"{m.mod_id}::/{m.folder}/dhe_{n}.con", n
        return f"::/industries/{n}/{n}.con", n


def _lua(planned: _Plan, files: _Files, on_water: frozenset[str], with_industries: bool) -> str:
    """Editor import (towns/industries) in the format of the editor's export."""
    lines = ["function data()", "return {", "\tindustries = {"]
    for q in planned.placed if with_industries else []:
        fn, tag = files.of(q)
        lines += ["\t\t{", f"\t\t\tangle = {q.angle:g},", f"\t\t\tfileName = {lua_string(fn)},",
                  f"\t\t\tonWater = {'true' if q.name in on_water else 'false'},",
                  f"\t\t\tposition = {{ x = {q.x:.1f}, y = {q.y:.1f}, }},", f"\t\t\ttag = {lua_string(tag)},",
                  "\t\t},"]
    lines += ["\t},", "\ttowns = {"]
    for t in planned.towns:
        lines += ["\t\t{", "\t\t\tlandUse2CargoNeedsCategories = {"]
        for use in ("com", "ind"):
            if use in t.needs:
                lines.append(f"\t\t\t\t{use} = {{ {{ {lua_string(t.needs[use])}, 1, }}, }},")
        lines += ["\t\t\t},", f"\t\t\tname = {lua_string(t.name)},",
                  f"\t\t\tposition = {{ x = {t.x:.1f}, y = {t.y:.1f}, }},",
                  "\t\t\tsizeFactors = { " + ", ".join(f"{v:g}" for v in t.size) + ", },", "\t\t},"]
    lines += ["\t},", "}", "end", ""]
    return "\n".join(lines)


def _log_balance(planned: _Plan, planner: _IndustryPlanner, params: PlanParams, log: Log) -> None:
    """Supply (placed industries) and use (factories + towns) per cargo, units per 1000 s."""
    supply: dict[str, float] = {}
    use = dict(planned.town_need)
    built = _built(planned)
    for n, c in built.items():
        if n not in planned.rules:                     # foreign trade
            continue
        for g in planned.rules[n][1]:
            supply[g] = supply.get(g, 0) + c * planner.rate_out(n, g)
        for g in planned.rules[n][0]:
            use[g] = use.get(g, 0) + c * planner.rate_in(n, g)
    for g in sorted(set(supply) | set(use)):
        a, b = supply.get(g, 0) * 1000, use.get(g, 0) * 1000
        flag = "" if b == 0 or a >= b * 0.95 else "  <- too little"
        log(f"{g:<12} supply {a:8.1f} demand {b:8.1f}{flag}")
    log(f"{len(planned.placed)} industries ({sum(q.compact for q in planned.placed)} compact, "
        f"{sum(q.steep for q in planned.placed)} without a fitting site = levelest place, check in game)")


def _built(planned: _Plan) -> dict[str, int]:
    built: dict[str, int] = {}
    for q in planned.placed:
        built[q.name] = built.get(q.name, 0) + 1
    return built


def _preview(pv: _Previews, planned: _Plan, colors: Mapping[str, tuple[int, int, int]],
             short: Mapping[str, str]) -> PilImage.Image:
    """Short name per industry (red frame = steep fallback), towns with their demand, legend."""
    img = PilImage.fromarray(pv.background.astype(np.uint8)).resize((pv.pw, pv.ph), PilImage.Resampling.NEAREST)
    d = ImageDraw.Draw(img)
    f, font, font_b = pv.f, pv.font, pv.font_b
    for q in planned.placed:
        x, y = (q.j + 0.5) * f, (q.i + 0.5) * f
        col = colors.get(q.name, (255, 255, 255))
        d.rectangle((x - 7, y - 7, x + 7, y + 7), fill=col, outline=(255, 0, 0) if q.steep else (0, 0, 0),
                    width=3 if q.steep else 1)
        d.text((x + 9, y - 8), short.get(q.name, q.name[:3]) + ("·k" if q.compact else ""), fill=(0, 0, 0),
               font=font, stroke_width=2, stroke_fill=(255, 255, 255))
    for t in planned.towns:
        x, y = t.u * f, t.v * f
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(220, 0, 0), outline=(255, 255, 255))
        d.text((x + 7, y - 8), f"{t.name} ({t.needs.get('com', '-')}, {t.needs.get('ind', '-')})", fill=(120, 0, 0),
               font=font, stroke_width=2, stroke_fill=(255, 255, 255))
    yl = 10
    built = _built(planned)
    for n in sorted(built):
        d.rectangle((10, yl, 24, yl + 14), fill=colors.get(n, (255, 255, 255)), outline=(0, 0, 0))
        d.text((30, yl - 1), f"{short.get(n, n[:3])} = {n} ({built[n]})", fill=(0, 0, 0), font=font_b,
               stroke_width=2, stroke_fill=(255, 255, 255))
        yl += 18
    return img
