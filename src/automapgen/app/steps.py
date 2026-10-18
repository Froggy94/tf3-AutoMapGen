"""Processing steps: named parts of a run that the user switches on and off (GUI tick boxes).

A step is data (configs/gui.toml): node path patterns and node type patterns. It owns the sinks it matches (the first
step that matches a sink wins) and, through type patterns, other nodes too - e.g. the sources of a download step.
A run computes only what the chosen steps need; the engine gets their nodes as targets.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict

from automapgen.graph.plan import ExecutionPlan, PlannedNode


class ProcessingStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    key: str
    """Identifier (stays the same when the label is translated)."""
    label: str
    """Display name."""
    description: str = ""
    """What the step writes (tooltip)."""
    nodes: tuple[str, ...] = ()
    """Node paths of the sinks the step writes (patterns: "*_png/write")."""
    types: tuple[str, ...] = ()
    """Node types the step runs (patterns: "osm.*"), sinks or not."""

    def owns(self, node: PlannedNode) -> bool:
        """Path patterns take sinks only, type patterns any node."""
        return ((node.spec.side_effects and any(fnmatchcase(node.id, p) for p in self.nodes))
                or any(fnmatchcase(node.spec.type_id, p) for p in self.types))


class StepPlan:
    """Which nodes of one execution plan belong to which step."""

    def __init__(self, steps: Sequence[ProcessingStep], plan: ExecutionPlan) -> None:
        self._steps = steps
        self._owned: dict[str, list[str]] = {s.key: [] for s in steps}
        self._unassigned: list[str] = []
        for node in plan.nodes:
            owner = next((s for s in steps if s.owns(node)), None)
            if owner is not None:
                self._owned[owner.key].append(node.id)
            elif node.spec.side_effects:
                self._unassigned.append(node.id)

    @property
    def unassigned(self) -> list[str]:
        """Sinks no step owns; they run in every run."""
        return list(self._unassigned)

    def nodes(self, step: str) -> list[str]:
        return list(self._owned[step])

    def targets(self, chosen: Collection[str]) -> list[str]:
        """The nodes a run of the chosen steps must reach (plus the unassigned sinks)."""
        unknown = sorted(set(chosen) - set(self._owned))
        if unknown:
            raise ValueError("unknown processing steps: " + ", ".join(unknown))
        if not chosen:
            raise ValueError("choose at least one processing step")
        return [n for s in self._steps if s.key in chosen for n in self._owned[s.key]] + self._unassigned
