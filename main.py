from __future__ import annotations

import sys
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star, register

PLUGIN_DIR = Path(__file__).resolve().parent


def _drop_legacy_top_level_modules() -> None:
    """
    旧版曾把插件目录塞进 sys.path，子模块会以 di / command / logic / utils
    这类顶层名留在 sys.modules 里。AstrBot 热重载只清 data.plugins.<插件>.*，
    那些顶层名清不掉，更新后仍会复用旧代码。加载和卸载时按文件路径清掉。
    """
    plugin_root = str(PLUGIN_DIR)
    while plugin_root in sys.path:
        sys.path.remove(plugin_root)

    prefix = __package__ or ""
    for name, mod in list(sys.modules.items()):
        if name in {"__main__", "__mp_main__"}:
            continue
        if prefix and (name == prefix or name.startswith(prefix + ".")):
            continue
        file = getattr(mod, "__file__", None)
        if not file:
            continue
        try:
            resolved = Path(file).resolve()
        except OSError:
            continue
        if resolved.is_relative_to(PLUGIN_DIR):
            del sys.modules[name]


_drop_legacy_top_level_modules()

from .di.commands import registry  # noqa: E402
from .di.container import container  # noqa: E402
from .di.wiring import setup_commands  # noqa: E402
from .utils.config_holder import set_config  # noqa: E402


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
        _drop_legacy_top_level_modules()
        logger.info("astrbot_plugin_kuku 已卸载")


# ---- 动态注入全部指令（含 rate）----
setup_commands()
registry.bind(Main)
