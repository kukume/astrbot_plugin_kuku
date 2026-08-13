from __future__ import annotations

from cachetools import TTLCache
from typing import Any, Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class SimpleCache(Generic[K, V]):
    def __init__(self, ttl_seconds: float, maxsize: int = 1024):
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)

    def get(self, key: K, default: V | None = None) -> V | None:
        return self._cache.get(key, default)

    def get_if_present(self, key: K) -> V | None:
        return self._cache.get(key)

    def put(self, key: K, value: V) -> None:
        self._cache[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._cache


class CacheManager:
    _caches: dict[str, SimpleCache[Any, Any]] = {}

    @classmethod
    def get_cache(cls, key: str, ttl_seconds: float = 3600) -> SimpleCache[Any, Any]:
        cache = cls._caches.get(key)
        if cache is None:
            cache = SimpleCache(ttl_seconds=ttl_seconds)
            cls._caches[key] = cache
        return cache
