from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .config_holder import get_config
from .http_client import http_client
from .s3_utils import S3Utils

send_video_lock = asyncio.Lock()


def extract_images(event: AstrMessageEvent) -> list[Comp.Image]:
    return [m for m in event.get_messages() if isinstance(m, Comp.Image)]


def extract_image_urls(event: AstrMessageEvent) -> list[str]:
    urls: list[str] = []
    for m in extract_images(event):
        url = getattr(m, "url", None) or getattr(m, "file", None)
        if url and str(url).startswith(("http://", "https://", "file://", "base64://")):
            urls.append(str(url))
        elif url:
            urls.append(str(url))
    return urls


def find_json_segment(event: AstrMessageEvent) -> dict[str, Any] | None:
    for m in event.get_messages():
        type_name = m.__class__.__name__.lower()
        raw = getattr(m, "data", None) or getattr(m, "content", None)
        if type_name in ("json", "xml") or (isinstance(raw, str) and raw.strip().startswith("{")):
            try:
                if isinstance(raw, dict):
                    return raw
                if isinstance(raw, str):
                    return json.loads(raw)
            except Exception:
                continue
        if hasattr(m, "type") and str(getattr(m, "type")) == "json":
            data = getattr(m, "data", {}) or {}
            payload = data.get("data") or data
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except Exception:
                    continue
            if isinstance(payload, dict):
                return payload
    try:
        message_obj = event.message_obj
        raw_message = getattr(message_obj, "message", None) or getattr(message_obj, "raw_message", None)
        if isinstance(raw_message, list):
            for seg in raw_message:
                if isinstance(seg, dict) and seg.get("type") == "json":
                    data = seg.get("data", {}).get("data")
                    if isinstance(data, str):
                        return json.loads(data)
                    if isinstance(data, dict):
                        return data
    except Exception as e:
        logger.debug(f"find_json_segment fallback failed: {e}")
    return None


async def zhihu_pic(url: str) -> bytes:
    base = (get_config("zhihu_url") or "http://localhost:38127").rstrip("/")
    resp = await http_client.post(
        f"{base}/render",
        data={"url": url},
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.content


async def send_video_or_file(event: AstrMessageEvent, file_path: Path, filename: str | None = None):
    """小文件发视频，大文件走群文件。优先 S3 预签名 URL。"""
    filename = filename or file_path.name
    size = file_path.stat().st_size
    key = f"tmp/{filename}"
    url = None
    try:
        S3Utils.put_object(key, file_path)
        url = S3Utils.presigned_url(key)
    except Exception as e:
        logger.warning(f"S3 upload failed, fallback local file: {e}")

    if size > 50 * 1024 * 1024:
        group_id = event.get_group_id()
        if not group_id:
            raise RuntimeError("大文件仅支持群聊上传")
        if url:
            try:
                downloaded = await event.bot.api.call_action("download_file", url=url)
                remote_path = downloaded.get("file") or downloaded.get("data", {}).get("file")
                await event.bot.api.call_action(
                    "upload_group_file",
                    group_id=int(group_id),
                    file=remote_path,
                    name=filename,
                )
                return
            except Exception as e:
                logger.warning(f"upload_group_file via url failed: {e}")
        await event.bot.api.call_action(
            "upload_group_file",
            group_id=int(group_id),
            file=str(file_path.resolve()),
            name=filename,
        )
        return

    if url:
        await event.send(event.chain_result([Comp.Video.fromURL(url)]))
    else:
        await event.send(event.chain_result([Comp.Video.fromFileSystem(str(file_path.resolve()))]))


def extract_url(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(0) if match else None
