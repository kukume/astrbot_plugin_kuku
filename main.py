from __future__ import annotations

import sys
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register

# 保证 logic/command/utils/di 可导入
PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from di.commands import registry  # noqa: E402
from di.container import container  # noqa: E402
from di.wiring import setup_commands  # noqa: E402
from utils.config_holder import set_config  # noqa: E402


@register(
    "astrbot_plugin_kuku",
    "kuku",
    "移植自 onebot 的工具指令集",
    "1.0.0",
)
class Main(Star):
    """
    插件入口。指令全部由 di.registry 动态注入（含 rate）。

    setup_commands() → registry.bind(Main)
    bind 在主模块 exec 出包装函数再套 @filter，等价于写在 main.py。
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        set_config(dict(self.config))
        container.set("config", self.config)
        container.set("context", context)
        logger.info("astrbot_plugin_kuku 已加载")

    async def initialize(self):
        set_config(dict(self.config or {}))
        container.set("config", self.config or {})
        Path("tmp").mkdir(parents=True, exist_ok=True)
        logger.info("astrbot_plugin_kuku 初始化完成")

    async def terminate(self):
        container.clear()
        logger.info("astrbot_plugin_kuku 已卸载")


# ---- 动态注入全部指令（含 rate）----
setup_commands()
registry.bind(Main)
