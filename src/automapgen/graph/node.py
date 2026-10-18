"""Nodes: small pure functions with typed inputs, outputs and parameters.

A node is a plain function marked with `@node`. Its signature is its contract:

    @node("mask.below", output="mask")
    def below(value: Field, params: BelowParams) -> Mask:
        \"\"\"Pixels whose value is below the limit.\"\"\"

- inputs are the arguments annotated with a data type: `T` (exactly one link), `T | None = None` (optional) or
  `tuple[T, ...]` (any number of links, in link order);
- an argument annotated with a `NodeParams` subclass receives the node's parameters;
- an argument annotated with `NodeContext` receives logging, progress and cancellation;
- the return annotation gives the outputs: one data type (named by `output`), a NamedTuple of data types (one output
  per field) or `None` (no outputs; only for nodes with side effects).

Nodes read no files, environment or globals and change none of their inputs. Only nodes declared with
`side_effects=True` (sinks: write files, copy to the game) touch the outside world; they always run and are never
cached. The function's docstring is the node's description.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Any

from automapgen.graph.data import DataType, DataTypes
from automapgen.graph.errors import NodeDefinitionError, RunCancelledError
from automapgen.graph.params import NodeParams, NoParams

type JsonParams = dict[str, Any]
type Migration = Callable[[JsonParams], JsonParams]

TYPE_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
PORT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_DECLARED = "__automapgen_node__"


class Arity(Enum):
    """How many links an input takes."""

    ONE = "one"
    OPTIONAL = "optional"
    MANY = "many"


class OutputStyle(Enum):
    """How the function returns its outputs."""

    SINGLE = "single"   # the value of the only output
    NAMED = "named"     # a NamedTuple, one field per output
    NONE = "none"       # nothing (side-effect nodes)


@dataclass(frozen=True)
class InputPort:
    name: str
    type: DataType[Any]
    arity: Arity


@dataclass(frozen=True)
class OutputPort:
    name: str
    type: DataType[Any]


@dataclass(frozen=True)
class OutputTarget:
    """Where the sinks of a run write: a folder and a prefix for every file name (e.g. the project name), and named
    destinations that receive copies of chosen files (e.g. the import folders of an application).

    A setting of the run, not of the graph: the same graph writes different maps into different folders. The graph
    names the destination of a file; the run says which folder it is or leaves it out (no copy).
    """

    folder: Path
    prefix: str = ""
    destinations: Mapping[str, Path] = field(default_factory=dict)

    def file(self, name: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder / f"{self.prefix}{name}"

    def path(self, relative: str) -> Path:
        """A path below the folder without the prefix, e.g. a folder of several files."""
        return self.folder / relative

    def destination(self, name: str) -> Path | None:
        """The folder of a named destination (created when needed); None if the run has none of that name."""
        folder = self.destinations.get(name)
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
        return folder


class NodeContext:
    """What a running node may use besides inputs and parameters. Created by the engine for every node run."""

    def __init__(self, node_id: str, log: Callable[[str], None], progress: Callable[[float], None],
                 cancelled: Callable[[], bool], output: OutputTarget | None = None,
                 downloads: Path | None = None, game: Path | None = None,
                 secrets: Mapping[str, str] | None = None) -> None:
        self.node_id = node_id
        self._log = log
        self._progress = progress
        self._cancelled = cancelled
        self._output = output
        self._downloads = downloads
        self._game = game
        self._secrets = dict(secrets or {})

    def log(self, message: str) -> None:
        self._log(message)

    def progress(self, fraction: float) -> None:
        """Reports progress of a long node, 0..1."""
        self._progress(min(1.0, max(0.0, fraction)))

    def check_cancelled(self) -> None:
        """Long nodes call this now and then; raises if the run was cancelled."""
        if self._cancelled():
            raise RunCancelledError(f"cancelled in node '{self.node_id}'")

    def output_file(self, name: str) -> Path:
        """Path of an output file of this run (sinks only): output folder / prefix + name."""
        if self._output is None:
            raise RuntimeError("this run has no output folder (Engine(output=...))")
        return self._output.file(name)

    def output_path(self, relative: str) -> Path:
        """A path below the run's output folder without the prefix (sinks only), e.g. `mods/<mod id>`."""
        if self._output is None:
            raise RuntimeError("this run has no output folder (Engine(output=...))")
        return self._output.path(relative)

    def copy_destination(self, name: str) -> Path | None:
        """The folder of a named destination of the run that receives copies of written files (sinks only); None if
        the run has none of that name (then nothing is copied)."""
        if self._output is None:
            raise RuntimeError("this run has no output folder (Engine(output=...))")
        return self._output.destination(name)

    def download_folder(self) -> Path:
        """Folder for downloaded source data kept between runs (source nodes only), e.g. OSM region files."""
        if self._downloads is None:
            raise RuntimeError("this run has no download folder (Engine(downloads=...))")
        self._downloads.mkdir(parents=True, exist_ok=True)
        return self._downloads

    def game_folder(self) -> Path:
        """The Transport Fever 3 installation (or a local copy of its files) - source nodes that read game files
        only; never part of the repository (ADR 0005)."""
        if self._game is None:
            raise RuntimeError("this run has no game folder (Engine(game=...), CLI --game or local/settings.toml)")
        return self._game

    def secret(self, name: str) -> str:
        """A named secret of this computer, e.g. an API key (source nodes only); never a parameter, so it is neither
        stored in graphs or parameter sets nor part of cache keys."""
        value = self._secrets.get(name)
        if not value:
            raise RuntimeError(f"this run has no secret '{name}' (local/settings.toml, table [secrets])")
        return value


