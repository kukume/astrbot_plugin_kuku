from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..di.commands import cmd
from ..logic.card_image import render_linuxdo, render_nodeseek, render_v2ex
from ..utils.helpers import extract_url, png_component


class ZhiHuCommands:
    @cmd("ns")
    async def ns(self, event: AstrMessageEvent, url: str):
        """NodeSeek 帖子截图"""
        url = extract_url(url, r"https://www\.nodeseek\.com/post-\d+(?:-\d+)?") or (url or "").strip()
        if not url:
            yield event.plain_result("未找到 NodeSeek 链接")
            return
        try:
            data = await render_nodeseek(url)
        except Exception as e:
            logger.warning(f"nodeseek render failed: {e}")
            yield event.plain_result(f"生成失败: {e}")
            return
        yield event.chain_result([png_component(data)])

    @cmd("ld")
    async def ld(self, event: AstrMessageEvent, url: str):
        """Linux.do 帖子截图"""
        url = extract_url(url, r"https://linux\.do/t/[^\s]+") or (url or "").strip()
        if not url:
            yield event.plain_result("未找到 linux.do 链接")
            return
        try:
            data = await render_linuxdo(url)
        except Exception as e:
            logger.warning(f"linuxdo render failed: {e}")
            yield event.plain_result(f"生成失败: {e}")
            return
        yield event.chain_result([png_component(data)])

    @cmd("v2ex")
    async def v2ex(self, event: AstrMessageEvent, url: str):
        """V2EX 帖子截图"""
        url = extract_url(url, r"https?://(?:www\.|cn\.)?v2ex\.com/t/\d+") or (url or "").strip()
        if not url:
            yield event.plain_result("未找到 V2EX 链接")
            return
        try:
            data = await render_v2ex(url)
        except Exception as e:
            logger.warning(f"v2ex render failed: {e}")
            yield event.plain_result(f"生成失败: {e}")
            return
        yield event.chain_result([png_component(data)])
