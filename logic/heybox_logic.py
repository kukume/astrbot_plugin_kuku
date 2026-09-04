from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..utils.http_client import DEFAULT_UA, curl_session

_TZ_BJ = timezone(timedelta(hours=8))
_ALPHABET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
_TREE_PATH = "/bbs/app/link/tree"
_HOST_MARKERS = ("xiaoheihe.cn",)
_LINK_PATH_RE = re.compile(r"/app/bbs/link/([A-Za-z0-9]+)", re.I)
_LINK_ID_RE = re.compile(r"(?:[?&]link_id=|/link/)([A-Za-z0-9]+)", re.I)
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9]{6,32}$")
_VIDEO_EXTS = (".mp4", ".m3u8", ".mov", ".webm")
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://www.xiaoheihe.cn",
    "Referer": "https://www.xiaoheihe.cn/",
}


def is_heybox_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(host == m or host.endswith("." + m) for m in _HOST_MARKERS):
        return True
    lowered = (url or "").lower()
    return "xiaoheihe.cn" in lowered or "heybox://" in lowered


def is_heybox_video_url(url: str) -> bool:
    path = (url or "").split("?", 1)[0].rsplit("#", 1)[0].lower()
    return path.endswith(_VIDEO_EXTS)


def parse_link_id(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise RuntimeError("未找到小黑盒帖子 ID")
    m = _LINK_PATH_RE.search(raw) or _LINK_ID_RE.search(raw)
    if m:
        return m.group(1)
    if _BARE_ID_RE.fullmatch(raw):
        return raw
    raise RuntimeError("未找到小黑盒帖子 ID")


def _format_ts(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ts > 10_000_000_000:
        ts //= 1000
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_TZ_BJ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OSError, OverflowError, ValueError):
        return str(value)


def _https(url: str) -> str:
    if url.startswith("http://"):
        return "https://" + url[7:]
    if url.startswith("//"):
        return "https:" + url
    return url


def _xtime(e: int) -> int:
    return ((e << 1) ^ 27) & 255 if e & 128 else (e << 1) & 255


def _pc(e: int) -> int:
    return _xtime(e) ^ e


def _sf(e: int) -> int:
    return _pc(_xtime(e))


def _lh(e: int) -> int:
    return _sf(_pc(_xtime(e)))


def _ig(e: int) -> int:
    return _lh(e) ^ _sf(e) ^ _pc(e)


def _mix_first4(arr: list[int]) -> list[int]:
    e = list(arr)
    t0 = _ig(e[0]) ^ _lh(e[1]) ^ _sf(e[2]) ^ _pc(e[3])
    t1 = _pc(e[0]) ^ _ig(e[1]) ^ _lh(e[2]) ^ _sf(e[3])
    t2 = _sf(e[0]) ^ _pc(e[1]) ^ _ig(e[2]) ^ _lh(e[3])
    t3 = _lh(e[0]) ^ _sf(e[1]) ^ _pc(e[2]) ^ _ig(e[3])
    e[0], e[1], e[2], e[3] = t0, t1, t2, t3
    return e


def _map_slice(text: str, alphabet: str, end: int) -> str:
    table = alphabet[:end]
    return "".join(table[ord(ch) % len(table)] for ch in text)


def _map_full(text: str, alphabet: str) -> str:
    return "".join(alphabet[ord(ch) % len(alphabet)] for ch in text)


def _interleave(parts: list[str]) -> str:
    out = []
    for i in range(max((len(p) for p in parts), default=0)):
        for part in parts:
            if i < len(part):
                out.append(part[i])
    return "".join(out)


def _hkey(path: str, ts: int, nonce: str) -> str:
    # 网页端 PM.g：Tr(path, time+1, nonce)
    norm = "/" + "/".join(p for p in path.split("/") if p) + "/"
    seed = _interleave(
        [
            _map_slice(str(ts + 1), _ALPHABET, -2),
            _map_full(norm, _ALPHABET),
            _map_full(nonce, _ALPHABET),
        ]
    )[:20]
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    mixed = _mix_first4([ord(c) for c in digest[-6:]])
    checksum = f"{sum(mixed) % 100:02d}"
    return _map_slice(digest[:5], _ALPHABET, -4) + checksum


def _sign(path: str) -> dict[str, str]:
    ts = int(time.time())
    nonce = hashlib.md5(f"{ts}{time.time_ns()}{random.random()}".encode()).hexdigest().upper()
    return {
        "version": "999.0.4",
        "hkey": _hkey(path, ts, nonce),
        "_time": str(ts),
        "nonce": nonce,
    }


@dataclass
class HeyboxDetail:
    id: str
    title: str
    description: str
    push_time: str
    username: str
    userid: str
    avatar: str = ""
    ip_location: str = ""
    topic: str = ""
    tags: str = ""
    images: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    share_url: str = ""


def _upgrade_image(url: str) -> str:
    url = _https(url.strip())
    if not url:
        return ""
    # imageMogr2 的 `>` / `%3E` 会截断 CQ 码，合并转发时被当成缺 app 的 JSON
    base, _, query = url.partition("?")
    if "imageMogr2" in query or "%3E" in query or ">" in query:
        return base
    return url


def _parse_text_blocks(raw: Any) -> tuple[str, list[str], list[str]]:
    texts: list[str] = []
    images: list[str] = []
    videos: list[str] = []
    blocks: Any = raw
    if isinstance(raw, str) and raw.strip().startswith("["):
        try:
            blocks = json.loads(raw)
        except json.JSONDecodeError:
            blocks = raw
    if isinstance(blocks, str):
        if blocks.strip():
            texts.append(blocks.strip())
        return "\n".join(texts), images, videos
    if not isinstance(blocks, list):
        return "", images, videos
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").lower()
        url = _https(str(block.get("url") or "").strip())
        text = str(block.get("text") or "").strip()
        if kind in {"img", "image"} and url:
            images.append(_upgrade_image(url))
        elif kind == "video" and url:
            videos.append(url)
        elif url and is_heybox_video_url(url):
            videos.append(url)
        elif url and kind not in {"text"}:
            images.append(_upgrade_image(url))
        elif text and kind in {"", "text"}:
            texts.append(text)
        elif text and not url:
            texts.append(text)
    return "\n".join(texts), images, videos


def _detail_from_link(link: dict[str, Any], link_id: str) -> HeyboxDetail:
    user = link.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    body, images, videos = _parse_text_blocks(link.get("text"))
    desc = body or str(link.get("description") or "").strip()
    for extra in link.get("imgs") or []:
        url = _upgrade_image(str(extra))
        if url and url not in images and not is_heybox_video_url(url):
            images.append(url)
    topics = []
    for topic in link.get("topics") or []:
        if isinstance(topic, dict):
            name = str(topic.get("name") or "").strip()
        else:
            name = str(topic).strip()
        if name:
            topics.append(name)
    tags = []
    for tag in link.get("hashtags") or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
        else:
            name = str(tag).strip()
        if name:
            tags.append(name)
    avatar = _upgrade_image(str(user.get("avatar") or user.get("avartar") or ""))
    ip_location = str(link.get("ip_location") or "").strip()
    return HeyboxDetail(
        id=str(link.get("linkid") or link_id),
        title=str(link.get("title") or "").strip(),
        description=desc,
        push_time=_format_ts(link.get("create_at")),
        username=str(user.get("username") or "").strip(),
        userid=str(user.get("userid") or link.get("userid") or ""),
        avatar=avatar,
        ip_location=ip_location,
        topic=topics[0] if topics else "",
        tags=" ".join(tags),
        images=images,
        video_urls=videos,
        share_url=str(link.get("share_url") or "").strip(),
    )


def _query_with_sign(path: str, extra: dict[str, str]) -> str:
    params = {
        "app": "heybox",
        "os_type": "web",
        "x_app": "heybox_website",
        "x_client_type": "web",
        "x_os_type": "Windows",
        "x_client_version": "",
        "client_type": "web",
        "web_version": "3.0",
        **_sign(path),
        **extra,
    }
    return urllib.parse.urlencode(params)


def _fetch_json(url: str) -> dict[str, Any]:
    headers = {**_HEADERS, "User-Agent": DEFAULT_UA}
    try:
        with curl_session() as session:
            resp = session.get(url, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if not isinstance(payload, dict):
        raise RuntimeError("小黑盒接口返回异常")
    return payload


def _fetch_media(url: str) -> bytes:
    headers = {
        **_HEADERS,
        "User-Agent": DEFAULT_UA,
        "Referer": "https://www.xiaoheihe.cn/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        with curl_session() as session:
            resp = session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.content or b""
            if data:
                return data
    except Exception:
        pass
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        data = resp.read()
    if not data:
        raise RuntimeError("小黑盒媒体为空")
    return data


def _fetch_link(link_id: str) -> dict[str, Any]:
    query = _query_with_sign(
        _TREE_PATH,
        {
            "link_id": link_id,
            "is_first": "1",
            "page": "1",
            "limit": "1",
            "owner_only": "0",
        },
    )
    url = f"https://api.xiaoheihe.cn{_TREE_PATH}?{query}"
    data = _fetch_json(url)
    if str(data.get("status") or "") != "ok":
        raise RuntimeError(str(data.get("msg") or "小黑盒接口校验失败"))
    link = (data.get("result") or {}).get("link")
    if not isinstance(link, dict):
        raise RuntimeError("未获取到小黑盒帖子")
    return link


class HeyboxLogic:
    @staticmethod
    async def detail(url: str) -> HeyboxDetail:
        link_id = parse_link_id(url)
        link = await asyncio.to_thread(_fetch_link, link_id)
        detail = _detail_from_link(link, link_id)
        if not detail.id and not detail.title and not detail.description:
            raise RuntimeError("未获取到数据")
        return detail

    @staticmethod
    async def fetch_media(url: str) -> bytes:
        return await asyncio.to_thread(_fetch_media, url)


def extract_heybox_url(text: str) -> str:
    if not text:
        return ""
    for m in re.finditer(r"https?://[^\s<>\"']+", text):
        candidate = m.group(0).rstrip(").,，。]")
        if is_heybox_url(candidate):
            try:
                parse_link_id(candidate)
            except RuntimeError:
                continue
            return candidate
    qs = parse_qs(urlparse(text).query)
    if qs.get("link_id"):
        return text
    if _LINK_PATH_RE.search(text):
        return text
    return ""
