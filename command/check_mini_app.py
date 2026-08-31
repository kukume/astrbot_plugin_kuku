from __future__ import annotations

import random

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..di.commands import on_group
from ..utils.helpers import find_json_segment

_POEMS = [
    """网页明明能直达，偏偏甩你小程序。
点开广告一大把，关都关不掉俩俩。
界面模糊乱七八，跳来跳去像傻瓜。
装作分享讲精致，其实懒癌犯大发。
劝君别做麻烦鬼，链接复制才优雅。""",
    """小程遮正道，乱点似迷宫。
网页明明在，何须绕重重？""",
    """人间本有好链接，却爱小程惹人嫌。
一跳三层开广告，半屏乱码丧心田。
转来转去无归处，点了才知被卖钱。
劝君莫做麻烦客，清清爽爽最堪怜。""",
]


class CheckMiniAppCommands:
    @on_group()
    async def check_mini_app(self, event: AstrMessageEvent):
        """检测小程序分享并吐槽 + 提取真实链接"""
        json_node = find_json_segment(event)
        if not json_node:
            return
        try:
            url = ((json_node.get("meta") or {}).get("detail_1") or {}).get("qqdocurl")
            if not url:
                return
            yield event.plain_result(f"{random.choice(_POEMS)}\n{url}")
            event.stop_event()
        except Exception as e:
            logger.warning(f"check mini app failed: {e}")