@dataclass(frozen=True)
class NodeDeclaration:
    """What `@node` attaches to a function; turned into a `NodeSpec` once the catalog knows the data types."""

    type_id: str
    version: int
    output: str
    side_effects: bool
    migrations: Mapping[int, Migration]
    func: Callable[..., Any] = field(compare=False)


@dataclass(frozen=True)
class _NodeDecorator:
    type_id: str
    version: int
    output: str
    side_effects: bool
    migrations: Mapping[int, Migration]

    def __call__[F: Callable[..., Any]](self, func: F) -> F:
        setattr(func, _DECLARED, NodeDeclaration(self.type_id, self.version, self.output, self.side_effects,
                                                 self.migrations, func))
        return func


def node(type_id: str, *, version: int = 1, output: str = "out", side_effects: bool = False,
         migrations: Mapping[int, Migration] | None = None) -> _NodeDecorator:
    """Function decorator: declares the function as node type `type_id` (`category.name`).

    `version` is raised when the parameters change incompatibly; `migrations[v]` turns stored parameters of version v
    into those of version v + 1, so saved graphs keep working.
    """
    return _NodeDecorator(type_id, version, output, side_effects, dict(migrations or {}))


def declared_node(func: object) -> NodeDeclaration | None:
    declared = getattr(func, _DECLARED, None)
    return declared if isinstance(declared, NodeDeclaration) and declared.func is func else None


@dataclass(frozen=True)
class NodeSpec:
    """A node type as the compiler and engine see it: ports, parameters and the function."""

    type_id: str
    version: int
    description: str
    inputs: Mapping[str, InputPort]
    outputs: Mapping[str, OutputPort]
    params_model: type[NodeParams]
    side_effects: bool
    migrations: Mapping[int, Migration]
    func: Callable[..., Any] = field(compare=False)
    params_arg: str | None = None
    context_arg: str | None = None
    output_style: OutputStyle = OutputStyle.SINGLE

    @staticmethod
    def of(declaration: NodeDeclaration, data_types: DataTypes) -> NodeSpec:
        return _SignatureReader(declaration, data_types).spec()

    @cached_property
    def code_hash(self) -> str:
        """Hash of the function's source - part of cache keys, so editing a node invalidates its cached results."""
        try:
            source = inspect.getsource(self.func)
        except (OSError, TypeError):
            source = self.func.__qualname__
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def run(self, inputs: Mapping[str, object], params: NodeParams, context: NodeContext) -> dict[str, object]:
        """Calls the function and returns its outputs by name; checks that they have the declared types."""
        kwargs: dict[str, object] = dict(inputs)
        if self.params_arg:
            kwargs[self.params_arg] = params
        if self.context_arg:
            kwargs[self.context_arg] = context
        result = self.func(**kwargs)
        if self.output_style is OutputStyle.NONE:
            outputs: dict[str, object] = {}
        elif self.output_style is OutputStyle.SINGLE:
            outputs = {next(iter(self.outputs)): result}
        else:
            outputs = dict(result._asdict())
        for name, port in self.outputs.items():
            if not port.type.accepts(outputs.get(name)):
                raise TypeError(f"output '{name}' should be {port.type.name}, got {type(outputs.get(name)).__name__}")
        return outputs


