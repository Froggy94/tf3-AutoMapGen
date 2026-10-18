"""Data types: the typed values that flow between nodes.

A data type is a Python class plus what the engine needs to handle its values without knowing them: a name for graph
documents, and optionally a codec (disk cache), a preview (debugging, editor) and a fingerprint (cache keys of graph
inputs). Values are immutable by contract - nodes never change their inputs. A data type whose class derives from
another data type's class fits that type's ports (e.g. every JSON document fits a JSON writer).

Own classes declare themselves with `@data_type(...)`; numbers, integers, texts and flags are built in.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from automapgen.graph.errors import NodeDefinitionError

type Preview = Image.Image | str
"""A picture (written as PNG) or a text (written as .txt)."""

_DECLARED = "__automapgen_data_type__"


class Codec[T](Protocol):
    """Stores values of one data type in a file (disk cache)."""

    @property
    def suffix(self) -> str: ...

    def save(self, value: T, path: Path) -> None: ...

    def load(self, path: Path) -> T: ...


@dataclass(frozen=True)
class DataType[T]:
    """A data type: its name in graph documents, its class, and how the engine may store, show and identify values."""

    name: str
    cls: type[T]
    description: str
    codec: Codec[T] | None = None
    preview: Callable[[T], Preview] | None = None
    fingerprint: Callable[[T], str] | None = None

    def accepts(self, value: object) -> bool:
        return isinstance(value, self.cls)

    def fits(self, target: DataType[Any]) -> bool:
        """Whether values of this type may flow into a port of `target`: the same type or a subtype (a river plan is a
        JSON document, so a JSON writer takes it)."""
        return self is target or issubclass(self.cls, target.cls)


@dataclass(frozen=True)
class _Declaration:
    name: str
    description: str
    codec: Codec[Any] | None
    preview: Callable[[Any], Preview] | None
    fingerprint: Callable[[Any], str] | None

    def __call__[C: type[Any]](self, cls: C) -> C:
        setattr(cls, _DECLARED, DataType(self.name, cls, self.description, self.codec, self.preview, self.fingerprint))
        return cls


def data_type(name: str, description: str, *, codec: Codec[Any] | None = None,
              preview: Callable[[Any], Preview] | None = None,
              fingerprint: Callable[[Any], str] | None = None) -> _Declaration:
    """Class decorator: declares the class as a data type with this name."""
    return _Declaration(name, description, codec, preview, fingerprint)


def declared_data_type(cls: object) -> DataType[Any] | None:
    """The data type a class declared itself (not one inherited from a base class)."""
    declared = getattr(cls, _DECLARED, None)
    return declared if isinstance(declared, DataType) and declared.cls is cls else None


class JsonCodec:
    """Codec for JSON-compatible values."""

    suffix = ".json"

    def save(self, value: Any, path: Path) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def load(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))


SCALARS: tuple[DataType[Any], ...] = (
    DataType("number", float, "A number.", JsonCodec(), str, json.dumps),
    DataType("integer", int, "A whole number.", JsonCodec(), str, json.dumps),
    DataType("text", str, "A text.", JsonCodec(), str, json.dumps),
    DataType("flag", bool, "Yes or no.", JsonCodec(), str, json.dumps),
)


class DataTypes:
    """Catalog of the known data types: by class (node signatures) and by name (graph documents)."""

    def __init__(self, types: Iterable[DataType[Any]]) -> None:
        self._by_name: dict[str, DataType[Any]] = {}
        self._by_class: dict[type[Any], DataType[Any]] = {}
        for t in types:
            if t.name in self._by_name or t.cls in self._by_class:
                raise NodeDefinitionError(f"data type '{t.name}' ({t.cls.__qualname__}) is declared twice")
            self._by_name[t.name] = t
            self._by_class[t.cls] = t

    def of_class(self, cls: object) -> DataType[Any] | None:
        return self._by_class.get(cls) if isinstance(cls, type) else None

    def named(self, name: str) -> DataType[Any] | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name)
