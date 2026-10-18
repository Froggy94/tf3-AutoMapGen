"""A map run started from the GUI: compile the project, run the chosen processing steps with the engine in a
background thread, collect the import summary. Cancelling stops between nodes (and inside long nodes); "cancel +
delete" removes every file the run created or changed in the map's output folder and the game's user data folder."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automapgen.app.gui.settings import GuiPaths
from automapgen.app.gui.summary import ImportSummary
from automapgen.app.local_settings import LocalSettings
from automapgen.app.projects import ProjectStore
from automapgen.app.steps import ProcessingStep, StepPlan
from automapgen.graph.cache import DiskCache
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.engine import CancelToken, Engine
from automapgen.graph.errors import RunCancelledError
from automapgen.graph.library import FolderLibrary
from automapgen.graph.node import OutputTarget
from automapgen.graph.paramsets import ParameterSet
from automapgen.nodes.gamefiles import GameUserData

_MAX_LISTED = 200
_SLOW_S = 5.0                   # nodes taking longer are reported with their time


class RunLog:
    """Lines of the current run and the node it is in (read by the page while the run is going on)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.lines: list[str] = []
        self.step = ""

    def add(self, text: str) -> None:
        with self.lock:
            self.lines.append(text)

    def reset(self) -> None:
        with self.lock:
            self.lines, self.step = [], ""


class LogListener:
    """The engine's events as log lines (a RunListener)."""

    def __init__(self, log: RunLog) -> None:
        self._log = log

    def node_started(self, node_id: str, type_id: str, index: int, total: int) -> None:
        self._log.step = f"{index}/{total} {node_id}"
        self._log.add(f"[{index}/{total}] {node_id} ({type_id})")

    def node_finished(self, node_id: str, seconds: float) -> None:
        if seconds >= _SLOW_S:
            self._log.add(f"        {node_id} done in {seconds:.0f} s")

    def message(self, node_id: str, text: str) -> None:
        self._log.add(f"        {node_id}: {text}")

    def progress(self, node_id: str, fraction: float) -> None:
        self._log.step = f"{self._log.step.split(' (')[0]} ({round(fraction * 100)} %)"


@dataclass(frozen=True)
class OutputSnapshot:
    """Files (path -> modification time, size) and folders below some roots."""

    files: Mapping[str, tuple[int, int]]
    dirs: frozenset[str]

    @classmethod
    def take(cls, roots: Sequence[Path]) -> OutputSnapshot:
        files: dict[str, tuple[int, int]] = {}
        dirs: set[str] = set()
        for root in roots:
            for folder, _, names in os.walk(root):
                dirs.add(folder)
                for name in names:
                    path = os.path.join(folder, name)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    files[path] = (st.st_mtime_ns, st.st_size)
        return cls(files, frozenset(dirs))

    def changed_since(self, before: OutputSnapshot) -> list[str]:
        return sorted(p for p, st in self.files.items() if before.files.get(p) != st)

    def new_dirs_since(self, before: OutputSnapshot) -> list[str]:
        return sorted(self.dirs - before.dirs, key=len, reverse=True)


@dataclass(frozen=True)
class RunChoice:
    """What the user ticked: the processing steps (keys) and whether files are copied into the game."""

    steps: frozenset[str]
    copy_to_game: bool = True


