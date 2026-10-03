"""Request bodies (validation) for the web UI and the agent API (docs/agent-api.md)."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_PATH_RE = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")
_AGENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def check_relative_path(path: str) -> str:
    if len(path) > 400 or not _PATH_RE.match(path) or any(p in (".", "..") for p in path.split("/")):
        raise ValueError(f"invalid path: {path!r}")
    return path


# Starting points only; the agent may refine them from GET /options of its NodeODM.
PROFILES: dict[str, dict] = {
    "fast": {"fast-orthophoto": True, "feature-quality": "medium", "skip-3dmodel": True},
    "standard": {"feature-quality": "high", "pc-quality": "medium", "skip-3dmodel": True},
    "high": {"feature-quality": "ultra", "pc-quality": "high", "dsm": True},
    # Farming Guide: Mavic 3M bands (G/R/RE/NIR TIFs) -> multiband orthophoto -> relative vegetation indices
    "multispectral": {"radiometric-calibration": "camera+sun", "primary-band": "NIR", "skip-3dmodel": True},
}


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    image_keys: list[str] = Field(min_length=1, max_length=5000)
    profile: Literal["fast", "standard", "high", "multispectral"] = "standard"
    odm_options: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=50)

    @field_validator("image_keys")
    @classmethod
    def unique_keys(cls, keys: list[str]) -> list[str]:
        if len(set(keys)) != len(keys):
            raise ValueError("image_keys contains duplicates")
        return keys


class MetaRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=200)


class AgentRef(BaseModel):
    agent_id: str

    @field_validator("agent_id")
    @classmethod
    def valid_agent(cls, value: str) -> str:
        if not _AGENT_RE.match(value):
            raise ValueError("agent_id: 1-64 chars of A-Z a-z 0-9 . _ -")
        return value


class Claim(AgentRef):
    lease_seconds: int | None = Field(default=None, ge=10, le=3600)
    capabilities: dict = Field(default_factory=dict)


class Heartbeat(AgentRef):
    progress: float = Field(default=0, ge=0, le=100)
    message: str | None = Field(default=None, max_length=500)
    lease_seconds: int | None = Field(default=None, ge=10, le=3600)


class UploadUrls(AgentRef):
    paths: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("paths")
    @classmethod
    def valid_paths(cls, paths: list[str]) -> list[str]:
        return [check_relative_path(p) for p in paths]


class ManifestFile(BaseModel):
    path: str
    kind: str = Field(default="other", max_length=32)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size: int | None = Field(default=None, ge=0)

    @field_validator("path")
    @classmethod
    def valid_path(cls, path: str) -> str:
        return check_relative_path(path)


class ManifestTiles(BaseModel):
    path: str = "tiles"
    format: Literal["png", "webp", "jpg"] = "png"
    minzoom: int = Field(ge=0, le=24)
    maxzoom: int = Field(ge=0, le=24)

    @field_validator("path")
    @classmethod
    def valid_path(cls, path: str) -> str:
        return check_relative_path(path)


class ManifestLayer(ManifestTiles):
    """Additional tile layer of a job, e.g. a vegetation index of the Farming Guide."""
    kind: str = Field(pattern=r"^[a-z0-9_]{1,32}$")      # e.g. ndvi, ndre, gndvi
    name: str = Field(min_length=1, max_length=60)        # appended to the job name
    legend: dict | None = None                            # {"range": [lo, hi], "palette": [[v, "#rrggbb"], ...], "unit": ""}
    stats: dict | None = None                             # {"mean", "std", "min", "max", "p10", "p50", "p90", "valid_ratio"}
    relative: bool = False


class Manifest(BaseModel):
    crs: str | None = Field(default=None, max_length=32)
    bounds_wgs84: list[float] | None = Field(default=None, min_length=4, max_length=4)
    files: list[ManifestFile] = Field(default_factory=list, max_length=10000)
    tiles: ManifestTiles | None = None
    layers: list[ManifestLayer] = Field(default_factory=list, max_length=20)


class Complete(AgentRef):
    manifest: Manifest


class Fail(AgentRef):
    error: str = Field(min_length=1, max_length=2000)


_NAME_RE = re.compile(r"^[A-Za-z0-9_().-][A-Za-z0-9 _().-]{0,58}[A-Za-z0-9_().-]$|^[A-Za-z0-9_().-]$")


def _segments_cross(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and 0 not in (o1, o2, o3, o4)


class RouteParams(BaseModel):
    height: float = Field(ge=10, le=500)            # m, shooting height relative to the height mode
    direction: int = Field(ge=0, le=359)            # deg, main flight line direction
    margin: int = Field(ge=0, le=200)               # m, extension beyond the polygon
    overlap_h: int = Field(ge=10, le=90)            # %, along track
    overlap_w: int = Field(ge=10, le=90)            # %, across track
    speed: float = Field(ge=1, le=15)               # m/s
    image_format: str | None = Field(default=None, pattern=r"^(visable|ir|visable,ir)$")  # thermal cameras
    lens: str | None = Field(default=None, pattern=r"^(wide|thermal|ms)$")  # camera that sets line/photo spacing


class RouteCopy(BaseModel):
    name: str
    polygon: list[list[float]] = Field(min_length=3, max_length=100)
    params: RouteParams

    @field_validator("name")
    @classmethod
    def valid_name(cls, name: str) -> str:
        if not _NAME_RE.match(name):
            raise ValueError("name: 1-60 chars of A-Z a-z 0-9 space _ ( ) . - (becomes the file name)")
        return name

    @field_validator("polygon")
    @classmethod
    def valid_polygon(cls, poly: list[list[float]]) -> list[list[float]]:
        for p in poly:
            if len(p) != 2 or not (-180 <= p[0] <= 180 and -85 <= p[1] <= 85):
                raise ValueError("polygon points must be [lon, lat]")
        lons, lats = [p[0] for p in poly], [p[1] for p in poly]
        if max(lons) - min(lons) > 0.3 or max(lats) - min(lats) > 0.2:
            raise ValueError("polygon larger than about 20 km")
        n = len(poly)
        area2 = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
        if abs(area2) < 1e-12:
            raise ValueError("polygon has no area")
        for i in range(n):
            for j in range(i + 1, n):
                if abs(i - j) in (1, n - 1):
                    continue
                if _segments_cross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                    raise ValueError("polygon edges intersect")
        return poly


class PlanPreview(BaseModel):
    polygon: list[list[float]] = Field(min_length=3, max_length=100)
    lens: str = Field(pattern=r"^(wide|thermal|ms)$")
    height: float = Field(ge=10, le=500)
    overlap_h: int = Field(ge=10, le=90)
    overlap_w: int = Field(ge=10, le=90)
    direction: int = Field(ge=0, le=359)
    speed: float = Field(ge=1, le=15)
    margin: int = Field(default=0, ge=0, le=200)
