from __future__ import annotations

import base64

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from di.commands import cmd, on_group
from utils.helpers import extract_url, find_json_segment, zhihu_pic


class ZhiHuCommands:
    @cmd("zh")
    async def zh(self, event: AstrMessageEvent):
        """知乎回答截图"""
        url = extract_url(
            event.message_str,
            r"https://www\.zhihu\.com/question/\d+/answer/\d+",
        )
        if not url:
            yield event.plain_result("未找到知乎回答链接")
            return
        try:
            data = await zhihu_pic(url)
            b64 = base64.b64encode(data).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @cmd("ns")
    async def ns(self, event: AstrMessageEvent):
        """NodeSeek 帖子截图"""
        url = extract_url(event.message_str, r"https://www\.nodeseek\.com/post-\d+-1")
        if not url:
            yield event.plain_result("未找到 NodeSeek 链接")
            return
        try:
            data = await zhihu_pic(url)
            b64 = base64.b64encode(data).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @cmd("ld")
    async def ld(self, event: AstrMessageEvent):
        """Linux.do 帖子截图"""
        url = extract_url(event.message_str, r"https://linux\.do/t/topic/\d+")
        if not url:
            yield event.plain_result("未找到 linux.do 链接")
            return
        try:
            data = await zhihu_pic(url)
            b64 = base64.b64encode(data).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @on_group()
    async def check_zhihu_card(self, event: AstrMessageEvent):
        """自动识别知乎小程序/卡片并截图"""
        json_node = find_json_segment(event)
        if not json_node:
            return
        try:
            url = (
                ((json_node.get("meta") or {}).get("detail_1") or {}).get("qqdocurl")
            )
            if not url or "www.zhihu.com/question" not in url:
                return
            data = await zhihu_pic(url)
            b64 = base64.b64encode(data).decode()
            yield event.chain_result([Comp.Image.fromBase64(b64)])
            event.stop_event()
        except Exception as e:
            logger.warning(f"check zhihu card failed: {e}")
