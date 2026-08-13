from __future__ import annotations

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from di.commands import cmd
from logic.ygo_logic import YgoLogic


class YgoCommands:
    @cmd("ygo")
    async def ygo(self, event: AstrMessageEvent, name: str):
        """游戏王查卡。用法: ygo 青眼白龙"""
        full = (event.message_str or "").strip()
        for prefix in ("ygo", "/ygo"):
            if full.lower().startswith(prefix):
                full = full[len(prefix) :].strip()
                break
        if full and (not name or len(full) > len(name)):
            name = full
        if not name:
            yield event.plain_result("请输入卡片名称")
            return
        try:
            card_list = await YgoLogic.search(name)
            if not card_list:
                yield event.plain_result("未找到卡片")
                return
            lines = [f"{i + 1}、{card.chinese_name}" for i, card in enumerate(card_list)]
            yield event.plain_result(
                "请发送你需要查询的卡片，\n发送序号：\n" + "\n".join(lines)
            )

            @session_waiter(timeout=60)
            async def waiter(controller: SessionController, evt: AstrMessageEvent):
                try:
                    index = int(evt.message_str.strip())
                except ValueError:
                    index = None
                if index is None or index < 1 or index > len(card_list):
                    await evt.send(evt.plain_result("请输入正确的序号"))
                    controller.keep(timeout=60, reset_timeout=True)
                    return
                card = card_list[index - 1]
                text = (
                    f"\n中文名：{card.chinese_name}\n日文名：{card.japanese_name}\n"
                    f"英文名：{card.english_name}\n效果：\n{card.effect}\n链接：{card.url}"
                )
                chain = []
                if card.image_url:
                    chain.append(Comp.Image.fromURL(card.image_url))
                chain.append(Comp.Plain(text))
                await evt.send(evt.chain_result(chain))
                controller.stop()

            try:
                await waiter(event)
            except TimeoutError:
                yield event.plain_result("选择超时")
            finally:
                event.stop_event()
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")
