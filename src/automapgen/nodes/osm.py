"""OpenStreetMap from Geofabrik region files: choose and download the regions covering the map, read features.

Data (c) OpenStreetMap contributors, ODbL 1.0. Region files are kept in the run's download folder; read results are
cached by the engine like any node result.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import osmium
import requests
from PIL import Image, ImageDraw

from automapgen.graph import NodeContext, NodeParams, data_type, node
from automapgen.nodes.maps import MapFrame, MapProjection

type BBox = tuple[float, float, float, float]
"""(lon0, lat0, lon1, lat1) in WGS84 degrees."""
type _GeoRing = Sequence[Sequence[float]]
type _GeoPolygon = Sequence[_GeoRing]


class GeofabrikIndex:
    """Chooses the smallest Geofabrik regions covering a crop and downloads missing region files."""

    INDEX_URL = "https://download.geofabrik.de/index-v1.json"
    INDEX_MAX_AGE_DAYS = 30
    COVER_GRID = 200                 # cells per side of the crop for the coverage check
    COVER_MARGIN_DEG = 0.02          # crop slightly larger for the region choice
    USER_AGENT = "tf3-AutoMapGen/0.1"
    _DOWNLOAD_REPORT_BYTES = 50 << 20

    def __init__(self, folder: Path, context: NodeContext) -> None:
        self._folder = folder
        self._context = context

    def pbf_files(self, bbox: BBox) -> list[Path]:
        """Region files for the crop (missing ones are downloaded)."""
        paths = []
        for _, name, url in self.regions_for_bbox(bbox):
            path = self._folder / os.path.basename(url)
            if not path.exists():
                self._download(name, url, path)
            paths.append(path)
        self._context.log("Geofabrik regions: " + ", ".join(p.name.replace("-latest.osm.pbf", "") for p in paths))
        return paths

    def regions_for_bbox(self, bbox: BBox) -> list[tuple[str, str, str]]:
        """Smallest regions (lowest level) covering the crop: [(id, name, pbf URL)]."""
        lon0, lat0, lon1, lat1 = bbox
        m = self.COVER_MARGIN_DEG
        box = (lon0 - m, lat0 - m, lon1 + m, lat1 + m)
        features = self._index()
        parents = {f["properties"].get("parent") for f in features}
        leaves = []
        for f in features:
            p = f["properties"]
            if p["id"] in parents or "pbf" not in p.get("urls", {}):
                continue
            polys = self._polygons(f.get("geometry"))
            xs = [x for poly in polys for x, _ in poly[0]]
            ys = [y for poly in polys for _, y in poly[0]]
            if not xs or max(xs) < box[0] or min(xs) > box[2] or max(ys) < box[1] or min(ys) > box[3]:
                continue
            leaves.append((self._area(polys), p["id"], p.get("name", p["id"]), p["urls"]["pbf"], polys))
        n = self.COVER_GRID
        uncovered = Image.new("1", (n, n), 1)
        chosen = []
        for _, rid, name, url, polys in sorted(leaves):
            mask = self._raster(polys, box, n)
            hit = Image.composite(mask, Image.new("1", (n, n), 0), uncovered)   # mask & uncovered
            if hit.getbbox() is None:
                continue
            chosen.append((rid, name, url))
            uncovered = Image.composite(Image.new("1", (n, n), 0), uncovered, mask)
            if uncovered.getbbox() is None:
                break
        rest = np.count_nonzero(np.asarray(uncovered)) / (n * n)
        if rest > 0.01:
            self._context.log(f"note: {rest * 100:.0f} % of the crop in no Geofabrik region (sea/abroad?)")
        return chosen

    def _index(self) -> list[dict[str, Any]]:
        path = self._folder / "geofabrik_index-v1.json"
        if not path.exists() or time.time() - path.stat().st_mtime > self.INDEX_MAX_AGE_DAYS * 86400:
            self._context.log(f"loading the Geofabrik index {self.INDEX_URL} ...")
            r = requests.get(self.INDEX_URL, timeout=120, headers={"User-Agent": self.USER_AGENT})
            r.raise_for_status()
            path.write_bytes(r.content)
        features: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["features"]
        return features

    def _download(self, name: str, url: str, path: Path) -> None:
        self._context.log(f"loading {name} ({url}) ...")
        part = path.with_name(path.name + ".part")
        with requests.get(url, stream=True, timeout=(30, 600), headers={"User-Agent": self.USER_AGENT}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            n, last = 0, 0
            with part.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    n += len(chunk)
                    if n - last >= self._DOWNLOAD_REPORT_BYTES:
                        self._context.log(f"  {n / 1e6:.0f} / {total / 1e6:.0f} MB")
                        self._context.check_cancelled()
                        last = n
        os.replace(part, path)

    @staticmethod
    def _polygons(geom: dict[str, Any] | None) -> list[_GeoPolygon]:
        if not geom:
            return []
        if geom["type"] == "Polygon":
            return [geom["coordinates"]]
        return list(geom["coordinates"]) if geom["type"] == "MultiPolygon" else []

    @staticmethod
    def _area(polys: Sequence[_GeoPolygon]) -> float:
        """Approximate area (degrees², with cos(latitude)) of the outer rings - only for sorting."""
        a = 0.0
        for poly in polys:
            ring = list(poly[0])
            lat = sum(p[1] for p in ring) / len(ring)
            s = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True))
            a += abs(s) / 2 * math.cos(math.radians(lat))
        return a

    @staticmethod
    def _raster(polys: Sequence[_GeoPolygon], bbox: BBox, n: int) -> Image.Image:
        lon0, lat0, lon1, lat1 = bbox
        img = Image.new("1", (n, n), 0)
        d = ImageDraw.Draw(img)
        for poly in polys:
            for k, ring in enumerate(poly):
                pts = [((x - lon0) / (lon1 - lon0) * n, (lat1 - y) / (lat1 - lat0) * n) for x, y in ring]
                if len(pts) >= 3:
                    d.polygon(pts, fill=0 if k else 1)
        return img


@dataclass(frozen=True)
class Way:
    """An OSM way: id, name (or its relation's name), kind, CEMT class (`CEMT` tag, inland waterway class) and its
    nodes (ref, lon, lat)."""

    id: int
    name: str
    kind: str
    cemt: str
    nodes: tuple[tuple[int, float, float], ...]


class _WaysCodec:
    suffix = ".json"

    def save(self, value: OsmWays, path: Path) -> None:
        path.write_text(json.dumps([[w.id, w.name, w.kind, w.cemt, w.nodes] for w in value.ways]),
                        encoding="utf-8")

    def load(self, path: Path) -> OsmWays:
        data = json.loads(path.read_text(encoding="utf-8"))
        return OsmWays(tuple(Way(i, n, k, c, tuple((r, x, y) for r, x, y in pts))
                             for i, n, k, c, pts in data))


@data_type("osm_ways", "OpenStreetMap ways (lines) of one kind touching the map.", codec=_WaysCodec(),
           preview=lambda w: f"{len(w.ways)} ways: " + ", ".join(sorted({x.name for x in w.ways if x.name})))
@dataclass(frozen=True)
class OsmWays:
    ways: tuple[Way, ...]


class WaterwayParams(NodeParams):
    types: tuple[str, ...] = ("river", "canal")
    """OSM waterway values that count."""
    underground: tuple[str, ...] = ("underground", "underwater")
    """OSM location values left out (tunnel=* and covered=yes are always left out)."""


@node("osm.waterways", output="ways")
def waterways(frame: MapFrame, params: WaterwayParams, context: NodeContext) -> OsmWays:
    """Waterways touching the map, without tunnels and culverts; unnamed ways get the name of their river relation."""
    bbox = MapProjection(frame).wgs84_box()
    lon0, lat0, lon1, lat1 = bbox
    ways: list[Way] = []
    relation_name: dict[int, str] = {}
    for path in GeofabrikIndex(context.download_folder(), context).pbf_files(bbox):
        context.log(f"reading {path.name} (a few minutes) ...")
        fp: Any = osmium.FileProcessor(str(path)).with_locations().with_filter(
            osmium.filter.TagFilter(*(("waterway", t) for t in params.types)))   # osmium's union types: Any
        for obj in fp:
            if obj.is_relation():                        # river relation: name for unnamed member ways
                if obj.tags.get("name"):
                    for member in obj.members:
                        if member.type == "w":
                            relation_name.setdefault(member.ref, obj.tags.get("name"))
                continue
            if not obj.is_way():
                continue
            pts = tuple((nd.ref, nd.lon, nd.lat) for nd in obj.nodes if nd.location.valid())
            if not any(lon0 <= x <= lon1 and lat0 <= y <= lat1 for _, x, y in pts):
                continue
            if (obj.tags.get("tunnel", "no") != "no" or obj.tags.get("location") in params.underground
                    or obj.tags.get("covered") == "yes"):   # underground (gallery, culvert): no surface river
                continue
            ways.append(Way(obj.id, obj.tags.get("name", ""), obj.tags.get("waterway"),
                            obj.tags.get("CEMT", ""), pts))
    named = tuple(w if w.name else Way(w.id, relation_name.get(w.id, ""), w.kind, w.cemt, w.nodes)
                  for w in ways)
    context.log(f"{len(named)} waterway sections ({', '.join(params.types)})")
    return OsmWays(named)


type Ring = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Area:
    """An OSM area (closed way or multipolygon part): tags, outer rings and inner rings as (lon, lat)."""

    tags: dict[str, str]
    outer: tuple[Ring, ...]
    inner: tuple[Ring, ...]


class _AreasCodec:
    suffix = ".json"

    def save(self, value: OsmAreas, path: Path) -> None:
        path.write_text(json.dumps([[a.tags, a.outer, a.inner] for a in value.areas]), encoding="utf-8")

    def load(self, path: Path) -> OsmAreas:
        def rings(data: list[list[list[float]]]) -> tuple[Ring, ...]:
            return tuple(tuple((x, y) for x, y in ring) for ring in data)

        return OsmAreas(tuple(Area(tags, rings(outer), rings(inner))
                              for tags, outer, inner in json.loads(path.read_text(encoding="utf-8"))))


@data_type("osm_areas", "OpenStreetMap areas with certain tags inside the map.", codec=_AreasCodec(),
           preview=lambda a: f"{len(a.areas)} areas")
@dataclass(frozen=True)
class OsmAreas:
    areas: tuple[Area, ...]


class AreaParams(NodeParams):
    tags: tuple[tuple[str, str], ...] = (("landuse", "farmland"),)
    """(key, value) pairs; an area with one of them counts."""


@node("osm.areas", output="areas")
def areas(frame: MapFrame, params: AreaParams, context: NodeContext) -> OsmAreas:
    """Areas (closed ways and multipolygons) with one of the tags inside the map, e.g. fields."""
    bbox = MapProjection(frame).wgs84_box()
    tag_pairs = list(params.tags)
    out: dict[tuple[Any, ...], Area] = {}
    for path in GeofabrikIndex(context.download_folder(), context).pbf_files(bbox):
        context.log(f"reading areas from {path.name} (assembling areas, a few minutes) ...")
        found: Any = (osmium.FileProcessor(str(path)).with_areas(osmium.filter.TagFilter(*tag_pairs))
                      .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA)))
        for a in found:
            # the filter in with_areas does not apply to all closed ways -> check the tags here
            if not a.is_area() or not any(a.tags.get(k) == v for k, v in tag_pairs):
                continue
            tags = None
            for k, outer in enumerate(a.outer_rings()):
                ring = tuple((n.lon, n.lat) for n in outer)
                xs, ys = [q[0] for q in ring], [q[1] for q in ring]
                if max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3]:
                    continue
                if tags is None:
                    tags = {t.k: t.v for t in a.tags}
                inner = tuple(tuple((n.lon, n.lat) for n in r) for r in a.inner_rings(outer))
                out[(a.from_way(), a.orig_id(), k)] = Area(tags, (ring,), inner)
            context.check_cancelled()
        context.log(f"{len(out)} areas so far")
    return OsmAreas(tuple(out.values()))



@dataclass(frozen=True)
class OsmLine:
    """An OSM way with all its tags and node ids."""

    id: int
    tags: dict[str, str]
    nodes: tuple[int, ...]


class _LinesCodec:
    suffix = ".json"

    def save(self, value: OsmLines, path: Path) -> None:
        data = {"lines": [[w.id, w.tags, w.nodes] for w in value.lines],
                "points": [[k, x, y] for k, (x, y) in value.points.items()]}
        path.write_text(json.dumps(data), encoding="utf-8")

    def load(self, path: Path) -> OsmLines:
        data = json.loads(path.read_text(encoding="utf-8"))
        return OsmLines(tuple(OsmLine(i, tags, tuple(nodes)) for i, tags, nodes in data["lines"]),
                        {k: (x, y) for k, x, y in data["points"]})


@data_type("osm_lines", "OpenStreetMap ways with their tags and the positions (lon, lat) of their nodes.",
           codec=_LinesCodec(), preview=lambda w: f"{len(w.lines)} ways, {len(w.points)} points")
@dataclass(frozen=True)
class OsmLines:
    lines: tuple[OsmLine, ...]
    points: dict[int, tuple[float, float]]


class HighwayParams(NodeParams):
    types: tuple[str, ...] = ("track",)
    """OSM highway values to read."""


@node("osm.highways", output="lines")
def highways(frame: MapFrame, params: HighwayParams, context: NodeContext) -> OsmLines:
    """Highways of the given types touching the map, with all tags."""
    bbox = MapProjection(frame).wgs84_box()
    lon0, lat0, lon1, lat1 = bbox
    points: dict[int, tuple[float, float]] = {}
    lines: dict[int, OsmLine] = {}
    for path in GeofabrikIndex(context.download_folder(), context).pbf_files(bbox):
        context.log(f"reading {path.name} (a few minutes) ...")
        fp: Any = osmium.FileProcessor(str(path)).with_locations().with_filter(
            osmium.filter.TagFilter(*(("highway", t) for t in params.types)))
        for way in fp:
            if not way.is_way():
                continue
            pts = [(nd.ref, nd.lon, nd.lat) for nd in way.nodes if nd.location.valid()]
            if not any(lon0 <= x <= lon1 and lat0 <= y <= lat1 for _, x, y in pts):
                continue
            lines[way.id] = OsmLine(way.id, {t.k: t.v for t in way.tags}, tuple(r for r, _, _ in pts))
            for r, x, y in pts:
                points[r] = (x, y)
        context.log(f"{len(lines)} ways so far")
    return OsmLines(tuple(lines.values()), points)


@dataclass(frozen=True)
class OsmPoint:
    """An OSM node with tags (a place, a peak ...)."""

    id: int
    lon: float
    lat: float
    tags: dict[str, str]


class _PointsCodec:
    suffix = ".json"

    def save(self, value: OsmPoints, path: Path) -> None:
        path.write_text(json.dumps([[p.id, p.lon, p.lat, p.tags] for p in value.points]), encoding="utf-8")

    def load(self, path: Path) -> OsmPoints:
        return OsmPoints(tuple(OsmPoint(i, x, y, t) for i, x, y, t in json.loads(path.read_text(encoding="utf-8"))))


@data_type("osm_points", "OpenStreetMap nodes with tags (places, peaks ...) inside the map.", codec=_PointsCodec(),
           preview=lambda p: f"{len(p.points)} points")
@dataclass(frozen=True)
class OsmPoints:
    points: tuple[OsmPoint, ...]


PLACE_VALUES = ("city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood", "isolated_dwelling", "farm",
                "locality")


class PointParams(NodeParams):
    tags: tuple[tuple[str, str], ...] = tuple(("place", v) for v in PLACE_VALUES)
    """(key, value) pairs; a node with one of them counts. Default: all places."""


@node("osm.points", output="points")
def points(frame: MapFrame, params: PointParams, context: NodeContext) -> OsmPoints:
    """Nodes with one of the tags inside the map (places by default)."""
    bbox = MapProjection(frame).wgs84_box()
    out: dict[int, OsmPoint] = {}
    for path in GeofabrikIndex(context.download_folder(), context).pbf_files(bbox):
        context.log(f"reading points from {path.name} ...")
        fp: Any = osmium.FileProcessor(str(path), osmium.osm.NODE).with_filter(
            osmium.filter.TagFilter(*params.tags))
        for o in fp:
            if (o.is_node() and o.location.valid() and bbox[0] <= o.location.lon <= bbox[2]
                    and bbox[1] <= o.location.lat <= bbox[3]):
                out[o.id] = OsmPoint(o.id, o.location.lon, o.location.lat, {t.k: t.v for t in o.tags})
    context.log(f"{len(out)} points")
    return OsmPoints(tuple(out.values()))
