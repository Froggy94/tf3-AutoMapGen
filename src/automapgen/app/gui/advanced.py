"""The advanced settings: every parameter of a project's graph as a form, in sections (configs/gui.toml [advanced]).

Nothing here knows a node: the entries come from the parameter slots of the compiled graph, the forms from the
parameter schemas (forms.py), the sections from node type patterns. A new node or parameter therefore appears by
itself, with the description, unit and limits of its schema.

The values shown (`base`) are those of the project without its own changes: the look it extends and the values of
the main page. The page keeps the project's changes in its document and shows them on top; only they are saved. Where
a change goes (`stores`):
- an exposed parameter of the graph: `[defaults]` if the look gives it (a preset value: tables merge key by key with
  the look's, so a change inside the climate is one value), otherwise `[params]`;
- anything else: `[nodes."<path>"]` - a node parameter or an exposed parameter of a subgraph instance. The same node
  in several instances of a subgraph (the draft and the final terrain) is one entry writing all of them, as long as
  their values agree.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from automapgen.app.gui.forms import DEFAULT_KINDS, FieldKind, FormField, SchemaForms
from automapgen.app.gui.settings import SettingsLayout, SettingsSection
from automapgen.app.projects import ProjectStore
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.library import GraphLibrary
from automapgen.graph.paramsets import ParameterSet
from automapgen.graph.plan import ExecutionPlan, ParameterSlot

type Location = tuple[str, ...]
"""Path of a value in a project document: ("params", "max_towns"), ("nodes", "draft/water", "banks")."""


@dataclass(frozen=True)
class SettingEntry:
    name: str
    description: str
    field: FormField
    base: Any
    stores: tuple[Location, ...]
    """Where a change is written (all of them)."""
    reads: tuple[Location, ...]
    """Where the project may hold a change, the first found wins (all are cleared when the entry is reset)."""

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "field": self.field.as_json(), "base": self.base,
                "stores": [list(s) for s in self.stores], "reads": [list(r) for r in self.reads]}


@dataclass(frozen=True)
class SettingGroup:
    """The entries of one node (or of the graph itself)."""

    title: str
    node_type: str
    description: str
    entries: tuple[SettingEntry, ...]

    def as_json(self) -> dict[str, Any]:
        return {"title": self.title, "type": self.node_type, "description": self.description,
                "entries": [e.as_json() for e in self.entries]}


@dataclass(frozen=True)
class FormSection:
    key: str
    label: str
    groups: tuple[SettingGroup, ...]

    def as_json(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "groups": [g.as_json() for g in self.groups]}


class AdvancedSettings:
    """Describes the advanced settings of a project document."""

    def __init__(self, layout: SettingsLayout, catalog: NodeCatalog, library: GraphLibrary, projects: ProjectStore,
                 kinds: Sequence[FieldKind] = DEFAULT_KINDS) -> None:
        self._layout = layout
        self._compiler = GraphCompiler(catalog, library)
        self._library = library
        self._projects = projects
        self._kinds = kinds

    def describe(self, document: Mapping[str, Any]) -> list[FormSection]:
        """Sections of the project document (the page's: its own changes are not part of the values shown)."""
        parameters = self._projects.parameters(self.base_document(document))
        graph = self._library.get(parameters.graph or "")
        if graph is None:
            raise ValueError(f"unknown graph {parameters.graph!r}")
        return _Description(self._compiler.compile(graph, parameters), parameters, self._layout, self._kinds).sections()

    def base_document(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """The project without its own changes: graph, what it extends and the values of the main page."""
        params = document.get("params", {})
        return {"graph": document.get("graph") or "map", "extends": list(document.get("extends", [])),
                "params": {k: v for k, v in params.items() if k in self._layout.main_page}}


class _Description:
    """The sections of one compiled project."""

    def __init__(self, plan: ExecutionPlan, parameters: ParameterSet, layout: SettingsLayout,
                 kinds: Sequence[FieldKind]) -> None:
        self._plan = plan
        self._presets = parameters.defaults
        self._layout = layout
        self._kinds = kinds
        self._order = {node.id: i for i, node in enumerate(plan.nodes)}
        self._dumps: dict[str, dict[str, Any]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}

    def sections(self) -> list[FormSection]:
        entries: dict[str, dict[tuple[str, ...], list[tuple[ParameterSlot, SettingEntry]]]] = {}
        for slots in self._merged(s for s in self._plan.slots if not self._hidden(s)):
            by_owners = entries.setdefault(self._section(slots[0]).key, {})
            by_owners.setdefault(tuple(s.owner for s in slots), []).append((slots[0], self._entry(slots)))
        out = []
        for section in (*self._layout.sections, self._layout.other):
            groups = sorted(entries.get(section.key, {}).items(), key=lambda item: self._position(item[1][0][0]))
            if groups:
                out.append(FormSection(section.key, section.label,
                                       tuple(self._group(owners, members) for owners, members in groups)))
        return out

    def _merged(self, slots: Any) -> list[list[ParameterSlot]]:
        """Slots of the same node and parameter in several subgraph instances with the same value are one entry."""
        merged: dict[tuple[str, ...], list[ParameterSlot]] = {}
        for slot in slots:
            key = (slot.owner, slot.name) if not slot.owner else (
                slot.scope, slot.local, slot.name, json.dumps(self._base(slot), sort_keys=True))
            merged.setdefault(key, []).append(slot)
        return list(merged.values())

    def _hidden(self, slot: ParameterSlot) -> bool:
        if not slot.owner:
            return slot.name in self._layout.main_page
        target = slot.targets[0]
        name = f"{self._plan.node(target.node).spec.type_id}.{target.name}"
        return any(fnmatchcase(name, pattern) for pattern in self._layout.hidden)

    def _section(self, slot: ParameterSlot) -> SettingsSection:
        type_id = self._plan.node(slot.targets[0].node).spec.type_id
        return next((s for s in self._layout.sections if any(fnmatchcase(type_id, p) for p in s.types)),
                    self._layout.other)

    def _position(self, slot: ParameterSlot) -> int:
        return min(self._order[t.node] for t in slot.targets)

    def _group(self, owners: tuple[str, ...], members: list[tuple[ParameterSlot, SettingEntry]]) -> SettingGroup:
        first = members[0][0]
        if not first.owner:
            return SettingGroup(f"{first.scope} (the whole map)", "", "", tuple(e for _, e in members))
        entries = tuple(e for _, e in members)
        if first.owner != first.targets[0].node:          # an exposed parameter of a subgraph instance
            return SettingGroup(", ".join(owners), "subgraph", "", entries)
        spec = self._plan.node(first.owner).spec
        return SettingGroup(", ".join(owners), spec.type_id, spec.description.split("\n")[0], entries)

    def _entry(self, slots: list[ParameterSlot]) -> SettingEntry:
        slot, target = slots[0], slots[0].targets[0]
        schema = self._schema(target.node)
        field = SchemaForms(schema.get("$defs", {}), self._kinds).field(schema["properties"][target.name],
                                                                        self._base(slot))
        if slot.owner:
            stores: tuple[Location, ...] = tuple(("nodes", s.owner, s.name) for s in slots)
            reads = stores
        else:
            stores = (("defaults" if slot.name in self._presets else "params", slot.name),)
            reads = (("params", slot.name), ("defaults", slot.name))
        return SettingEntry(slot.name, slot.description or field.description, field, self._base(slot), stores, reads)

    def _base(self, slot: ParameterSlot) -> Any:
        target = slot.targets[0]
        if target.node not in self._dumps:
            self._dumps[target.node] = self._plan.node(target.node).params.model_dump(mode="json")
        return self._dumps[target.node][target.name]

    def _schema(self, node: str) -> dict[str, Any]:
        spec = self._plan.node(node).spec
        if spec.type_id not in self._schemas:
            self._schemas[spec.type_id] = spec.params_model.model_json_schema()
        return self._schemas[spec.type_id]
