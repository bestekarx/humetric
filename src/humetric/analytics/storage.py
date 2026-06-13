"""Export storage abstraction — local filesystem and S3-compatible (Cloudflare R2)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from humetric import config
from humetric.analytics import require_analytics

_log = logging.getLogger(__name__)


class ExportStorage(ABC):
    """Abstract export storage backend."""

    @abstractmethod
    async def put(self, path: str, data: bytes) -> None:
        """Write data to path (creates parent dirs / prefixes as needed)."""
        ...

    @abstractmethod
    async def get(self, path: str) -> bytes | None:
        """Read data from path. Returns None if the file does not exist."""
        ...

    @abstractmethod
    async def list_prefix(self, prefix: str) -> list[str]:
        """List all paths under the given prefix."""
        ...

    @abstractmethod
    def duckdb_glob(self, prefix: str) -> str:
        """Return a glob string suitable for DuckDB read_parquet()."""
        ...


class LocalStorage(ExportStorage):
    """Writes Parquet files to a local directory tree."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def put(self, path: str, data: bytes) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    async def get(self, path: str) -> bytes | None:
        target = self.root / path
        if not target.exists():
            return None
        return target.read_bytes()

    async def list_prefix(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()]

    def duckdb_glob(self, prefix: str) -> str:
        return str(self.root / prefix / "**" / "*.parquet")


class S3Storage(ExportStorage):
    """Writes Parquet files to S3-compatible object storage (Cloudflare R2 etc.)."""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: str = "",
        region: str = "auto",
        access_key_id: str = "",
        secret_access_key: str = "",
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self._endpoint_url = endpoint_url or None
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._client: object | None = None

    def _get_client(self) -> object:
        if self._client is None:
            import boto3

            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _s3_key(self, path: str) -> str:
        return f"{self.prefix}/{path}" if self.prefix else path

    async def put(self, path: str, data: bytes) -> None:
        key = self._s3_key(path)
        client = self._get_client()
        await asyncio.to_thread(client.put_object, Bucket=self.bucket, Key=key, Body=data)
        _log.debug("S3 put s3://%s/%s (%d bytes)", self.bucket, key, len(data))

    async def get(self, path: str) -> bytes | None:
        from botocore.exceptions import ClientError

        key = self._s3_key(path)
        client = self._get_client()
        try:
            resp = await asyncio.to_thread(client.get_object, Bucket=self.bucket, Key=key)
            return await asyncio.to_thread(resp["Body"].read)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    async def list_prefix(self, prefix: str) -> list[str]:
        s3_prefix = self._s3_key(prefix)
        client = self._get_client()
        paginator = client.get_paginator("list_objects_v2")

        keys: list[str] = []

        async def _paginate() -> None:
            pages = paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix)
            for page in pages:
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    # Strip the storage prefix to return relative paths
                    rel = k[len(self.prefix) + 1 :] if self.prefix else k
                    keys.append(rel)

        await asyncio.to_thread(lambda: list(paginator.paginate(Bucket=self.bucket, Prefix=s3_prefix)))
        # Re-implement synchronously inside to_thread for simplicity
        keys.clear()

        def _list_sync() -> list[str]:
            result = []
            paginator2 = client.get_paginator("list_objects_v2")
            for page in paginator2.paginate(Bucket=self.bucket, Prefix=s3_prefix):
                for obj in page.get("Contents", []):
                    k = obj["Key"]
                    rel = k[len(self.prefix) + 1 :] if self.prefix else k
                    result.append(rel)
            return result

        return await asyncio.to_thread(_list_sync)

    def duckdb_glob(self, prefix: str) -> str:
        s3_prefix = self._s3_key(prefix)
        return f"s3://{self.bucket}/{s3_prefix}/**/*.parquet"


def get_export_storage() -> ExportStorage:
    """Return the configured export storage backend."""
    require_analytics()
    storage_type = config.EXPORT_STORAGE
    if storage_type == "local":
        return LocalStorage(config.EXPORT_LOCAL_DIR)
    if storage_type == "s3":
        if not config.EXPORT_S3_BUCKET:
            raise RuntimeError(
                "HUMETRIC_EXPORT_S3_BUCKET is required when HUMETRIC_EXPORT_STORAGE=s3"
            )
        return S3Storage(
            bucket=config.EXPORT_S3_BUCKET,
            prefix=config.EXPORT_S3_PREFIX,
            endpoint_url=config.EXPORT_S3_ENDPOINT_URL,
            region=config.EXPORT_S3_REGION,
            access_key_id=config.EXPORT_S3_ACCESS_KEY_ID,
            secret_access_key=config.EXPORT_S3_SECRET_ACCESS_KEY,
        )
    raise RuntimeError(
        f"Unknown HUMETRIC_EXPORT_STORAGE value: {storage_type!r}. Must be 'local' or 's3'."
    )
