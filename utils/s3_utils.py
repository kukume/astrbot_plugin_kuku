from __future__ import annotations

from pathlib import Path

from .config_holder import get_config
from .http_client import http_client


class S3Utils:
    @staticmethod
    def _client():
        import boto3
        from botocore.client import Config

        region = get_config("s3_region")
        access_key = get_config("s3_access_key_id")
        secret_key = get_config("s3_secret_access_key")
        endpoint = get_config("s3_endpoint_url")
        if not all([region, access_key, secret_key, endpoint]):
            raise RuntimeError("S3 配置不完整，请在插件后台配置 s3_* 相关项")
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint,
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    @staticmethod
    def _bucket() -> str:
        bucket = get_config("s3_bucket")
        if not bucket:
            raise RuntimeError("缺少 s3_bucket 配置")
        return bucket

    @classmethod
    def put_object(cls, key: str, file_path: str | Path) -> None:
        client = cls._client()
        path = Path(file_path)
        client.upload_file(str(path), cls._bucket(), key)

    @classmethod
    def put_bytes(cls, key: str, data: bytes) -> None:
        client = cls._client()
        client.put_object(Bucket=cls._bucket(), Key=key, Body=data)

    @classmethod
    def presigned_url(cls, key: str, expires_in: int = 3600) -> str:
        client = cls._client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cls._bucket(), "Key": key},
            ExpiresIn=expires_in,
        )

    @classmethod
    async def presigned_url_with_upload(cls, key: str, path: str | Path) -> str:
        url = cls.presigned_url(key)
        try:
            resp = await http_client.get(url, follow_redirects=True)
            if resp.status_code == 200:
                return url
        except Exception:
            pass
        cls.put_object(key, path)
        return cls.presigned_url(key)

    @classmethod
    def delete_object(cls, key: str) -> None:
        client = cls._client()
        client.delete_object(Bucket=cls._bucket(), Key=key)
