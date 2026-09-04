from __future__ import annotations

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..di.commands import cmd, on_group
from ..logic.card_image import render_xhs
from ..logic.xhs_logic import XhsDetail, XhsLogic, is_xhs_url, is_xhs_video_url
from ..utils.helpers import find_json_segment, png_component


def _build_xhs_forward(event: AstrMessageEvent, detail: XhsDetail) -> Comp.Nodes:
    text = (
        f"标题：{detail.title}\n描述：{detail.description}\n"
        f"最后更新时间：{detail.update_time}\n作者：{detail.username}"
    )
    uin = event.get_self_id()
    name = "小红书"
    videos = list(detail.video_urls) or [u for u in detail.download_urls if is_xhs_video_url(u)]
    images = [u for u in detail.download_urls if u not in videos and not is_xhs_video_url(u)]
    nodes = [Comp.Node(uin=uin, name=name, content=[Comp.Plain(text)])]
    for video in videos:
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Video.fromURL(video)]))
    for image in images:
        nodes.append(Comp.Node(uin=uin, name=name, content=[Comp.Image.fromURL(image)]))
    return Comp.Nodes(nodes)


class XhsCommands:
    @cmd("xhs")
    async def xhs(self, event: AstrMessageEvent, url: str):
        """小红书解析。用法: xhs https://..."""
        url = (url or "").strip()
        if not url:
            yield event.plain_result("请提供小红书链接")
            return
        detail = await XhsLogic.detail(url)
        yield event.chain_result([_build_xhs_forward(event, detail)])
        try:
            pic = await render_xhs(url, detail)
            yield event.chain_result([png_component(pic)])
        except Exception as e:
            logger.warning(f"xhs render failed: {e}")

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
            if not is_xhs_url(url):
                return
            detail = await XhsLogic.detail(url)
            yield event.chain_result([_build_xhs_forward(event, detail)])
            pic = await render_xhs(url, detail)
            yield event.chain_result([png_component(pic)])
            event.stop_event()
        except Exception as e:
            logger.warning(f"check xhs card failed: {e}")
