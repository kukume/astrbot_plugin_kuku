from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot.api import logger
from astrbot.api.event import filter

# 装饰器写在方法上的元数据 key
_SPEC_ATTR = "__kuku_spec__"


@dataclass
class HandlerSpec:
    """一条待注入到 Star 子类上的 handler 描述。"""

    attr_name: str
    handler: Callable[..., Any]
    # command | group_event
    kind: str = "command"
    command_name: str | None = None
    alias: set[str] = field(default_factory=set)


def cmd(name: str, *, alias: set[str] | list[str] | None = None, attr_name: str | None = None):
    """标记一个指令 handler，扫描 command 包时自动注册。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            fn,
            _SPEC_ATTR,
            {
                "kind": "command",
                "command_name": name,
                "alias": set(alias or ()),
                "attr_name": attr_name or fn.__name__,
            },
        )
        return fn

    return decorator


def on_group(*, attr_name: str | None = None):
    """标记一个群消息事件 handler（非指令）。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            fn,
            _SPEC_ATTR,
            {
                "kind": "group_event",
                "command_name": None,
                "alias": set(),
                "attr_name": attr_name or fn.__name__,
            },
        )
        return fn

    return decorator


def _build_wrapper_source(attr_name: str, impl_key: str, handler: Callable[..., Any]) -> str:
    """
    在主模块 exec 出包装函数，签名与实现一致，方便 AstrBot 按类型注入参数。

    例：impl 为 (self, event, a: int, b: int)
    → async def xxx(self, event, a: int, b: int): ...
    """
    sig = inspect.signature(handler)
    params = list(sig.parameters.values())
    if not params:
        raise ValueError(f"handler {attr_name} 无参数")

    # 去掉 self，包装里重新声明
    rest = params[1:] if params[0].name in {"self", "cls"} else params
    # 确保第一个业务参数叫 event（AstrBot 约定）
    if not rest or rest[0].name != "event":
        # 兼容只有 (self, event) 以外的写法：强制 event 在前
        pass

    # 形参列表（含注解字符串，便于 AstrBot 解析类型）
    def _fmt(p: inspect.Parameter) -> str:
        if p.kind == inspect.Parameter.VAR_POSITIONAL:
            base = f"*{p.name}"
        elif p.kind == inspect.Parameter.VAR_KEYWORD:
            base = f"**{p.name}"
        else:
            base = p.name
        if p.annotation is not inspect.Parameter.empty:
            ann = p.annotation
            if isinstance(ann, str):
                ann_s = ann
            elif getattr(ann, "__name__", None):
                ann_s = ann.__name__
            else:
                ann_s = "str"
            # 内置/常用类型直接写名字；其它退回 str
            if ann_s not in {"str", "int", "float", "bool", "AstrMessageEvent"}:
                # 保留 typing 名或自定义名
                pass
            base = f"{base}: {ann_s}"
        if p.default is not inspect.Parameter.empty:
            base = f"{base}={p.default!r}"
        return base

    # self + 其余
    param_src = ", ".join(["self"] + [_fmt(p) for p in rest])
    # 调用 impl 时的实参：self + 其余参数名
    call_names: list[str] = ["self"]
    for p in rest:
        if p.kind == inspect.Parameter.VAR_POSITIONAL:
            call_names.append(f"*{p.name}")
        elif p.kind == inspect.Parameter.VAR_KEYWORD:
            call_names.append(f"**{p.name}")
        else:
            call_names.append(p.name)
    call_src = ", ".join(call_names)

    return (
        f"async def {attr_name}({param_src}):\n"
        f"    _impl = {impl_key}\n"
        f"    _ret = _impl({call_src})\n"
        f"    if hasattr(_ret, '__aiter__'):\n"
        f"        async for _item in _ret:\n"
        f"            yield _item\n"
        f"    else:\n"
        f"        await _ret\n"
    )


