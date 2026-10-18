"""Elevation data from OpenTopography: download once into the download folder, read like a file (no network: the
HTTP answer is faked)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
import requests
from rasterio.transform import from_bounds

from automapgen.graph import NodeContext
from automapgen.nodes import elevation
from automapgen.nodes.elevation import GeoBounds, ReadParams
from automapgen.nodes.opentopography import DemDownload

CROP = GeoBounds(west=11.0, south=47.0, east=11.1, north=47.1)


def _geotiff() -> bytes:
    buffer = io.BytesIO()
    with rasterio.open(buffer, "w", driver="GTiff", width=20, height=20, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_bounds(10.9, 46.9, 11.2, 47.2, 20, 20)) as dst:
        dst.write(np.full((1, 20, 20), 700, dtype=np.float32))
    return buffer.getvalue()


class _Answer:
    def __init__(self, status: int, body: bytes) -> None:
        self.status_code = status
        self.text = body.decode("latin-1")
        self._body = body

    def iter_content(self, size: int) -> Iterator[bytes]:
        yield self._body

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *args: object) -> None:
        pass


@pytest.fixture
def requests_seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def get(url: str, params: dict[str, Any], **kwargs: Any) -> _Answer:
        seen.append(params)
        return _Answer(200, _geotiff())

    monkeypatch.setattr(requests, "get", get)
    return seen


def _context(downloads: Path, secrets: dict[str, str] | None = None) -> NodeContext:
    return NodeContext("dem", lambda m: None, lambda f: None, lambda: False, downloads=downloads,
                       secrets={"opentopography_api_key": "KEY"} if secrets is None else secrets)


def test_crop_is_downloaded_once(tmp_path: Path, requests_seen: list[dict[str, Any]]) -> None:
    params = ReadParams(crop=CROP, download=DemDownload())

    first = elevation.read(params, _context(tmp_path))
    elevation.read(params, _context(tmp_path))

    assert len(requests_seen) == 1
    assert requests_seen[0]["API_Key"] == "KEY"
    assert requests_seen[0]["demtype"] == "GEDTM30"
    assert requests_seen[0]["west"] < CROP.west     # with margin
    assert requests_seen[0]["north"] > CROP.north
    assert float(np.nanmax(first.heights)) == 700
    assert [p.name for p in (tmp_path / "elevation").iterdir()] == [
        "GEDTM30_11.00000_47.00000_11.10000_47.10000.tif"]


def test_download_needs_the_key_and_a_good_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    params = ReadParams(crop=CROP, download=DemDownload())
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Answer(401, b"Invalid API key"))

    with pytest.raises(RuntimeError, match="no secret 'opentopography_api_key'"):
        elevation.read(params, _context(tmp_path, {}))
    with pytest.raises(ValueError, match="answers 401: Invalid API key"):
        elevation.read(params, _context(tmp_path))
    with pytest.raises(ValueError, match="no elevation model"):
        elevation.read(ReadParams(crop=CROP), _context(tmp_path))
