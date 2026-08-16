from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path

from .http_client import http_client

logger = logging.getLogger("astrbot_plugin_kuku.command")


async def run_ffmpeg(args: list[str]) -> None:
    """调用 ffmpeg（AstrBot 环境一般自带）。传参数列表，避免 Windows 走 cmd /C 把路径转义坏。"""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        logger.info(stdout.decode(errors="ignore"))
    if stderr:
        logger.error(stderr.decode(errors="ignore"))
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


async def run_command(command: str) -> str:
    is_windows = platform.system().lower().startswith("win")
    if is_windows:
        proc = await asyncio.create_subprocess_exec(
            "cmd",
            "/c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    stdout, stderr = await proc.communicate()
    out = (stdout or b"").decode(errors="ignore")
    err = (stderr or b"").decode(errors="ignore")
    if out:
        logger.info(out)
    if err:
        logger.error(err)
    if proc.returncode != 0:
        raise RuntimeError(f"command error: {proc.returncode}\n{err or out}")
    return out


async def segments_download(
    file_path: str | Path,
    url: str,
    headers: dict[str, str] | None = None,
) -> Path:
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    prefix_length = 0
    suffix_length = 4_000_000
    new_url = url
    req_headers = dict(headers or {})

    with path.open("ab") as fos:
        while True:
            range_headers = {
                **req_headers,
                "range": f"bytes={prefix_length}-{suffix_length}",
            }
            response = await http_client.get(new_url, headers=range_headers, follow_redirects=False)
            location = response.headers.get("Location")
            if location:
                new_url = location
                continue
            response.raise_for_status()
            fos.write(response.content)

            content_range = response.headers.get("Content-Range")
            if not content_range:
                # 有些源不支持 range，直接一次写完
                break
            # bytes 0-xx/total
            total = int(content_range.split("/")[1])
            if suffix_length >= total:
                break
            prefix_length = suffix_length + 1
            temp_length = suffix_length + 4_000_000
            suffix_length = temp_length if temp_length < total else total

    return path
