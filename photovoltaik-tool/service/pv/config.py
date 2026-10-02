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
    mapping_url: str            # media list of the mapping service (object keys, looked up with the user's token)
    media_bucket: str           # Pilot 2 uploads (read only)
    bucket: str                 # own bucket: inspections, crops, exports
    web_origin: str             # browser origin of the web UI (CORS)
    tsdk_dir: str               # DJI Thermal SDK (not in the repo, see photovoltaik-tool/README.md)


def load() -> Settings:
    # GDAL reads/writes MinIO through /vsis3/ (AWS_S3_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    for name in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        _env(name)
    return Settings(
        port=int(_env("PORT", "6792")),
        jwt_secret=_env("JWT_SECRET"),
        mapping_url=_env("MAPPING_URL", "http://mapping:6790").rstrip("/"),
        media_bucket=_env("MINIO_BUCKET", "dji-cloud"),
        bucket=_env("PV_BUCKET", "pv-inspections"),
        web_origin=_env("WEB_ORIGIN", "*"),
        tsdk_dir=_env("TSDK_DIR", "/opt/dji-tsdk"),
    )
