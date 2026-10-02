"""Settings from environment (edge/docker-compose.yml passes them from edge/.env)."""
import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    server_host: str
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    media_database: str
    jwt_secret: str
    agent_token: str
    presign_ttl_seconds: int
    minio_internal_endpoint: str
    minio_public_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_region: str
    media_bucket: str
    results_bucket: str
    basemaps_bucket: str
    default_lease_seconds: int
    max_lease_attempts: int
    wol_mac: str
    wol_broadcast: str


def load() -> Settings:
    server_host = _env("SERVER_HOST")
    return Settings(
        server_host=server_host,
        mysql_host=_env("MYSQL_HOST", "mysql"),
        mysql_port=int(_env("MYSQL_PORT", "3306")),
        mysql_user=_env("MYSQL_USER", "root"),
        mysql_password=_env("MYSQL_ROOT_PASSWORD"),
        mysql_database=_env("MAPPING_DATABASE", "mapping"),
        media_database=_env("MEDIA_DATABASE", "cloud_sample"),
        jwt_secret=_env("JWT_SECRET"),
        agent_token=_env("MAPPING_AGENT_TOKEN"),
        presign_ttl_seconds=int(_env("PRESIGN_TTL_SECONDS", "3600")),
        # internal: container network (stat/list); public: what the agent and browsers can reach
        minio_internal_endpoint=_env("MINIO_INTERNAL_ENDPOINT", "minio:9000"),
        minio_public_endpoint=_env("MINIO_PUBLIC_ENDPOINT", f"{server_host}:9000"),
        minio_access_key=_env("MAPPING_MINIO_USER"),
        minio_secret_key=_env("MAPPING_MINIO_PASSWORD"),
        minio_region=_env("MINIO_REGION", "us-east-1"),
        media_bucket=_env("MINIO_BUCKET"),
        results_bucket=_env("MAPPING_RESULTS_BUCKET", "mapping-results"),
        basemaps_bucket=_env("MAPPING_BASEMAPS_BUCKET", "basemaps"),
        default_lease_seconds=int(_env("MAPPING_LEASE_SECONDS", "600")),
        max_lease_attempts=int(_env("MAPPING_MAX_LEASE_ATTEMPTS", "3")),
        wol_mac=os.environ.get("WOL_MAC", ""),
        wol_broadcast=os.environ.get("WOL_BROADCAST", ""),
    )
