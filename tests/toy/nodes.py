"""Toy data type and nodes: lists of numbers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from automapgen.graph import NodeContext, NodeParams, data_type, node, param


class NumbersCodec:
    suffix = ".json"

    def save(self, value: Numbers, path: Path) -> None:
        path.write_text(json.dumps(list(value.values)), encoding="utf-8")

    def load(self, path: Path) -> Numbers:
        return Numbers(tuple(json.loads(path.read_text(encoding="utf-8"))))


@data_type("numbers", "A list of numbers.", codec=NumbersCodec(), preview=lambda n: str(list(n.values)),
           fingerprint=lambda n: json.dumps(list(n.values)))
@dataclass(frozen=True)
class Numbers:
    values: tuple[float, ...]


class ConstantParams(NodeParams):
    value: float = param(1.0, unit="m")
    """The value of every element."""
    count: int = param(3, ge=1)
    """Number of elements."""


@node("toy.constant", output="numbers")
def constant(params: ConstantParams) -> Numbers:
    """A list with `count` times `value`."""
    return Numbers((params.value,) * params.count)


class ScaleParams(NodeParams):
    factor: float = 2.0
    """Multiplier."""


@node("toy.scale", output="numbers", version=2, migrations={1: lambda p: {"factor": p.get("times", 2.0)}})
def scale(numbers: Numbers, params: ScaleParams) -> Numbers:
    """Every element times the factor (version 1 called the parameter `times`)."""
    return Numbers(tuple(v * params.factor for v in numbers.values))


@node("toy.add", output="numbers")
def add(a: Numbers, b: Numbers) -> Numbers:
    """Element-wise sum of two lists."""
    return Numbers(tuple(x + y for x, y in zip(a.values, b.values, strict=True)))


@node("toy.sum", output="total")
def total(numbers: tuple[Numbers, ...], context: NodeContext) -> float:
    """Sum over all elements of all linked lists."""
    context.log(f"{len(numbers)} lists")
    context.check_cancelled()
    return float(sum(sum(n.values) for n in numbers))


@node("toy.offset", output="numbers")
def offset(numbers: Numbers, shift: Numbers | None = None) -> Numbers:
    """Adds `shift` if linked."""
    return numbers if shift is None else add(numbers, shift)


class Halves(NamedTuple):
    first: Numbers
    second: Numbers


@node("toy.split")
def split(numbers: Numbers) -> Halves:
    """Splits a list into two halves."""
    middle = len(numbers.values) // 2
    return Halves(Numbers(numbers.values[:middle]), Numbers(numbers.values[middle:]))


class Box(NodeParams):
    width: float = 1.0
    """Width."""
    height: float = 1.0
    """Height."""


class BoxParams(NodeParams):
    box: Box = Box()
    """The box."""
    label: str = ""
    """Name of the box."""


@node("toy.box", output="numbers")
def box(params: BoxParams) -> Numbers:
    """Width and height of the box (a parameter that is a table)."""
    return Numbers((params.box.width, params.box.height))


class WriteParams(NodeParams):
    path: str
    """File to write."""


@node("toy.write", side_effects=True)
def write(numbers: Numbers, params: WriteParams) -> None:
    """Writes the list to a file (a sink)."""
    Path(params.path).write_text(json.dumps(list(numbers.values)), encoding="utf-8")


@node("toy.fail", output="numbers")
def fail(numbers: Numbers) -> Numbers:
    """Always fails."""
    raise ValueError("broken on purpose")
