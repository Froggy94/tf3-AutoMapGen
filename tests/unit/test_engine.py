"""Running plans: results, only what is needed, cache reuse by origin, side effects, cancel, failures, previews."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automapgen.graph.cache import DiskCache, MemoryCache, NodeCache
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.document import GraphDocument
from automapgen.graph.engine import CancelToken, Engine, PreviewWriter, RunResult
from automapgen.graph.errors import InputError, NodeFailedError, RunCancelledError
from automapgen.graph.paramsets import ParameterSet
from tests.helpers import compiler, graph
from tests.toy.nodes import Numbers

DOC = graph(
    inputs={"extra": "numbers"}, outputs={"total": "number", "first": "numbers"},
    nodes={"a": {"type": "toy.constant", "params": {"value": 2}}, "s": {"type": "toy.scale"},
           "split": {"type": "toy.split"}, "sum": {"type": "toy.sum"}, "unused": {"type": "toy.constant"}},
    links=[["a.numbers", "s.numbers"], ["s.numbers", "split.numbers"], ["split.first", "outputs.first"],
           ["s.numbers", "sum.numbers"], ["inputs.extra", "sum.numbers"], ["sum.total", "outputs.total"]])
EXTRA = {"extra": Numbers((1.0, 1.0))}


class Recorder:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.messages: list[str] = []

    def node_started(self, node_id: str, type_id: str, index: int, total: int) -> None:
        self.started.append(node_id)

    def node_finished(self, node_id: str, seconds: float) -> None:
        pass

    def message(self, node_id: str, text: str) -> None:
        self.messages.append(f"{node_id}: {text}")

    def progress(self, node_id: str, fraction: float) -> None:
        pass


def run(catalog: NodeCatalog, doc: GraphDocument = DOC, inputs: dict[str, object] | None = None,
        cache: NodeCache | None = None, parameters: ParameterSet | None = None,
        cancel: CancelToken | None = None) -> tuple[RunResult, Recorder]:
    recorder = Recorder()
    plan = compiler(catalog).compile(doc, parameters)
    result = Engine(cache, recorder).run(plan, EXTRA if inputs is None else inputs, cancel)
    return result, recorder


def test_runs_only_what_the_outputs_need(catalog: NodeCatalog) -> None:
    result, recorder = run(catalog)

    assert result.outputs == {"total": 14.0, "first": Numbers((4.0,))}
    assert recorder.started == ["a", "s", "split", "sum"]
    assert "unused" not in result.executed
    assert recorder.messages == ["sum: 2 lists"]


def test_cache_reuses_results_by_origin(catalog: NodeCatalog) -> None:
    cache = MemoryCache()
    run(catalog, cache=cache)

    again, recorder = run(catalog, cache=cache)
    changed, changed_recorder = run(catalog, cache=cache, parameters=ParameterSet(nodes={"s": {"factor": 3}}))
    other_input, input_recorder = run(catalog, cache=cache, inputs={"extra": Numbers((5.0,))})

    assert recorder.started == []
    assert again.outputs["total"] == 14.0
    assert set(again.reused) == {"split", "sum"}
    assert changed_recorder.started == ["s", "split", "sum"]
    assert changed.outputs["total"] == 20.0
    assert input_recorder.started == ["sum"]
    assert other_input.outputs["total"] == 17.0


def test_disk_cache_keeps_results_between_engines(catalog: NodeCatalog, tmp_path: Path) -> None:
    run(catalog, cache=DiskCache(tmp_path))

    result, recorder = run(catalog, cache=DiskCache(tmp_path))

    assert recorder.started == []
    assert result.outputs["first"] == Numbers((4.0,))
    assert not list(tmp_path.rglob("*.partial*"))


def test_side_effect_nodes_always_run_and_are_not_cached(catalog: NodeCatalog, tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    doc = graph(nodes={"a": {"type": "toy.constant"}, "w": {"type": "toy.write", "params": {"path": str(target)}}},
                links=[["a.numbers", "w.numbers"]])
    cache = MemoryCache()

    run(catalog, doc, {}, cache)
    target.unlink()
    _, recorder = run(catalog, doc, {}, cache)

    assert recorder.started == ["w"]
    assert json.loads(target.read_text()) == [1.0, 1.0, 1.0]


def test_targets_run_only_the_chosen_nodes_and_what_they_need(catalog: NodeCatalog, tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    doc = graph(nodes={"a": {"type": "toy.constant"}, "s": {"type": "toy.scale"},
                       "w1": {"type": "toy.write", "params": {"path": str(first)}},
                       "w2": {"type": "toy.write", "params": {"path": str(second)}}},
                links=[["a.numbers", "w1.numbers"], ["a.numbers", "s.numbers"], ["s.numbers", "w2.numbers"]])
    plan = compiler(catalog).compile(doc)
    recorder = Recorder()
    cache = MemoryCache()

    result = Engine(cache, recorder).run(plan, {}, targets=["w1"])
    Engine(cache, recorder).run(plan, {}, targets=["s"])          # a source of a later step, computed ahead

    assert recorder.started == ["a", "w1", "s"]
    assert first.is_file()
    assert not second.exists()
    assert result.outputs == {}
    with pytest.raises(InputError, match="unknown target nodes nope"):
        Engine().run(plan, {}, targets=["nope"])


def test_wrong_inputs_are_reported(catalog: NodeCatalog) -> None:
    with pytest.raises(InputError, match="missing input 'extra'"):
        run(catalog, inputs={})
    with pytest.raises(InputError, match=r"input 'extra' should be numbers, got float.*unknown input 'x'|"
                                         r"unknown input 'x'.*input 'extra' should be numbers"):
        run(catalog, inputs={"extra": 1.0, "x": 1})


def test_failing_node_is_named(catalog: NodeCatalog) -> None:
    doc = graph(outputs={"n": "numbers"}, nodes={"a": {"type": "toy.constant"}, "f": {"type": "toy.fail"}},
                links=[["a.numbers", "f.numbers"], ["f.numbers", "outputs.n"]])

    with pytest.raises(NodeFailedError, match=r"node 'f' \(toy.fail\) failed: ValueError: broken on purpose"):
        run(catalog, doc, {})


def test_cancelled_run_stops(catalog: NodeCatalog) -> None:
    token = CancelToken()
    token.cancel()

    with pytest.raises(RunCancelledError):
        run(catalog, cancel=token)


def test_previews_of_executed_nodes(catalog: NodeCatalog, tmp_path: Path) -> None:
    plan = compiler(catalog).compile(DOC)

    Engine(previews=PreviewWriter(tmp_path, only={"split", "sum"})).run(plan, EXTRA)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["split.first.txt", "split.second.txt", "sum.total.txt"]
    assert (tmp_path / "split.second.txt").read_text() == "[4.0, 4.0]"
