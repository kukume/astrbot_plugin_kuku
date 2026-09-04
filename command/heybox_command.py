from __future__ import annotations

import base64

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..di.commands import cmd, on_group
from ..logic.card_image import render_heybox
from ..logic.heybox_logic import (
    HeyboxDetail,
    HeyboxLogic,
    extract_heybox_url,
    is_heybox_url,
    is_heybox_video_url,
)
from ..utils.helpers import find_json_segment, png_component


def _jump_url(node: dict) -> str:
    meta = node.get("meta") or {}
    for key in ("news", "detail_1", "music", "video"):
        block = meta.get(key) or {}
        if not isinstance(block, dict):
            continue
        for field in ("jumpUrl", "qqdocurl", "preview", "url"):
            url = block.get(field)
            if isinstance(url, str) and url:
                return url
    return ""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def _media_node(uin: str, name: str, url: str, *, video: bool) -> Comp.Node | None:
    try:
        data = await HeyboxLogic.fetch_media(url)
        if not data:
            return None
        if video:
            b64 = _b64(data)
            if hasattr(Comp.Video, "fromBase64"):
                seg = Comp.Video.fromBase64(b64)
            else:
                seg = Comp.Video(file="base64://" + b64)
        else:
            seg = png_component(data)
        return Comp.Node(uin=uin, name=name, content=[seg])
    except Exception as e:
        logger.warning(f"heybox media fetch failed: {e}")
        return None


async def _build_heybox_forward(event: AstrMessageEvent, detail: HeyboxDetail) -> Comp.Nodes:
    lines = [
        f"标题：{detail.title}",
        f"正文：{detail.description}",
        f"作者：{detail.username}",
        f"时间：{detail.push_time}",
    ]
    if detail.ip_location:
        lines.append(f"IP：{detail.ip_location}")
    if detail.topic:
        lines.append(f"社区：{detail.topic}")
    text = "\n".join(lines)
    uin = event.get_self_id()
    name = "小黑盒"
    nodes = [Comp.Node(uin=uin, name=name, content=[Comp.Plain(text)])]
    for video in detail.video_urls:
        node = await _media_node(uin, name, video, video=True)
        if node:
            nodes.append(node)
    for image in detail.images:
        if is_heybox_video_url(image):
            continue
        node = await _media_node(uin, name, image, video=False)
        if node:
            nodes.append(node)
    return Comp.Nodes(nodes)


class HeyboxCommands:
    @cmd("xhh", alias={"heybox"})
    async def xhh(self, event: AstrMessageEvent, url: str):
        """小黑盒帖子解析。用法: xhh https://www.xiaoheihe.cn/app/bbs/link/..."""
        url = (url or "").strip()
        if not url:
            yield event.plain_result("请提供小黑盒链接")
            return
        detail = await HeyboxLogic.detail(url)
        yield event.chain_result([await _build_heybox_forward(event, detail)])
        try:
            pic = await render_heybox(url, detail)
            yield event.chain_result([png_component(pic)])
        except Exception as e:
            logger.warning(f"heybox render failed: {e}")

    @on_group()
    async def check_heybox_card(self, event: AstrMessageEvent):
        """自动识别小黑盒卡片或链接"""
        url = ""
        json_node = find_json_segment(event)
        if json_node:
            url = _jump_url(json_node)
        if not url:
            url = extract_heybox_url(event.message_str or "")
        if not url or not is_heybox_url(url):
            return
        try:
            detail = await HeyboxLogic.detail(url)
            yield event.chain_result([await _build_heybox_forward(event, detail)])
            pic = await render_heybox(url, detail)
            yield event.chain_result([png_component(pic)])
            event.stop_event()
        except Exception as e:
            logger.warning(f"check heybox card failed: {e}")
