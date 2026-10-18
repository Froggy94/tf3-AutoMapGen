"""Command line: inspect node types and check, draw and describe graphs.

    automapgen nodes                                 node types with ports and parameters
    automapgen projects                              the projects (configs/projects/<id>.toml): id, graph, map name
    automapgen gui [--port PORT]                     the local browser GUI (http://127.0.0.1:PORT)
    automapgen check [GRAPH] [--graphs DIR] [--project ID] [--set FILE ...]
                                                     compile a graph (with parameter sets) and list problems
    automapgen show GRAPH [--graphs DIR]            the graph as Mermaid flowchart
    automapgen run [GRAPH] [--project ID] [--set FILE ...] [--name NAME] [--output DIR] [--cache DIR] [--downloads DIR]
                     [--game DIR] [--user-data DIR | --no-copy] [--previews DIR]
                                                     run a graph without inputs; files go to DIR (default
                                                     output/NAME) as NAME<file name>; heightmap, layers, import
                                                     files and mods are also copied into the game's user data
                                                     folder (default: user_data_folder in local/settings.toml)
    automapgen schema                                JSON schema of graph documents
    automapgen reference [--out DIR]                 write the reference of node types and graphs (Markdown)

GRAPH is a `.graph.json` file or a graph id found in the --graphs folders, configs/graphs or among the built-in graphs;
without it,
the graph the parameter sets are made for. --project ID is the parameter set configs/projects/<id>.toml (before the
--set files); NAME defaults to the parameter map_name of the sets.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from automapgen.app.gui.server import GuiServer
from automapgen.app.local_settings import LOCAL_SETTINGS, LocalSettings
from automapgen.app.project_checks import ProjectChecks
from automapgen.app.reference import GraphReference, NodeReference
from automapgen.graph.cache import DiskCache
from automapgen.graph.catalog import NodeCatalog
from automapgen.graph.compiler import GraphCompiler
from automapgen.graph.document import GraphDocument, GraphFile
from automapgen.graph.engine import Engine, LoggingListener, PreviewWriter
from automapgen.graph.errors import AutoMapGenError, GraphError, InputError, Problem
from automapgen.graph.library import FolderLibrary
from automapgen.graph.node import Arity, OutputTarget
from automapgen.graph.paramsets import ParameterSet
from automapgen.graph.plan import ExecutionPlan
from automapgen.graph.render import MermaidDiagram
from automapgen.graphs import GRAPHS_DIR
from automapgen.nodes import BUILTIN_MODULES
from automapgen.nodes.gamefiles import GameUserData

PROJECTS = Path("configs") / "projects"
USER_GRAPHS = Path("configs") / "graphs"        # own graphs; searched before the built-in ones
REFERENCE = Path("docsAI") / "reference"


class Cli:
    def __init__(self, catalog: NodeCatalog, out: TextIO, err: TextIO, projects: Path = PROJECTS) -> None:
        self._catalog = catalog
        self._out = out
        self._err = err
        self._projects_folder = projects

    def main(self, argv: Sequence[str]) -> int:
        args = self._parser().parse_args(argv)
        try:
            return int(getattr(self, f"_{args.command}")(args))
        except AutoMapGenError as e:
            print(e, file=self._err)
            return 1

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="automapgen", description="tf3-AutoMapGen graph tools")
        commands = parser.add_subparsers(dest="command", required=True)
        commands.add_parser("nodes", help="list node types")
        commands.add_parser("schema", help="print the JSON schema of graph documents")
        commands.add_parser("projects", help="list the projects")
        commands.add_parser("reference", help="write the reference of node types and graphs").add_argument(
            "--out", type=Path, default=REFERENCE, help=f"folder (default: {REFERENCE})")
        commands.add_parser("gui", help="start the local browser GUI").add_argument(
            "--port", type=int, help="local port (default: configs/gui.toml)")
        for name, text in (("check", "compile a graph and list its problems"), ("show", "graph as Mermaid flowchart"),
                           ("run", "run a graph that has no inputs")):
            command = commands.add_parser(name, help=text)
            command.add_argument("graph", nargs="?" if name != "show" else None,
                                 help="a .graph.json file or a graph id (default: the graph of the parameter sets)")
            command.add_argument("--graphs", type=Path, action="append", default=[], help="folder with graphs")
            if name != "show":
                command.add_argument("--project", help="project id: the parameter set configs/projects/<id>.toml")
                command.add_argument("--set", type=Path, action="append", default=[], help="parameter set (TOML)")
            if name == "run":
                command.add_argument("--name", help="first part of every file name (default: map_name)")
                command.add_argument("--output", type=Path, help="output folder (default: output/NAME)")
                command.add_argument("--cache", type=Path, help="keep node results in this folder between runs")
                command.add_argument("--downloads", type=Path, default=Path("downloads"),
                                     help="folder for downloaded source data, e.g. OSM region files")
                command.add_argument("--game", type=Path, help="Transport Fever 3 installation "
                                                                       "(default: game_folder in local/settings.toml)")
                copies = command.add_mutually_exclusive_group()
                copies.add_argument("--user-data", type=Path, help="the game's user data folder that receives "
                                                                   "copies (default: user_data_folder in "
                                                                   "local/settings.toml)")
                copies.add_argument("--no-copy", action="store_true", help="copy nothing into the game")
                command.add_argument("--previews", type=Path, help="write a preview of every node output here")
        return parser

    def _nodes(self, args: argparse.Namespace) -> int:
        for spec in self._catalog.specs():
            ins = ", ".join(f"{p.name}: {p.type.name}{'' if p.arity is Arity.ONE else f' ({p.arity.value})'}"
                            for p in spec.inputs.values())
            outs = ", ".join(f"{p.name}: {p.type.name}" for p in spec.outputs.values())
            params = ", ".join(spec.params_model.model_fields)
            print(f"{spec.type_id} v{spec.version}  ({ins}) -> ({outs})  params: {params or '-'}", file=self._out)
            if spec.description:
                print(f"    {spec.description.splitlines()[0]}", file=self._out)
        return 0

    def _schema(self, args: argparse.Namespace) -> int:
        print(json.dumps(GraphFile.json_schema(), indent=2), file=self._out)
        return 0

    def _projects(self, args: argparse.Namespace) -> int:
        for path in sorted(self._projects_folder.glob("*.toml")):
            project = ParameterSet.load(path)
            print(f"{path.stem}  graph {project.graph or '-'}  map {project.params.get('map_name', '-')}",
                  file=self._out)
        return 0

    def _reference(self, args: argparse.Namespace) -> int:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in (("nodes.md", NodeReference(self._catalog).markdown()),
                           ("graphs.md", GraphReference(FolderLibrary([GRAPHS_DIR])).markdown())):
            (args.out / name).write_text(text, encoding="utf-8", newline="\n")
            print(f"written: {args.out / name}", file=self._out)
        return 0

    def _gui(self, args: argparse.Namespace) -> int:
        GuiServer(Path.cwd(), self._catalog, args.port).serve()
        return 0

    def _check(self, args: argparse.Namespace) -> int:
        plan, parameters = self._plan(args)
        self._project_checks(args, plan, parameters, LocalSettings.load(LOCAL_SETTINGS).game_folder)
        print(f"graph '{plan.graph_id}' is valid: {len(plan.nodes)} nodes, inputs {', '.join(plan.inputs) or '-'}, "
              f"outputs {', '.join(plan.outputs) or '-'}", file=self._out)
        return 0

    def _run(self, args: argparse.Namespace) -> int:
        plan, parameters = self._plan(args)
        if plan.inputs:
            raise InputError(f"graph '{plan.graph_id}' has inputs ({', '.join(plan.inputs)}); run it from a program")
        local = LocalSettings.load(LOCAL_SETTINGS)
        self._project_checks(args, plan, parameters, args.game or local.game_folder)
        user_data = None if args.no_copy else args.user_data or local.user_data_folder
        name = args.name if args.name is not None else str(parameters.params.get("map_name", ""))
        output = OutputTarget(args.output or Path("output") / (name or plan.graph_id), name,
                              GameUserData(user_data).destinations() if user_data else {})
        engine = Engine(DiskCache(args.cache) if args.cache else None, LoggingListener(),
                        PreviewWriter(args.previews) if args.previews else None, output, args.downloads,
                        args.game or local.game_folder, local.secrets)
        result = engine.run(plan, {})
        print(f"done: {len(result.executed)} nodes ran, {len(result.reused)} reused", file=self._out)
        return 0

    def _project_checks(self, args: argparse.Namespace, plan: ExecutionPlan, parameters: ParameterSet,
                        game: Path | None) -> None:
        """Raises for game materials the game does not have (checked when the game folder is known); prints the
        warnings of the given parameter files."""
        problems = ProjectChecks(lambda: game).problems(plan)
        if problems:
            raise GraphError(problems)
        for file in self._files(args):
            for warning in ProjectChecks.warnings(tomllib.loads(file.read_text(encoding="utf-8")), parameters.defaults):
                print(f"warning: {file.name}: {warning}", file=self._out)

    def _files(self, args: argparse.Namespace) -> list[Path]:
        """The parameter files of the command line: the project, then the --set files."""
        return ([self._projects_folder / f"{args.project}.toml"] if args.project else []) + list(args.set)

    def _plan(self, args: argparse.Namespace) -> tuple[ExecutionPlan, ParameterSet]:
        """The compiled graph and the merged parameter sets."""
        files = self._files(args)
        missing = [p for p in files if not p.is_file()]
        if missing:
            raise GraphError([Problem(str(p), "parameter set not found") for p in missing])
        sets = [ParameterSet.load(p) for p in files]
        graph = args.graph or next((s.graph for s in reversed(sets) if s.graph), None)
        if graph is None:
            raise GraphError([Problem("command line", "no graph given and the parameter sets name none")])
        document, library = self._load(graph, args.graphs)
        parameters = ParameterSet.layered(sets, document.id)
        return GraphCompiler(self._catalog, library).compile(document, parameters), parameters

    def _show(self, args: argparse.Namespace) -> int:
        document, library = self._load(args.graph, args.graphs)
        diagram = MermaidDiagram(lambda graph_id: (library.get(graph_id) or document).title)
        print(diagram.render(document), end="", file=self._out)
        return 0

    @staticmethod
    def _load(graph: str, folders: list[Path]) -> tuple[GraphDocument, FolderLibrary]:
        path = Path(graph)
        if path.suffix == ".json":
            if not path.is_file():
                raise GraphError([Problem(graph, "file not found")])
            return GraphFile.load(path), FolderLibrary([path.parent, *folders, USER_GRAPHS, GRAPHS_DIR])
        library = FolderLibrary([*folders, USER_GRAPHS, GRAPHS_DIR])
        document = library.get(graph)
        if document is None:
            raise GraphError([Problem(graph, "no such graph in the --graphs folders")])
        return document, library


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):   # Windows consoles default to a code page without many characters
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return Cli(NodeCatalog.discover(BUILTIN_MODULES), sys.stdout, sys.stderr).main(
        sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
