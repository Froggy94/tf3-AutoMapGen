"""Transition between two looks in the climate's trees: the rising share over the zone (import tree) and which look
paints where (material tree): share = mask x factor(surface, biome), turned into the weights of the two looks."""

from __future__ import annotations

from dataclasses import dataclass

from automapgen.lua import Port
from automapgen.nodes.climate.adapters import ClimateAdapter
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.preset import (
    BiomeFactor,
    NoiseSpec,
    OverlayMix,
    PatchMix,
    SoftMix,
    Transition,
)


class ZoneRamp:
    """ramp = area x clamp(distance to the start line / length).

    `distance_map` is only known from the base game (coast: distance 40..250 from the water line), so two things are
    assumptions: the unit is game metres, and which side of the threshold it measures. The distance is therefore taken
    from both sides (the area and its inverse) and the larger one used; outside the area the ramp is 0 either way.
    """

    def __init__(self, factory: NodeFactory, top: float) -> None:
        self._f = factory
        self._top = top

    def build(self, name: str, layer: Port, length_game_m: float) -> Port:
        f = self._f
        area = f.mask(f"{name} area", layer, 0.5, self._top)
        outside = f.invert(f"{name} outside", area)
        distance = f.maximum(f"{name} distance", f.distance(f"{name} distance inside", area, 0.5),
                             f.distance(f"{name} distance outside", outside, 0.5))
        rise = f.clamp(f"{name} rise", distance, (0, max(length_game_m, 1)), (0, 1))
        return f.multiply(name, area, rise)


@dataclass(frozen=True)
class MixParts:
    """Weights of the two looks on a surface. `primary` None: the primary look stays complete and the secondary look
    is painted on top of it with the weight `secondary`."""

    primary: Port | None
    secondary: Port

    @property
    def on_top(self) -> bool:
        return self.primary is None


class TransitionMixer:
    """Weights of the primary and the secondary look for the surfaces of a transition."""

    def __init__(self, factory: NodeFactory, adapter: ClimateAdapter, transition: Transition, mask: Port,
                 seed: Port) -> None:
        self._f = factory
        self._adapter = adapter
        self._transition = transition
        self._mask = mask
        self._seed = seed
        self._noise: dict[NoiseSpec, Port] = {}

    def applies_to(self, surface_key: str) -> bool:
        return surface_key in self._transition.surfaces

    def parts(self, surface_key: str) -> MixParts:
        surface = self._transition.surfaces[surface_key]
        name = f"{surface_key} transition"
        share = self._f.multiply(f"{name} share", self._mask, self._factor(f"{name} factor", surface.factor))
        mix = surface.mix or self._transition.mix
        if isinstance(mix, OverlayMix):
            return MixParts(None, self._strength(name, share, mix))
        choice = self._choose(name, share, mix)
        return MixParts(self._f.invert(f"{name} not secondary", choice), choice)

    def _factor(self, name: str, factor: BiomeFactor) -> Port:
        """default + sum over biomes (factor_b - default) x biome_mask_b."""
        f = self._f
        result = f.constant(name, factor.default)
        for biome, value in sorted(factor.by_biome.items()):
            delta = f.multiply(f"{name} biome {biome}", self._adapter.biome_mask(biome),
                               f.constant(f"{name} biome {biome} delta", value - factor.default))
            result = f.add(f"{name} with biome {biome}", result, delta)
        return result

    def _strength(self, name: str, share: Port, mix: OverlayMix) -> Port:
        """share x (1 -/+ variation), varied by noise (window -0.5..0.5 as for the patch intensity), at most 1."""
        f = self._f
        variation = f.clamp(f"{name} variation", self._noise_of(mix.noise), (-0.5, 0.5),
                            (1 - mix.variation, 1 + mix.variation))
        return f.clamp(f"{name} strength", f.multiply(f"{name} varied", share, variation), (0, 1), (0, 1))

    def _choose(self, name: str, share: Port, mix: PatchMix | SoftMix) -> Port:
        """Choice map (0 = primary, 1 = secondary). Patches: secondary where noise > 1 - 2 x share - the same share
        convention as the patch layers (noise assumed roughly even in -1..1, unverified), so a share of 0.3 covers
        about 30 % of the area."""
        if isinstance(mix, SoftMix):
            return share
        f = self._f
        shift = f.add(f"{name} shift", f.multiply(f"{name} double", share, f.constant(f"{name} two", 2)),
                      f.constant(f"{name} minus one", -1))
        margin = f.add(f"{name} margin", self._noise_of(mix.noise), shift)   # noise - (1 - 2 x share)
        if mix.edge <= 0:
            return f.greater(f"{name} choice", margin, f.constant(f"{name} zero", 0))
        return f.clamp(f"{name} choice", margin, (-mix.edge / 2, mix.edge / 2), (0, 1))

    def _noise_of(self, spec: NoiseSpec) -> Port:
        """One noise per specification, shared by all surfaces (secondary patches of grass and rock coincide)."""
        if spec not in self._noise:
            self._noise[spec] = self._f.noise(self._f.unique("transition noise"), self._seed, spec)
        return self._noise[spec]
