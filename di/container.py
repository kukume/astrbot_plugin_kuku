from __future__ import annotations

from typing import Any, Callable, TypeVar

T = TypeVar("T")


class Container:
    """极简 DI（类似 Koin 单例）：按 key 懒创建、缓存实例。"""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], Any]] = {}
        self._singletons: dict[str, Any] = {}

    def single(self, key: str, factory: Callable[[], T]) -> None:
        self._factories[key] = factory
        self._singletons.pop(key, None)

    def get(self, key: str) -> Any:
        if key in self._singletons:
            return self._singletons[key]
        if key not in self._factories:
            raise KeyError(f"DI 未注册: {key}")
        inst = self._factories[key]()
        self._singletons[key] = inst
        return inst

    def set(self, key: str, instance: Any) -> None:
        """直接放入已有实例（如插件 config）。"""
        self._singletons[key] = instance

    def has(self, key: str) -> bool:
        return key in self._singletons or key in self._factories

    def clear(self) -> None:
        self._singletons.clear()


container = Container()
