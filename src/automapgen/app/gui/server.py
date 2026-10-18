"""Local web server of the GUI: wires the parts together and serves the API (only http://127.0.0.1:<port>)."""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from automapgen.app.gui.advanced import AdvancedSettings
from automapgen.app.gui.api import GuiApi, Response
from automapgen.app.gui.graph_view import GraphView
from automapgen.app.gui.run import GenerationRun, MapJob
from automapgen.app.gui.settings import GuiPaths, GuiSettings
from automapgen.app.local_settings import LOCAL_SETTINGS, LocalSettings
from automapgen.app.map_sizes import MapSizeCatalog
from automapgen.app.project_checks import ProjectChecks
from automapgen.app.projects import ProjectStore
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.errors import AutoMapGenError
from automapgen.graph.library import FolderLibrary
from automapgen.graphs import GRAPHS_DIR


class PortInUseError(AutoMapGenError):
    """The GUI's port is taken, usually by a GUI that is still running."""


class LocalHttpServer(ThreadingHTTPServer):
    """Only http://127.0.0.1:<port>. On Windows the port is not shared: with address reuse a second GUI would bind
    the same port and the browser would talk to the older one (seen 2026-10-18: an old GUI answered "not found")."""

    allow_reuse_address = sys.platform != "win32"

    @classmethod
    def at(cls, port: int, handler: type[BaseHTTPRequestHandler]) -> LocalHttpServer:
        try:
            return cls(("127.0.0.1", port), handler)
        except OSError as error:
            raise PortInUseError(
                f"port {port} is in use ({error.strerror}): is tf3-AutoMapGen already running? Close its console "
                f"window and start again, or start with another port (gui --port {port + 1}).") from error


class GuiServer:
    """All parts of the GUI for the working directory `root`."""

    def __init__(self, root: Path, catalog: NodeCatalog, port: int | None = None) -> None:
        paths = GuiPaths(root.resolve())
        self._settings = GuiSettings.load(paths.configs / "gui.toml")
        projects = ProjectStore(paths.projects)
        library = FolderLibrary([paths.graphs, GRAPHS_DIR])
        job = MapJob(paths, catalog, library, projects, self._settings.steps)
        self.api = GuiApi(paths, self._settings, MapSizeCatalog.load(paths.configs / "map_sizes.toml"), projects,
                          GenerationRun(job, paths.root), catalog,
                          AdvancedSettings(self._settings.advanced, catalog, library, projects),
                          GraphView(self._settings.graphs, catalog, library, projects,
                                    ProjectChecks(lambda: LocalSettings.load(paths.root / LOCAL_SETTINGS).game_folder)))
        self._port = port or self._settings.port

    def serve(self) -> None:
        server = LocalHttpServer.at(self._port, _handler(self.api))
        url = f"http://127.0.0.1:{self._port}/"
        print(f"tf3-AutoMapGen GUI: {url}  (stop: Ctrl+C)")
        if self._settings.open_browser:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            self.api.stop()


def _handler(api: GuiApi) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:    # no access lines in the console
            pass

        def do_GET(self) -> None:
            url = urlparse(self.path)
            self._send(api.get(url.path, {k: v[0] for k, v in parse_qs(url.query).items()}))

        def do_POST(self) -> None:
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except json.JSONDecodeError as error:
                self._send(Response(400, {"error": str(error)}))
                return
            self._send(api.post(urlparse(self.path).path, body))

        def _send(self, response: Response) -> None:
            data = response.payload()
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler
