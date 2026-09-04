from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from ..utils.http_client import curl_session

_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
_XHS_HOST_MARKERS = (
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
)
_VIDEO_EXTS = (".mp4", ".m3u8", ".mov", ".webm")
_TZ_BJ = timezone(timedelta(hours=8))


def is_xhs_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if any(host == m or host.endswith("." + m) for m in _XHS_HOST_MARKERS):
        return True
    lowered = (url or "").lower()
    return any(m in lowered for m in _XHS_HOST_MARKERS)


def is_xhs_video_url(url: str) -> bool:
    path = (url or "").split("?", 1)[0].rsplit("#", 1)[0].lower()
    return path.endswith(_VIDEO_EXTS)


def _format_ms(value: Any) -> str:
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
    return url


@dataclass
class XhsDetail:
    id: str
    title: str
    description: str
    push_time: str
    update_time: str
    username: str
    userid: str
    download_urls: list[str]
    tags: str = ""
    avatar: str = ""
    video_urls: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "XhsDetail":
        download_urls = [str(u) for u in (data.get("下载地址") or data.get("download_urls") or [])]
        video_urls = [str(u) for u in (data.get("video_urls") or [])]
        if not video_urls:
            video_urls = [u for u in download_urls if is_xhs_video_url(u)]
        return cls(
            id=str(data.get("作品ID") or data.get("id") or ""),
            title=str(data.get("作品标题") or data.get("title") or ""),
            description=str(data.get("作品描述") or data.get("description") or ""),
            push_time=str(data.get("发布时间") or data.get("push_time") or ""),
            update_time=str(data.get("最后更新时间") or data.get("update_time") or ""),
            username=str(data.get("作者昵称") or data.get("username") or ""),
            userid=str(data.get("作者ID") or data.get("userid") or ""),
            download_urls=download_urls,
            tags=str(data.get("作品标签") or data.get("tags") or ""),
            avatar=str(data.get("avatar") or data.get("作者头像") or ""),
            video_urls=video_urls,
        )


def _parse_initial_state(html: str) -> dict[str, Any]:
    idx = html.find("__INITIAL_STATE__=")
    if idx < 0:
        raise RuntimeError("未获取到小红书数据")
    raw = html[idx:].split("=", 1)[1]
    end = raw.find("</script>")
    if end < 0:
        raise RuntimeError("未获取到小红书数据")
    raw = raw[:end].strip()
    if raw.endswith(";"):
        raw = raw[:-1]
    raw = re.sub(r"\bundefined\b", "null", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("小红书页面数据解析失败") from e
    if not isinstance(data, dict):
        raise RuntimeError("未获取到小红书数据")
    return data


def _note_from_state(state: dict[str, Any]) -> dict[str, Any]:
    note_map = ((state.get("note") or {}).get("noteDetailMap") or {})
    if isinstance(note_map, dict):
        for wrap in note_map.values():
            if isinstance(wrap, dict) and isinstance(wrap.get("note"), dict):
                return wrap["note"]
    items = (((state.get("feed") or {}).get("undertakeNote") or {}).get("items") or [])
    if isinstance(items, list):
        for item in items:
            card = (item or {}).get("noteCard") if isinstance(item, dict) else None
            if isinstance(card, dict):
                return card
    raise RuntimeError("未获取到数据")


def _first_url(*candidates: Any) -> str:
    for value in candidates:
        if isinstance(value, str) and value.startswith("http"):
            return _https(value)
        if isinstance(value, list):
            found = _first_url(*value)
            if found:
                return found
    return ""


def _pick_from_stream(stream: Any) -> str:
    if not isinstance(stream, dict):
        return ""
    for codec in ("h264", "h265", "h266", "av1"):
        items = stream.get(codec) or []
        if not isinstance(items, list) or not items:
            continue
        chosen = next((it for it in items if isinstance(it, dict) and it.get("defaultStream")), None)
        if not isinstance(chosen, dict):
            chosen = next((it for it in items if isinstance(it, dict)), None)
        if not isinstance(chosen, dict):
            continue
        url = _first_url(
            chosen.get("masterUrl"),
            chosen.get("master_url"),
            chosen.get("backupUrls"),
            chosen.get("backup_urls"),
        )
        if url:
            return url
    return ""


def _pick_video_url(video: Any) -> str:
    if not isinstance(video, dict):
        return ""
    url = _pick_from_stream((video.get("media") or {}).get("stream"))
    if url:
        return url
    media_v2 = video.get("mediaV2")
    if isinstance(media_v2, str) and media_v2.strip():
        try:
            parsed = json.loads(media_v2)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            url = _pick_from_stream(parsed.get("stream"))
            if url:
                return url
            opaque = ((parsed.get("video") or {}).get("opaque1") or {})
            url = _first_url(opaque.get("default_screencast_stream") if isinstance(opaque, dict) else "")
            if url:
                return url
    return ""


def _detail_from_note(note: dict[str, Any]) -> XhsDetail:
    user = note.get("user") or {}
    if not isinstance(user, dict):
        user = {}
    images: list[str] = []
    for im in note.get("imageList") or []:
        if not isinstance(im, dict):
            continue
        url = _first_url(im.get("urlDefault"), im.get("url"), im.get("urlPre"))
        if url:
            images.append(url)
    video_url = _pick_video_url(note.get("video"))
    video_urls = [video_url] if video_url else []
    download_urls = list(images)
    if video_url and video_url not in download_urls:
        download_urls.append(video_url)
    tags: list[str] = []
    for tag in note.get("tagList") or []:
        if isinstance(tag, dict):
            name = str(tag.get("name") or "").strip()
        else:
            name = str(tag).strip()
        if name:
            tags.append(name)
    return XhsDetail(
        id=str(note.get("noteId") or note.get("id") or ""),
        title=str(note.get("title") or note.get("displayTitle") or note.get("display_title") or ""),
        description=str(note.get("desc") or ""),
        push_time=_format_ms(note.get("time")),
        update_time=_format_ms(note.get("lastUpdateTime")),
        username=str(user.get("nickname") or user.get("nickName") or user.get("nick_name") or ""),
        userid=str(user.get("userId") or user.get("userid") or user.get("user_id") or ""),
        download_urls=download_urls,
        tags=" ".join(tags),
        avatar=_https(str(user.get("avatar") or "")),
        video_urls=video_urls,
    )


def _fetch_html(url: str) -> str:
    with curl_session() as session:
        resp = session.get(
            url,
            headers={"Accept": _ACCEPT, "Accept-Language": "zh-CN,zh;q=0.9"},
            allow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text or ""


class XhsLogic:
    @staticmethod
    async def detail(url: str) -> XhsDetail:
        html = await asyncio.to_thread(_fetch_html, url)
        note = _note_from_state(_parse_initial_state(html))
        detail = _detail_from_note(note)
        if not detail.id and not detail.title and not detail.description:
            raise RuntimeError("未获取到数据")
        return detail