class CommandRegistry:
    """
    动态指令注册表。

    AstrBot 要求 handler 落在插件主模块上：在主模块 namespace里 exec 包装函数，
    再套 @filter 并 setattr 到 Star 子类。包装签名与实现一致，支持类型参数注入。
    """

    def __init__(self) -> None:
        self._specs: list[HandlerSpec] = []

    def clear(self) -> None:
        self._specs.clear()

    def command(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        alias: set[str] | None = None,
        attr_name: str | None = None,
    ) -> None:
        self._specs.append(
            HandlerSpec(
                attr_name=attr_name or handler.__name__,
                handler=handler,
                kind="command",
                command_name=name,
                alias=set(alias or ()),
            )
        )

    def group_event(
        self,
        handler: Callable[..., Any],
        *,
        attr_name: str | None = None,
    ) -> None:
        self._specs.append(
            HandlerSpec(
                attr_name=attr_name or handler.__name__,
                handler=handler,
                kind="group_event",
            )
        )

    def collect_from_command_package(self, package_name: str = "command") -> int:
        """
        扫描 package 下模块，收集带 @cmd / @on_group 的方法。
        """
        package = importlib.import_module(package_name)
        paths = getattr(package, "__path__", None)
        if not paths:
            raise RuntimeError(f"{package_name} 不是包")

        count = 0
        for info in pkgutil.iter_modules(paths, package_name + "."):
            mod = importlib.import_module(info.name)
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if cls.__module__ != mod.__name__:
                    continue
                for _, method in inspect.getmembers(cls, inspect.isfunction):
                    meta = getattr(method, _SPEC_ATTR, None)
                    if not meta:
                        continue
                    kind = meta["kind"]
                    if kind == "command":
                        self.command(
                            meta["command_name"],
                            method,
                            alias=meta.get("alias") or set(),
                            attr_name=meta.get("attr_name"),
                        )
                    elif kind == "group_event":
                        self.group_event(method, attr_name=meta.get("attr_name"))
                    else:
                        raise ValueError(f"unknown kind in decorator: {kind}")
                    count += 1
        return count

    def bind(self, star_cls: type) -> None:
        main_module_name = star_cls.__module__
        main_mod = sys.modules.get(main_module_name)
        if main_mod is None:
            raise RuntimeError(f"主模块未加载: {main_module_name}")

        g = main_mod.__dict__
        # 给注解用
        g.setdefault("AstrMessageEvent", None)
        try:
            from astrbot.api.event import AstrMessageEvent as _AME

            g["AstrMessageEvent"] = _AME
        except Exception:
            pass

        bound = 0
        for spec in self._specs:
            impl_key = f"_kuku_impl_{spec.attr_name}"
            g[impl_key] = spec.handler

            src = _build_wrapper_source(spec.attr_name, impl_key, spec.handler)
            exec(src, g)  # noqa: S102
            fn = g[spec.attr_name]
            fn.__doc__ = spec.handler.__doc__

            # 同步注解，便于 AstrBot 参数解析
            try:
                impl_ann = dict(getattr(spec.handler, "__annotations__", {}) or {})
                # wrapper 无 self 注解问题不大；合并 event 等
                fn.__annotations__ = impl_ann
            except Exception:
                pass

            if spec.kind == "command":
                assert spec.command_name
                kw: dict[str, Any] = {}
                if spec.alias:
                    kw["alias"] = spec.alias
                fn = filter.command(spec.command_name, **kw)(fn)
            elif spec.kind == "group_event":
                fn = filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)(fn)
            else:
                raise ValueError(f"unknown handler kind: {spec.kind}")

            setattr(star_cls, spec.attr_name, fn)
            g[spec.attr_name] = fn
            bound += 1

        logger.info(
            f"[di] 已向 {star_cls.__name__} 动态注入 {bound} 个 handler "
            f"(module={main_module_name}, typed-signature wrappers)"
        )


registry = CommandRegistry()
