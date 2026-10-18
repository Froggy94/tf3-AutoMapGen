"""The GUI's HTTP API: GET and POST routes -> response (status, body, content type)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from automapgen.app.gui.advanced import AdvancedSettings
from automapgen.app.gui.graph_view import GraphView
from automapgen.app.gui.run import GenerationRun, RunChoice
from automapgen.app.gui.settings import GuiPaths, GuiSettings
from automapgen.app.gui.tif_info import TifInfo
from automapgen.app.local_settings import LocalSettings
from automapgen.app.map_sizes import PIXELS_PER_TILE, MapSizeCatalog
from automapgen.app.projects import ProjectStore
from automapgen.graph.catalog import NodeCatalog
from automapgen.nodes.opentopography import SECRET, DemDownload
from automapgen.nodes.rivers import REAL_WATER_ABOVE_M

JSON = "application/json; charset=utf-8"


@dataclass(frozen=True)
class Response:
    status: int
    body: Any
    content_type: str = JSON

    def payload(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        if isinstance(self.body, str):
            return self.body.encode("utf-8")
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


NOT_FOUND = Response(404, {"error": "not found"})
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class GuiApi:
    """Routes of the page page.html."""

    def __init__(self, paths: GuiPaths, settings: GuiSettings, sizes: MapSizeCatalog, projects: ProjectStore,
                 run: GenerationRun, catalog: NodeCatalog, advanced: AdvancedSettings, graphs: GraphView) -> None:
        self._paths = paths
        self._settings = settings
        self._sizes = sizes
        self._projects = projects
        self._run = run
        self._catalog = catalog
        self._advanced = advanced
        self._graphs = graphs
        self._get: Mapping[str, Callable[[Mapping[str, str]], Response]] = {
            "/": self._page, "/index.html": self._page, "/api/setup": self._setup, "/api/project": self._project,
            "/api/tif": self._tif, "/api/log": self._log, "/api/graph": self._graph}
        self._post: Mapping[str, Callable[[Mapping[str, Any]], Response]] = {
            "/api/save": self._save, "/api/run": self._start, "/api/stop": self._stop,
            "/api/stop_delete": self._stop_delete, "/api/state": self._state, "/api/key": self._key,
            "/api/settings": self._settings_form, "/api/check": self._check, "/api/folders": self._folders}

    def stop(self) -> None:
        """Server shutdown: cancel a running generation."""
        self._run.cancel()

    def get(self, path: str, query: Mapping[str, str]) -> Response:
        try:
            route = self._get.get(path)
            if route is not None:
                return route(query)
            if path.startswith("/output/") and path.endswith(".png"):
                return self._picture(path)
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))
            return NOT_FOUND
        except Exception as error:    # every error goes to the page
            return Response(500, {"error": str(error)})

    def post(self, path: str, body: Mapping[str, Any]) -> Response:
        try:
            route = self._post.get(path)
            return route(body) if route is not None else NOT_FOUND
        except Exception as error:
            return Response(400, {"error": str(error)})

    # --- GET -------------------------------------------------------------------------------------------------------

    def _page(self, query: Mapping[str, str]) -> Response:
        page = resources.files("automapgen.app.gui").joinpath("page.html").read_text(encoding="utf-8")
        return Response(200, page, "text/html; charset=utf-8")

    def _static(self, name: str) -> Response:
        """Scripts and styles of the page (static/, package resources)."""
        content_type = STATIC_TYPES.get(Path(name).suffix)
        file = resources.files("automapgen.app.gui").joinpath("static", name)
        if content_type is None or "/" in name or not file.is_file():
            return NOT_FOUND
        return Response(200, file.read_text(encoding="utf-8"), content_type)

    def _setup(self, query: Mapping[str, str]) -> Response:
        s = self._settings
        local = LocalSettings.load(self._paths.local_settings)
        state = json.loads(self._paths.state.read_text(encoding="utf-8")) if self._paths.state.is_file() else None
        return Response(200, {
            "sizes": [{"key": z.key, "label": z.label, "tiles": [list(t) for t in z.tiles]} for z in self._sizes.sizes],
            "formats": list(self._sizes.formats), "tile_m": self._sizes.tile_m, "tile_px": PIXELS_PER_TILE,
            "scale_steps": list(s.scale_steps), "large_km2": s.large_map_km2,
            "projects": [asdict(p) for p in self._projects.entries()],
            "looks": [asdict(look) for look in self._projects.looks(self._paths.configs)],
            "default_look": s.default_landscape,
            "dem_types": [d.model_dump() for d in s.dem_types], "margin_km": DemDownload().margin_km,
            "api_key_set": SECRET in local.secrets, "user_data": str(local.user_data_folder or ""),
            "game_folder": str(local.game_folder or ""),
            "towns_per_100_km2": self._default("towns.plan", "towns_per_100_km2"),
            "town_distance_game_m": self._default("towns.plan", "min_distance_game_m"),
            "real_water_above_m": REAL_WATER_ABOVE_M, "last_state": state,
            "climate_reference_step_m": self._default("climate.reference", "step_m"),
            "steps": [{"key": t.key, "label": t.label, "description": t.description} for t in s.steps],
            "graphs": self._graphs.choices()})

    def _default(self, type_id: str, name: str) -> Any:
        """Default of a node parameter (shown as placeholder)."""
        spec = self._catalog.spec(type_id)
        field = spec.params_model.model_fields.get(name) if spec is not None else None
        return None if field is None else field.default

    def _project(self, query: Mapping[str, str]) -> Response:
        project_id = query["id"]
        return Response(200, {"id": project_id, "doc": self._projects.load(project_id)})

    def _tif(self, query: Mapping[str, str]) -> Response:
        b = json.loads(query["bounds"]) if query.get("bounds") else None
        crop = (b["west"], b["south"], b["east"], b["north"]) if b else None
        path = Path(query["path"])
        return Response(200, TifInfo.read(path if path.is_absolute() else self._paths.root / path, crop).as_json())

    def _log(self, query: Mapping[str, str]) -> Response:
        start = int(query.get("from", 0))
        run = self._run
        with run.log.lock:
            lines = run.log.lines[start:]
            return Response(200, {"lines": lines, "next": start + len(lines), "running": run.running,
                                  "can_delete": run.can_delete, "step": run.log.step, "error": run.error,
                                  "result": run.result})

    def _graph(self, query: Mapping[str, str]) -> Response:
        return Response(200, self._graphs.diagram(query["id"]))

    def _picture(self, path: str) -> Response:
        file = (self._paths.root / path.lstrip("/")).resolve()
        if file.is_relative_to(self._paths.output.resolve()) and file.is_file():
            return Response(200, file.read_bytes(), "image/png")
        return NOT_FOUND

    # --- POST ------------------------------------------------------------------------------------------------------

    def _save(self, body: Mapping[str, Any]) -> Response:
        return Response(200, {"id": self._write(body)})

    def _settings_form(self, body: Mapping[str, Any]) -> Response:
        """The advanced settings of the page's project document (values without the project's own changes)."""
        return Response(200, {"sections": [s.as_json() for s in self._advanced.describe(body["doc"])]})

    def _check(self, body: Mapping[str, Any]) -> Response:
        """Problems of the page's project document with its graph, before a run."""
        doc = body["doc"]
        return Response(200, {"problems": self._graphs.check(doc), "warnings": self._graphs.warnings(doc)})

    def _start(self, body: Mapping[str, Any]) -> Response:
        if self._run.running:
            raise ValueError("a run is going on already")
        steps = frozenset(body.get("steps", [t.key for t in self._settings.steps]))
        project_id = self._write(body)
        self._run.start(project_id, RunChoice(steps, bool(body.get("copy_to_game", True))))
        return Response(200, {"id": project_id})

    def _write(self, body: Mapping[str, Any]) -> str:
        """Saves the project of the page; a new project gets its id from the map name."""
        doc = dict(body["doc"])
        project_id = body.get("id") or self._projects.new_id(str(doc.get("params", {}).get("map_name", "")))
        self._projects.save(project_id, doc)
        return str(project_id)

    def _stop(self, body: Mapping[str, Any]) -> Response:
        self._run.cancel()
        return Response(200, {"ok": True})

    def _stop_delete(self, body: Mapping[str, Any]) -> Response:
        return Response(200, {"ok": True, "deleted": self._run.cancel_and_delete()})

    def _state(self, body: Mapping[str, Any]) -> Response:
        """Last project, map view and run choice of the page."""
        self._paths.state.parent.mkdir(parents=True, exist_ok=True)
        self._paths.state.write_text(json.dumps(dict(body), ensure_ascii=False), encoding="utf-8")
        return Response(200, {"ok": True})

    def _folders(self, body: Mapping[str, Any]) -> Response:
        """Saves the game folder and the user data folder (empty = not set) after checking them."""
        game, user_data = (Path(text) if (text := str(body.get(key) or "").strip()) else None
                           for key in ("game_folder", "user_data_folder"))
        if game is not None and not (game / "base" / "content").is_dir():
            raise ValueError(f"{game} is not the game's installation folder: it has no base/content")
        if user_data is not None and not user_data.is_dir():
            raise ValueError(f"the user data folder {user_data} does not exist")
        LocalSettings.save_folders(self._paths.local_settings, game, user_data)
        return Response(200, {"game_folder": str(game or ""), "user_data": str(user_data or "")})

    def _key(self, body: Mapping[str, Any]) -> Response:
        LocalSettings.save_secret(self._paths.local_settings, SECRET, str(body.get("key", "")).strip())
        return Response(200, {"ok": True, "api_key_set": bool(str(body.get("key", "")).strip())})
