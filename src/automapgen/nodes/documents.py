"""JSON documents: plans and grids that are plain data (river plan, farm grid, path plan ...).

Each kind is its own data type (ports stay specific), derived from `json_document`, so one writer takes all of them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automapgen.graph import data_type


class JsonDocumentCodec:
    """Stores a document's data as JSON; `make` builds the document of the right kind again."""

    suffix = ".json"

    def __init__(self, make: Callable[[Any], Any]) -> None:
        self._make = make

    def save(self, value: JsonDocument, path: Path) -> None:
        path.write_text(json.dumps(value.data, ensure_ascii=False), encoding="utf-8")

    def load(self, path: Path) -> Any:
        return self._make(json.loads(path.read_text(encoding="utf-8")))


@data_type("json_document", "Plain data that can be written as JSON.",
           codec=JsonDocumentCodec(lambda data: JsonDocument(data)), preview=lambda d: json.dumps(d.data)[:2000])
@dataclass(frozen=True)
class JsonDocument:
    """Plain data. It is normalised to what JSON gives back (Python numbers instead of NumPy scalars, lists instead of
    tuples), so a document behaves the same fresh and after the cache or a file: NumPy 2 computes `float32 array -
    np.float64` in float64 but `float32 array - float` in float32."""

    data: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", json.loads(json.dumps(self.data, default=_plain)))


def _plain(value: Any) -> Any:
    """NumPy scalars and arrays as Python numbers and lists."""
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON data")
