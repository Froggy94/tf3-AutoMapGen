"""Elevation data from OpenTopography (API globaldem): the crop plus a margin as GeoTIFF, downloaded once into the
run's download folder (like the OSM region files). The API key is a secret of the run (`opentopography_api_key`);
a free key: OpenTopography portal, "My Account" (without a university account 50 requests per day)."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import rasterio
import requests
from pydantic import BaseModel, ConfigDict

from automapgen.graph import NodeContext

SECRET = "opentopography_api_key"
_PROGRESS_BYTES = 10 << 20


class DemDownload(BaseModel):
    """Which elevation model to download for the crop (a value of the project or the GUI)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    dem_type: str = "GEDTM30"
    """Elevation model of the API: GEDTM30 (terrain without trees and houses), EU_DTM, COP30 (surface with forest
    and houses), NASADEM, SRTMGL1, AW3D30."""
    margin_km: float = 1.0
    """Margin around the crop (the terrain is cut exactly later)."""
    url: str = "https://portal.opentopography.org/API/globaldem"
    """Address of the API."""


class OpenTopography:
    """Downloads the crop of an elevation model into a folder; a file that is already there is used again."""

    USER_AGENT = "tf3-AutoMapGen/0.1"

    def __init__(self, settings: DemDownload, folder: Path, context: NodeContext) -> None:
        self._settings = settings
        self._folder = folder
        self._context = context

    def file(self, west: float, south: float, east: float, north: float) -> Path:
        """The GeoTIFF of the crop (downloaded when missing)."""
        s = self._settings
        target = self._folder / f"{s.dem_type}_{west:.5f}_{south:.5f}_{east:.5f}_{north:.5f}.tif"
        if target.is_file():
            return target
        self._folder.mkdir(parents=True, exist_ok=True)
        lat = (south + north) / 2
        dlon = s.margin_km / (111.32 * math.cos(math.radians(lat)))
        dlat = s.margin_km / 110.57
        params = {"demtype": s.dem_type, "south": south - dlat, "north": north + dlat, "west": west - dlon,
                  "east": east + dlon, "outputFormat": "GTiff", "API_Key": self._context.secret(SECRET)}
        self._context.log(f"downloading {s.dem_type} from OpenTopography for lon {params['west']:.4f}-"
                          f"{params['east']:.4f}, lat {params['south']:.4f}-{params['north']:.4f} "
                          f"(margin {s.margin_km:g} km) ...")
        partial = target.with_name(target.name + ".part")
        size = self._fetch(params, partial)
        try:
            with rasterio.open(partial):                  # a readable GeoTIFF, not an error page
                pass
        except rasterio.errors.RasterioIOError as e:
            partial.unlink()
            raise ValueError(f"OpenTopography sent no readable GeoTIFF ({size} bytes)") from e
        os.replace(partial, target)
        self._context.log(f"saved {target} ({size / 1e6:.1f} MB)")
        return target

    def _fetch(self, params: dict[str, Any], partial: Path) -> int:
        with requests.get(self._settings.url, params=params, stream=True, timeout=(30, 600),
                          headers={"User-Agent": self.USER_AGENT}) as response:
            if response.status_code != 200:
                raise ValueError(f"OpenTopography answers {response.status_code}: {response.text[:500]}")
            size, reported = 0, 0
            with partial.open("wb") as stream:
                for chunk in response.iter_content(1 << 20):
                    self._context.check_cancelled()
                    stream.write(chunk)
                    size += len(chunk)
                    if size - reported >= _PROGRESS_BYTES:
                        self._context.log(f"  {size / 1e6:.0f} MB")
                        reported = size
        return size
