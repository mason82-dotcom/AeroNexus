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
}


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    image_keys: list[str] = Field(min_length=1, max_length=5000)
    profile: Literal["fast", "standard", "high"] = "standard"
    odm_options: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=50)

    @field_validator("image_keys")
    @classmethod
    def unique_keys(cls, keys: list[str]) -> list[str]:
        if len(set(keys)) != len(keys):
            raise ValueError("image_keys contains duplicates")
        return keys


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


class Manifest(BaseModel):
    crs: str | None = Field(default=None, max_length=32)
    bounds_wgs84: list[float] | None = Field(default=None, min_length=4, max_length=4)
    files: list[ManifestFile] = Field(default_factory=list, max_length=10000)
    tiles: ManifestTiles | None = None


class Complete(AgentRef):
    manifest: Manifest


class Fail(AgentRef):
    error: str = Field(min_length=1, max_length=2000)
