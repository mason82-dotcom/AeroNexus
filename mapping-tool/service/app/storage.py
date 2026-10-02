"""MinIO access. Two clients: internal (container network) for checks, public for presigned URLs.

Presigning is done offline (region is fixed), so the public endpoint does not have to be reachable
from inside the container. URLs handed out must use SERVER_HOST, never a docker hostname.
"""
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from .config import Settings


class Storage:
    def __init__(self, settings: Settings):
        self.settings = settings
        args = dict(access_key=settings.minio_access_key, secret_key=settings.minio_secret_key,
                    secure=False, region=settings.minio_region)
        self.internal = Minio(settings.minio_internal_endpoint, **args)
        self.public = Minio(settings.minio_public_endpoint, **args)

    def _ttl(self, seconds: int | None = None) -> timedelta:
        return timedelta(seconds=min(seconds or self.settings.presign_ttl_seconds, 3600))

    def presign_get(self, bucket: str, key: str, seconds: int | None = None) -> str:
        return self.public.presigned_get_object(bucket, key, expires=self._ttl(seconds))

    def presign_put(self, bucket: str, key: str) -> str:
        return self.public.presigned_put_object(bucket, key, expires=self._ttl())

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.internal.stat_object(bucket, key)
            return True
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return False
            raise

    def has_prefix(self, bucket: str, prefix: str) -> bool:
        return any(True for _ in self.internal.list_objects(bucket, prefix=prefix, recursive=True))