class MapJob:
    """One run of a project: compile, run the chosen steps with the settings of this computer, summary."""

    def __init__(self, paths: GuiPaths, catalog: NodeCatalog, library: FolderLibrary, projects: ProjectStore,
                 steps: Sequence[ProcessingStep]) -> None:
        self._paths = paths
        self._catalog = catalog
        self._library = library
        self._projects = projects
        self._steps = steps

    def roots(self, project_id: str, choice: RunChoice) -> list[Path]:
        """Folders the run writes to: the map's output folder and, when copying, the game's user data folders."""
        name = self._map_name(self._projects.merged(project_id))
        return [self._paths.output / name, *self._destinations(choice).values()]

    def run(self, project_id: str, choice: RunChoice, log: RunLog, cancel: CancelToken) -> dict[str, Any]:
        parameters = self._projects.merged(project_id)
        name = self._map_name(parameters)
        document = self._library.get(parameters.graph or "map")
        if document is None:
            raise ValueError(f"unknown graph {parameters.graph!r}")
        plan = GraphCompiler(self._catalog, self._library).compile(
            document, ParameterSet.layered([parameters], document.id))
        targets = StepPlan(self._steps, plan).targets(choice.steps)
        local = LocalSettings.load(self._paths.local_settings)
        destinations = self._destinations(choice)
        labels = ", ".join(s.label for s in self._steps if s.key in choice.steps)
        copies = (str(local.user_data_folder) if destinations else
                  "off" if local.user_data_folder else "none (no user_data_folder in local/settings.toml)")
        log.add(f"===== {name}: graph {document.id}; steps: {labels}; copies into the game: {copies} =====")
        engine = Engine(DiskCache(self._paths.cache), LogListener(log), None,
                        OutputTarget(self._paths.output / name, name, destinations), self._paths.downloads,
                        local.game_folder, local.secrets)
        t0 = time.time()
        result = engine.run(plan, {}, cancel, targets)
        log.add(f"----- done after {time.time() - t0:.0f} s: {len(result.executed)} nodes ran, "
                f"{len(result.reused)} reused from the cache")
        return ImportSummary(self._paths.output).build(parameters)

    def _destinations(self, choice: RunChoice) -> dict[str, Path]:
        local = LocalSettings.load(self._paths.local_settings)
        if not choice.copy_to_game or not local.user_data_folder:
            return {}
        return dict(GameUserData(local.user_data_folder).destinations())

    @staticmethod
    def _map_name(parameters: ParameterSet) -> str:
        name = parameters.params.get("map_name")
        if not name:
            raise ValueError("the project has no map_name")
        return ProjectStore.checked(str(name))


class GenerationRun:
    """At most one run at a time in a background thread; status for the page, cancel, cancel + delete."""

    def __init__(self, job: MapJob, root: Path) -> None:
        self._job = job
        self._root = root
        self.log = RunLog()
        self.running = False
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self._cancel = CancelToken()
        self._before: OutputSnapshot | None = None
        self._roots: Sequence[Path] = []
        self._thread: threading.Thread | None = None

    @property
    def can_delete(self) -> bool:
        return self._before is not None

    def start(self, project_id: str, choice: RunChoice) -> None:
        with self.log.lock:
            if self.running:
                raise ValueError("a run is going on already")
            self.running, self.result, self.error = True, None, None
        try:
            self._roots = self._job.roots(project_id, choice)
        except Exception:
            self.running = False
            raise
        self.log.reset()
        self._cancel = CancelToken()
        self._before = OutputSnapshot.take(self._roots)
        self._thread = threading.Thread(target=self._work, args=(project_id, choice), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.cancel()

    def cancel_and_delete(self) -> int:
        """Cancel the run, then delete all files created or changed since its start."""
        before = self._before
        if before is None:
            raise ValueError("no run whose files could be deleted")
        self.cancel()
        if self._thread:
            self._thread.join(timeout=120)
        after = OutputSnapshot.take(self._roots)
        changed = after.changed_since(before)
        failed = []
        for path in changed:
            try:
                os.remove(path)
            except OSError as error:
                failed.append(f"{path}: {error}")
        for folder in after.new_dirs_since(before):     # new folders, empty now
            with contextlib.suppress(OSError):
                os.rmdir(folder)
        deleted = len(changed) - len(failed)
        self._report_deleted(changed, failed, deleted)
        self._before = None
        self.result = None
        return deleted

    def _report_deleted(self, changed: Sequence[str], failed: Sequence[str], deleted: int) -> None:
        self.log.add("")
        self.log.add(f"===== {deleted} files of this run deleted =====")
        root = str(self._root.resolve())
        for path in changed[:_MAX_LISTED]:
            self.log.add("  " + (os.path.relpath(path, root) if path.startswith(root) else path))
        if len(changed) > _MAX_LISTED:
            self.log.add(f"  ... and {len(changed) - _MAX_LISTED} more")
        for line in failed:
            self.log.add("  not deleted: " + line)

    def _work(self, project_id: str, choice: RunChoice) -> None:
        try:
            self.result = self._job.run(project_id, choice, self.log, self._cancel)
            self.log.add("")
            self.log.add("===== DONE =====")
        except RunCancelledError:
            self.error = "cancelled"
            self.log.add("===== CANCELLED =====")
        except Exception as error:   # show every error in the page instead of ending the server
            self.error = str(error)
            self.log.add(f"ERROR: {error}")
        finally:
            with self.log.lock:
                self.running = False
                self.log.step = ""

