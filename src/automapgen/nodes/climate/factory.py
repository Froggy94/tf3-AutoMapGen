"""Creates nodes of the game's generator trees by meaning (mask, clamp, multiply, noise, ...) with automatic layout."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from automapgen.lua import NodeTree, ParamValue, Port, TreeNode
from automapgen.nodes.climate.preset import NoiseSpec

_ROWS_PER_COLUMN = 40
_ROW_STEP = 0.004
_COLUMN_STEP = 0.02


class NodeFactory:
    """Adds own nodes (names prefixed "lc ") to a node tree, laid out in columns starting at `origin`."""

    PREFIX = "lc "

    def __init__(self, graph: NodeTree, origin: tuple[float, float]) -> None:
        self._graph = graph
        self._origin = origin
        self._count = 0

    @property
    def graph(self) -> NodeTree:
        return self._graph

    def node(self, name: str, layer_type: str, inputs: Mapping[str, Port] | None = None,
             params: Mapping[str, ParamValue] | None = None) -> Port:
        column, row = divmod(self._count, _ROWS_PER_COLUMN)
        self._count += 1
        position = (round(self._origin[0] + column * _COLUMN_STEP, 4), round(self._origin[1] - row * _ROW_STEP, 4))
        node = TreeNode.create(self.PREFIX + name, layer_type, inputs, params, position)
        self._graph.add(node)
        return Port(node.name)

    def unique(self, name: str) -> str:
        """`name`, or `name #2`, `#3`, ... if a node with that name exists already (names are cosmetic)."""
        candidate, number = name, 1
        while self.PREFIX + candidate in self._graph:
            number += 1
            candidate = f"{name} #{number}"
        return candidate

    # --- sources -------------------------------------------------------------------------------------------------

    def data(self, name: str) -> Port:
        """input_data: provides the random seed ("seed")."""
        return self.node(name, "input_data")

    def constant(self, name: str, value: float) -> Port:
        return self.node(name, "constant_map", params={"value": value})

    def point_cloud(self, name: str, points: Sequence[tuple[float, float]]) -> Port:
        return self.node(name, "constant_pointcloud", params={"values": points})

    def noise(self, name: str, seed: Port, spec: NoiseSpec) -> Port:
        return self.node(name, "fractal_noise_map", {"seed": seed},
                         {"frequency": spec.frequency, "gain": spec.gain, "lacunarity": spec.lacunarity,
                          "numOctaves": spec.octaves})

    def dither(self, name: str, seed: Port) -> Port:
        return self.node(name, "dithering_noise_map", {"seed": seed})

    # --- operations ----------------------------------------------------------------------------------------------

    def mask(self, name: str, source: Port, low: float, high: float) -> Port:
        """1 where low <= value < high, else 0."""
        return self.node(name, "mask_map", {"in1": source},
                         {"interval_x": low, "interval_y": high, "lowerIncluded": True, "upperIncluded": False})

    def distance(self, name: str, source: Port, threshold: float) -> Port:
        """Distance between the cells above and those at/below `threshold` (game metres - assumption, see ZoneRamp)."""
        return self.node(name, "distance_map", {"in1": source}, {"threshold": threshold})

    def clamp(self, name: str, source: Port, source_range: tuple[float, float],
              target_range: tuple[float, float]) -> Port:
        """Linear map of source_range onto target_range, clamped."""
        return self.node(name, "map_clamp_map", {"in1": source},
                         {"clamp": True, "from_x": source_range[0], "from_y": source_range[1],
                          "to_x": target_range[0], "to_y": target_range[1]})

    def invert(self, name: str, source: Port) -> Port:
        return self.clamp(name, source, (0, 1), (1, 0))

    def multiply(self, name: str, a: Port, b: Port) -> Port:
        return self.node(name, "mul_map", {"in1": a, "in2": b})

    def add(self, name: str, a: Port, b: Port) -> Port:
        return self.node(name, "add_map", {"in1": a, "in2": b})

    def maximum(self, name: str, a: Port, b: Port) -> Port:
        return self.node(name, "compare_map", {"in1": a, "in2": b}, {"op": "MAX"})

    def greater(self, name: str, a: Port, b: Port) -> Port:
        """1 where a > b."""
        return self.node(name, "compare_map", {"in1": a, "in2": b}, {"op": "GREATER"})

    def select(self, name: str, condition: Port, if_greater: Port, otherwise: Port, threshold: float) -> Port:
        """`if_greater` where condition > threshold, else `otherwise`."""
        return self.node(name, "combine_map", {"compare": condition, "in1": if_greater, "in2": otherwise},
                         {"op": "Greater", "value": threshold})

    def blur(self, name: str, source: Port, sigma: float, size: int) -> Port:
        return self.node(name, "blur_map", {"in1": source}, {"sigma": sigma, "size": size})

    def piecewise_linear(self, name: str, source: Port, steps: Port) -> Port:
        return self.node(name, "pwlerp_map", {"in1": source, "steps": steps})
