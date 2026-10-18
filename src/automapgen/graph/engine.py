"""Runs an execution plan.

Only what is needed runs: the producers of the graph outputs and every node with side effects - or, if the run names
targets, those nodes and their producers only - minus everything whose results are cached (see LineageKeys). Results
are released as soon as their last consumer has run. The engine knows no domain; nodes, data types and parameters
come from the catalog.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from automapgen.graph.cache import LineageKeys, NoCache, NodeCache
from automapgen.graph.data import DataType
from automapgen.graph.errors import InputError, NodeFailedError, RunCancelledError
from automapgen.graph.node import Arity, NodeContext, OutputTarget
from automapgen.graph.plan import ExecutionPlan, PlannedNode, PortRef


class CancelToken:
    """Lets another thread (GUI) cancel a run; checked between nodes and by long nodes."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class RunListener(Protocol):
    def node_started(self, node_id: str, type_id: str, index: int, total: int) -> None: ...

    def node_finished(self, node_id: str, seconds: float) -> None: ...

    def message(self, node_id: str, text: str) -> None: ...

    def progress(self, node_id: str, fraction: float) -> None: ...


class LoggingListener:
    """Reports a run through the `logging` module."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("automapgen.run")

    def node_started(self, node_id: str, type_id: str, index: int, total: int) -> None:
        self._log.info("[%d/%d] %s (%s)", index, total, node_id, type_id)

    def node_finished(self, node_id: str, seconds: float) -> None:
        self._log.info("        %s done in %.2f s", node_id, seconds)

    def message(self, node_id: str, text: str) -> None:
        self._log.info("        %s: %s", node_id, text)

    def progress(self, node_id: str, fraction: float) -> None:
        self._log.debug("        %s: %d %%", node_id, round(fraction * 100))


class PreviewWriter:
    """Writes a preview of the outputs of executed nodes (all, or only the listed node paths) into a folder."""

    def __init__(self, folder: Path, only: Collection[str] | None = None) -> None:
        self._folder = folder
        self._only = None if only is None else set(only)

    def write(self, node_id: str, outputs: Mapping[str, tuple[DataType[Any], object]]) -> None:
        if self._only is not None and node_id not in self._only:
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        for name, (data_type, value) in outputs.items():
            if data_type.preview is None:
                continue
            preview = data_type.preview(value)
            stem = f"{node_id.replace('/', '__')}.{name}"
            if isinstance(preview, str):
                (self._folder / f"{stem}.txt").write_text(preview, encoding="utf-8")
            else:
                preview.save(self._folder / f"{stem}.png")


@dataclass(frozen=True)
class RunResult:
    outputs: Mapping[str, object]
    executed: tuple[str, ...]
    """Node paths that ran, in order."""
    reused: tuple[str, ...]
    """Node paths whose cached results were used."""


class Engine:
    """Runs plans. `output` is where sinks write, `downloads` where sources keep downloaded data, `game` the game's
    files, `secrets` named secrets of this computer such as API keys (settings of the run, not of the graph; never
    part of cache keys)."""

    def __init__(self, cache: NodeCache | None = None, listener: RunListener | None = None,
                 previews: PreviewWriter | None = None, output: OutputTarget | None = None,
                 downloads: Path | None = None, game: Path | None = None,
                 secrets: Mapping[str, str] | None = None) -> None:
        self._cache = cache or NoCache()
        self._listener = listener or LoggingListener()
        self._previews = previews
        self._output = output
        self._downloads = downloads
        self._game = game
        self._secrets = dict(secrets or {})

    def run(self, plan: ExecutionPlan, inputs: Mapping[str, object], cancel: CancelToken | None = None,
            targets: Collection[str] | None = None) -> RunResult:
        """Runs the plan with values for its graph inputs and returns the graph outputs. With `targets` (node ids)
        only these nodes and what they need run - e.g. a chosen part of the sinks - and no graph outputs are
        returned."""
        _check_inputs(plan, inputs)
        if targets is not None:
            _check_targets(plan, targets)
        folders = _RunSettings(self._output, self._downloads, self._game, self._secrets)
        return _Run(plan, inputs, self._cache, self._listener, self._previews, folders,
                    cancel or CancelToken(), targets).execute()


def _check_inputs(plan: ExecutionPlan, inputs: Mapping[str, object]) -> None:
    problems = [f"missing input '{n}'" for n in plan.inputs if n not in inputs]
    problems += [f"unknown input '{n}'" for n in inputs if n not in plan.inputs]
    problems += [f"input '{n}' should be {t.name}, got {type(inputs[n]).__name__}"
                 for n, t in plan.inputs.items() if n in inputs and not t.accepts(inputs[n])]
    if problems:
        raise InputError(f"graph '{plan.graph_id}': " + "; ".join(problems))


def _check_targets(plan: ExecutionPlan, targets: Collection[str]) -> None:
    known = {n.id for n in plan.nodes}
    unknown = sorted(t for t in targets if t not in known)
    if unknown:
        raise InputError(f"graph '{plan.graph_id}': unknown target nodes " + ", ".join(unknown))


@dataclass(frozen=True)
class _RunSettings:
    output: OutputTarget | None
    downloads: Path | None
    game: Path | None
    secrets: Mapping[str, str]


class _Run:
    """State of one run: which nodes run, which results are reused, values in memory and their consumers."""

    def __init__(self, plan: ExecutionPlan, inputs: Mapping[str, object], cache: NodeCache, listener: RunListener,
                 previews: PreviewWriter | None, folders: _RunSettings, cancel: CancelToken,
                 targets: Collection[str] | None) -> None:
        self._plan = plan
        self._targets = targets
        self._folders = folders
        self._cache = cache
        self._listener = listener
        self._previews = previews
        self._cancel = cancel
        self._keys = LineageKeys.of(plan, inputs)
        self._values: dict[PortRef, object] = {PortRef("inputs", k): v for k, v in inputs.items()}
        self._reused: set[str] = set()
        self._to_run = self._needed()
        self._consumers: Counter[PortRef] = Counter(ref for n in plan.nodes if n.id in self._to_run
                                                    for ref in n.sources())

    def execute(self) -> RunResult:
        nodes = [n for n in self._plan.nodes if n.id in self._to_run]
        for index, node in enumerate(nodes, 1):
            if self._cancel.cancelled:
                raise RunCancelledError(f"cancelled before node '{node.id}'")
            self._listener.node_started(node.id, node.spec.type_id, index, len(nodes))
            started = time.perf_counter()
            outputs = self._call(node)
            self._listener.node_finished(node.id, time.perf_counter() - started)
            self._keep(node, outputs)
            self._release(node)
        outputs = {} if self._targets is not None else {
            name: self._value(ref) for name, ref in self._plan.outputs.items()}
        return RunResult(outputs, tuple(n.id for n in nodes), tuple(sorted(self._reused)))

    def _needed(self) -> set[str]:
        """Nodes that must run: producers of graph outputs and side-effect nodes (or of the targets), back to cached
        results."""
        to_run: set[str] = set()
        if self._targets is not None:
            pending = list(self._targets)
        else:
            pending = [ref.node for ref in self._plan.outputs.values() if not ref.is_graph_input]
            pending += [n.id for n in self._plan.nodes if n.spec.side_effects]
        while pending:
            node = self._plan.node(pending.pop())
            if node.id in to_run or node.id in self._reused:
                continue
            if not node.spec.side_effects and all(self._cache.contains(self._keys[PortRef(node.id, name)], port.type)
                                                  for name, port in node.spec.outputs.items()):
                self._reused.add(node.id)
                continue
            to_run.add(node.id)
            pending += [ref.node for ref in node.sources() if not ref.is_graph_input]
        return to_run

    def _call(self, node: PlannedNode) -> dict[str, object]:
        inputs: dict[str, object] = {}
        for port, refs in node.inputs.items():
            values = tuple(self._value(ref) for ref in refs)
            many = node.spec.inputs[port].arity is Arity.MANY
            inputs[port] = values if many else (values[0] if values else None)
        context = NodeContext(node.id, lambda text: self._listener.message(node.id, text),
                              lambda fraction: self._listener.progress(node.id, fraction),
                              lambda: self._cancel.cancelled, self._folders.output, self._folders.downloads,
                              self._folders.game, self._folders.secrets)
        try:
            return node.spec.run(inputs, node.params, context)
        except RunCancelledError:
            raise
        except Exception as e:
            raise NodeFailedError(node.id, node.spec.type_id, e) from e

    def _keep(self, node: PlannedNode, outputs: Mapping[str, object]) -> None:
        for name, value in outputs.items():
            ref = PortRef(node.id, name)
            if self._consumers[ref] or ref in self._plan.outputs.values():
                self._values[ref] = value
            if not node.spec.side_effects:
                self._cache.store(self._keys[ref], node.spec.outputs[name].type, value)
        if self._previews is not None:
            self._previews.write(node.id, {n: (node.spec.outputs[n].type, v) for n, v in outputs.items()})

    def _release(self, node: PlannedNode) -> None:
        for ref in node.sources():
            self._consumers[ref] -= 1
            if self._consumers[ref] == 0 and ref not in self._plan.outputs.values():
                self._values.pop(ref, None)

    def _value(self, ref: PortRef) -> object:
        if ref not in self._values:   # result of a reused node: loaded once, released like any other
            self._values[ref] = self._cache.load(self._keys[ref], self._plan.node(ref.node).spec.outputs[ref.port].type)
        return self._values[ref]
