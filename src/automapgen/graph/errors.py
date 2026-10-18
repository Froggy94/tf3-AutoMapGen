"""Errors of the graph framework. Every error says where it happened (graph, node, port, file)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class AutoMapGenError(Exception):
    """Base of all errors the framework raises on purpose."""


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a graph or parameter set: where it is and what is wrong."""

    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


class GraphError(AutoMapGenError):
    """A graph (or parameter set) cannot be used. Lists every problem found, not only the first."""

    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(["graph is invalid:", *(f"  - {p}" for p in self.problems)]))


class NodeDefinitionError(AutoMapGenError):
    """A node function or data type is declared wrongly - an error of its author, found when the catalog is built."""


class InputError(AutoMapGenError):
    """The values given to a run do not match the graph's inputs."""


class NodeFailedError(AutoMapGenError):
    """A node raised while running."""

    def __init__(self, node_id: str, type_id: str, cause: BaseException) -> None:
        self.node_id = node_id
        self.type_id = type_id
        super().__init__(f"node '{node_id}' ({type_id}) failed: {type(cause).__name__}: {cause}")


class RunCancelledError(AutoMapGenError):
    """The run was cancelled."""
