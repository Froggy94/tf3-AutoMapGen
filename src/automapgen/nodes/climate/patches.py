"""Noise patches on an area: the building block of meadow, alpine, shore, riverbed and field patterns."""

from __future__ import annotations

from automapgen.lua import Port
from automapgen.nodes.climate.factory import NodeFactory
from automapgen.nodes.climate.preset import ConstantIntensity, PatchSpec


class PatchRule:
    """Coverage = area x patch window (x varying intensity), optionally limited to gentle slopes."""

    def __init__(self, factory: NodeFactory, seed: Port, slope: Port) -> None:
        self._factory = factory
        self._seed = seed
        self._slope = slope

    def coverage(self, name: str, area: Port, spec: PatchSpec) -> Port:
        f = self._factory
        if spec.slope_limit is not None:
            gentle = f.clamp(f"{name} gentle", self._slope, spec.slope_limit.steepness(), (1, 0))
            area = f.multiply(f"{name} area", area, gentle)
        threshold = 1 - 2 * spec.share
        window = (threshold + spec.center_offset - spec.edge / 2, threshold + spec.center_offset + spec.edge / 2)
        noise = f.noise(f"{name} noise", self._seed, spec.noise)
        if isinstance(spec.intensity, ConstantIntensity):
            pattern = f.clamp(f"{name} share", noise, window, (0, spec.intensity.value))
        else:
            share = f.clamp(f"{name} share", noise, window, (0, 1))
            varying = spec.intensity
            intensity_noise = f.noise(f"{name} intensity noise", self._seed, varying.noise)
            intensity = f.clamp(f"{name} intensity", intensity_noise, (-0.5, 0.5), (varying.low, varying.high))
            pattern = f.multiply(f"{name} pattern", share, intensity)
        return f.multiply(name, area, pattern)
