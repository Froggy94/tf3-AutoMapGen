"""Turns surface areas of the climate preset into coverage ports of the material tree."""

from __future__ import annotations

from collections.abc import Mapping

from automapgen.lua import Port
from automapgen.nodes.climate.adapters import ClimateAdapter
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.preset import AreaProduct, GameArea, LayerArea, SteepWaterArea

HARD_EDGE = (0.499, 0.501)
"""Cut of an interpolated 0/1 layer mask at 0.5 for a sharp edge."""

type AnyArea = LayerArea | GameArea | AreaProduct | SteepWaterArea


class AreaBuilder:
    """Builds each distinct area once (later uses share the same nodes)."""

    def __init__(self, factory: NodeFactory, adapter: ClimateAdapter, layers: Mapping[str, Port],
                 game_areas: Mapping[str, Port]) -> None:
        """`game_areas`: game areas computed by the builder itself (e.g. the extended rock)."""
        self._f = factory
        self._adapter = adapter
        self._layers = layers
        self._game_areas = game_areas
        self._built: dict[AnyArea, Port] = {}

    def port(self, area: AnyArea, name: str) -> Port:
        if area not in self._built:
            self._built[area] = self._build(area, name)
        return self._built[area]

    def _build(self, area: AnyArea, name: str) -> Port:
        f = self._f
        match area:
            case LayerArea():
                layer = self._layers[area.key]
                return f.clamp(f.unique(f"{area.key} hard"), layer, HARD_EDGE, (0, 1)) if area.hard else layer
            case GameArea():
                if area.name in self._game_areas:
                    return self._game_areas[area.name]
                return self._adapter.game_area(area.name, f)
            case AreaProduct():
                return f.multiply(f.unique(name), self.port(area.first, f"{name} a"),
                                  self.port(area.second, f"{name} b"))
            case SteepWaterArea():
                return self._steep_water(area, name)

    def _steep_water(self, area: SteepWaterArea, name: str) -> Port:
        """Steepness of the water surface itself (slopes at its edge do not count), blurred for a soft transition."""
        f = self._f
        water = self.port(area.water, f"{name} water")
        steep = f.clamp(f.unique(f"{name} steep"), self._adapter.slope, (area.steepness.start, area.steepness.full),
                        (0, 1))
        if area.edge_sigma > 0:
            blurred = f.blur(f.unique(f"{name} water blur"), water, area.edge_sigma, area.edge_size)
            inner = f.clamp(f.unique(f"{name} inner"), blurred, (area.edge_inner - 0.05, area.edge_inner), (0, 1))
            steep = f.multiply(f.unique(f"{name} steep inner"), steep, inner)
        soft = f.blur(f.unique(f"{name} soft"), steep, area.blur_sigma, area.blur_size)
        gained = f.clamp(f.unique(f"{name} gain"), soft, (0, 1 / area.gain), (0, 1))
        return f.multiply(f.unique(name), water, gained)
