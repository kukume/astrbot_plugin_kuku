from __future__ import annotations

import asyncio

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ..di.commands import cmd
from ..logic.grok_logic import GrokLogic
from ..logic.openai_logic import OpenaiLogic
from ..utils.helpers import extract_image_urls
from ..utils.http_client import http_client

_image_lock = asyncio.Lock()
_video_lock = asyncio.Lock()


class OpenaiCommands:
    @cmd("image")
    async def image(self, event: AstrMessageEvent, prompt: str):
        """AI 画图。用法: image a cat"""
        prompt = (prompt or "").strip()
        if not prompt:
            yield event.plain_result("请输入 prompt")
            return
        urls = extract_image_urls(event)
        if len(urls) > 1:
            yield event.plain_result("一次只能处理一张图片")
            return
        async with _image_lock:
            yield event.plain_result("生成图片中")
            if len(urls) == 1:
                img_bytes = (await http_client.get(urls[0], follow_redirects=True)).content
                b64 = await OpenaiLogic.image(prompt, img_bytes)
            else:
                b64 = await OpenaiLogic.image(prompt)
            yield event.chain_result([Comp.Image.fromBase64(b64)])

    @cmd("video")
    async def video(self, event: AstrMessageEvent, prompt: str):
        """AI 视频。用法: video a cat running"""
        prompt = (prompt or "").strip()
        if not prompt:
            yield event.plain_result("请输入 prompt")
            return
        async with _video_lock:
            yield event.plain_result("生成视频中")
            url = await GrokLogic.video(prompt)
            yield event.chain_result([Comp.Video.fromURL(url)])
