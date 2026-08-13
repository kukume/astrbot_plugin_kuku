from __future__ import annotations

import os
from typing import Any

_config: dict[str, Any] = {}


def set_config(config: dict[str, Any] | None) -> None:
    global _config
    _config = dict(config or {})


def get_config(key: str, default: Any = None) -> Any:
    """优先读插件配置，其次环境变量。"""
    value = _config.get(key)
    if value is None or value == "":
        env_key = key.upper()
        env_aliases = {
            "rapid_key": "RAPID_KEY",
            "zhihu_url": "ZHIHU_URL",
            "xhs_url": "XHS_URL",
            "openai_api_key": "OPENAI_API_KEY",
            "openai_base_url": "OPENAI_BASE_URL",
            "openai_image_model": "OPENAI_IMAGE_MODEL",
            "openai_video_model": "OPENAI_VIDEO_MODEL",
            "s3_region": "S3_REGION",
            "s3_access_key_id": "S3_ACCESS_KEY_ID",
            "s3_secret_access_key": "S3_SECRET_ACCESS_KEY",
            "s3_bucket": "S3_BUCKET",
            "s3_endpoint_url": "S3_ENDPOINT_URL",
        }
        env_name = env_aliases.get(key, env_key)
        return os.getenv(env_name, default)
    return value
