"""Node results kept between runs, keyed by their origin.

The key of an output depends only on where it comes from: node type, version, code hash, parameters and the keys of
its inputs; graph inputs by the fingerprint of their value. Keys are known before anything runs, so the engine can skip
every node whose results are cached - and everything only they need.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from automapgen.graph.data import DataType
from automapgen.graph.plan import ExecutionPlan, PortRef


class NodeCache(Protocol):
    def contains(self, key: str, data_type: DataType[Any]) -> bool: ...

    def load(self, key: str, data_type: DataType[Any]) -> object: ...

    def store(self, key: str, data_type: DataType[Any], value: object) -> None: ...


class NoCache:
    """Keeps nothing: every node runs."""

    def contains(self, key: str, data_type: DataType[Any]) -> bool:
        return False

    def load(self, key: str, data_type: DataType[Any]) -> object:
        raise KeyError(key)

    def store(self, key: str, data_type: DataType[Any], value: object) -> None:
        pass


class MemoryCache:
    """Keeps results in memory (tests, repeated runs in one GUI session)."""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def contains(self, key: str, data_type: DataType[Any]) -> bool:
        return key in self._values

    def load(self, key: str, data_type: DataType[Any]) -> object:
        return self._values[key]

    def store(self, key: str, data_type: DataType[Any], value: object) -> None:
        self._values[key] = value


class DiskCache:
    """Keeps results of data types with a codec in a folder (`<key[:2]>/<key><suffix>`); others are not kept."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def contains(self, key: str, data_type: DataType[Any]) -> bool:
        return data_type.codec is not None and self._path(key, data_type).is_file()

    def load(self, key: str, data_type: DataType[Any]) -> object:
        assert data_type.codec is not None
        return data_type.codec.load(self._path(key, data_type))

    def store(self, key: str, data_type: DataType[Any], value: object) -> None:
        if data_type.codec is None:
            return
        path = self._path(key, data_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(f"{path.stem}.partial{path.suffix}")
        data_type.codec.save(value, partial)
        os.replace(partial, path)   # a cancelled write never leaves a half file under the real key

    def _path(self, key: str, data_type: DataType[Any]) -> Path:
        assert data_type.codec is not None
        return self._folder / key[:2] / f"{key}{data_type.codec.suffix}"


class LineageKeys:
    """Cache keys of every output of a plan."""

    @staticmethod
    def of(plan: ExecutionPlan, inputs: Mapping[str, object]) -> dict[PortRef, str]:
        keys: dict[PortRef, str] = {}
        for name, data_type in plan.inputs.items():
            value = inputs[name]
            # without a fingerprint the value cannot be recognised again: a fresh key, nothing downstream is reused
            fingerprint = data_type.fingerprint(value) if data_type.fingerprint else uuid.uuid4().hex
            keys[PortRef("inputs", name)] = _hash({"input": data_type.name, "value": fingerprint})
        for node in plan.nodes:
            node_key = _hash({
                "type": node.spec.type_id, "version": node.spec.version, "code": node.spec.code_hash,
                "params": node.params.model_dump(mode="json"),
                "inputs": {port: [keys[ref] for ref in refs] for port, refs in node.inputs.items()},
            })
            for output in node.spec.outputs:
                keys[PortRef(node.id, output)] = _hash({"node": node_key, "output": output})
        return keys


def _hash(content: object) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
