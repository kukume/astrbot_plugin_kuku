"""扫描 command 包，收集 @cmd / @on_group 装饰的 handler。"""

from __future__ import annotations

from astrbot.api import logger

from di.commands import registry


def setup_commands() -> None:
    """import command/* 并自动注册装饰器标记的指令。"""
    registry.clear()
    n = registry.collect_from_command_package("command")
    logger.info(f"[wiring] 从 command 包扫描到 {n} 个 handler")