class _SignatureReader:
    """Reads a node function's signature into a NodeSpec; every mistake is a NodeDefinitionError naming the node."""

    def __init__(self, declaration: NodeDeclaration, data_types: DataTypes) -> None:
        self._d = declaration
        self._types = data_types

    def spec(self) -> NodeSpec:
        d = self._d
        if not TYPE_ID.match(d.type_id):
            self._fail("type id must look like 'category.name' (lower case, digits, underscores)")
        if d.version < 1 or any(not 1 <= v < d.version for v in d.migrations):
            self._fail("version must be >= 1 and migrations only from older versions")
        try:
            hints = typing.get_type_hints(d.func)
        except NameError as e:
            raise NodeDefinitionError(f"node '{d.type_id}': cannot resolve annotation ({e})") from e
        inputs: dict[str, InputPort] = {}
        params_arg = context_arg = None
        params_model: type[NodeParams] = NoParams
        for name, parameter in inspect.signature(d.func).parameters.items():
            annotation = hints.get(name)
            if isinstance(annotation, type) and issubclass(annotation, NodeParams):
                params_arg, params_model = name, annotation
            elif annotation is NodeContext:
                context_arg = name
            else:
                inputs[name] = self._input(name, annotation, parameter)
        if "return" not in hints:
            self._fail("needs a return annotation (a data type, a NamedTuple of data types or None)")
        outputs, style = self._outputs(hints["return"])
        if style is OutputStyle.NONE and not d.side_effects:
            self._fail("a node without outputs makes sense only with side_effects=True")
        return NodeSpec(d.type_id, d.version, inspect.getdoc(d.func) or "", inputs, outputs, params_model,
                        d.side_effects, d.migrations, d.func, params_arg, context_arg, style)

    def _input(self, name: str, annotation: object, parameter: inspect.Parameter) -> InputPort:
        if not PORT_NAME.match(name):
            self._fail(f"input name '{name}' must be lower case with underscores")
        args = typing.get_args(annotation)
        if typing.get_origin(annotation) is tuple and len(args) == 2 and args[1] is Ellipsis:
            return InputPort(name, self._type(args[0], name), Arity.MANY)
        if isinstance(annotation, types.UnionType) and len(args) == 2 and type(None) in args:
            if parameter.default is not None:
                self._fail(f"optional input '{name}' needs the default None")
            inner = args[0] if args[1] is type(None) else args[1]
            return InputPort(name, self._type(inner, name), Arity.OPTIONAL)
        return InputPort(name, self._type(annotation, name), Arity.ONE)

    def _outputs(self, annotation: object) -> tuple[dict[str, OutputPort], OutputStyle]:
        if annotation is None or annotation is type(None):
            return {}, OutputStyle.NONE
        if isinstance(annotation, type) and issubclass(annotation, tuple) and hasattr(annotation, "_fields"):
            fields = typing.get_type_hints(annotation)
            return {n: OutputPort(n, self._type(t, n)) for n, t in fields.items()}, OutputStyle.NAMED
        if not PORT_NAME.match(self._d.output):
            self._fail(f"output name '{self._d.output}' must be lower case with underscores")
        return {self._d.output: OutputPort(self._d.output, self._type(annotation, "return"))}, OutputStyle.SINGLE

    def _type(self, annotation: object, where: str) -> DataType[Any]:
        found = self._types.of_class(annotation)
        if found is None:
            self._fail(f"'{where}' is annotated with {annotation!r}, which is no known data type")
        return found

    def _fail(self, message: str) -> typing.NoReturn:
        raise NodeDefinitionError(f"node '{self._d.type_id}' ({self._d.func.__qualname__}): {message}")

