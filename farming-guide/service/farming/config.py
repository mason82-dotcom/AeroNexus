"""Settings from environment (edge/docker-compose.yml)."""
import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    port: int
    jwt_secret: str
    mapping_url: str            # internal URL of the mapping service (layer lookup with the user's token)
    results_bucket: str         # mapping results (index COGs), read only
    bucket: str                 # own bucket for zone maps and exports
    web_origin: str             # browser origin of the web UI (CORS)


def load() -> Settings:
    # GDAL reads/writes MinIO through /vsis3/ with these variables (set in docker-compose.yml):
    # AWS_S3_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_HTTPS=NO, AWS_VIRTUAL_HOSTING=FALSE
    for name in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        _env(name)
    return Settings(
        port=int(_env("PORT", "6791")),
        jwt_secret=_env("JWT_SECRET"),
        mapping_url=_env("MAPPING_URL", "http://mapping:6790").rstrip("/"),
        results_bucket=_env("MAPPING_RESULTS_BUCKET", "mapping-results"),
        bucket=_env("FARMING_BUCKET", "farming"),
        web_origin=_env("WEB_ORIGIN", "*"),
    )
