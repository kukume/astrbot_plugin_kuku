from __future__ import annotations

import base64

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from utils.helpers import find_json_segment, zhihu_pic
from di.commands import cmd, on_group
from logic.xhs_logic import XhsDetail, XhsLogic


def _build_xhs_chain(detail: XhsDetail) -> list:
    text = (
        f"标题：{detail.title}\n描述：{detail.description}\n"
        f"最后更新时间：{detail.update_time}\n作者：{detail.username}"
    )
    chain: list = [Comp.Plain(text)]
    images = [u for u in detail.download_urls if not u.endswith(".mp4")]
    videos = [u for u in detail.download_urls if u.endswith(".mp4")]
    for video in videos:
        chain.append(Comp.Video.fromURL(video))
    for image in images:
        chain.append(Comp.Image.fromURL(image))
    return chain


class XhsCommands:
    @cmd("xhs")
    async def xhs(self, event: AstrMessageEvent, url: str):
        """小红书解析。用法: xhs https://..."""
        full = (event.message_str or "").strip()
        for prefix in ("xhs", "/xhs"):
            if full.lower().startswith(prefix):
                full = full[len(prefix) :].strip()
                break
        if full and (not url or len(full) > len(url)):
            url = full
        if not url:
            yield event.plain_result("请提供小红书链接")
            return
        try:
            detail = await XhsLogic.detail(url)
            yield event.chain_result(_build_xhs_chain(detail))
            pic = await zhihu_pic(url)
            b64 = base64.b64encode(pic).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @on_group()
    async def check_xhs_card(self, event: AstrMessageEvent):
        """自动识别小红书卡片"""
        json_node = find_json_segment(event)
        if not json_node:
            return
        try:
            url = ((json_node.get("meta") or {}).get("news") or {}).get("jumpUrl")
            if not url:
                return
            if "www.xiaohongshu.com" not in url and "xhslink.com" not in url:
                return
            detail = await XhsLogic.detail(url)
            yield event.chain_result(_build_xhs_chain(detail))
            pic = await zhihu_pic(url)
            b64 = base64.b64encode(pic).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
            event.stop_event()
        except Exception as e:
            logger.warning(f"check xhs card failed: {e}")
