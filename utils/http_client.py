from __future__ import annotations

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_client: httpx.AsyncClient | None = None


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=20.0),
        follow_redirects=False,
        headers={"User-Agent": DEFAULT_UA},
    )


def get_http_client() -> httpx.AsyncClient:
    """获取可用 client；已关闭则自动新建。"""
    global _client
    if _client is None or _client.is_closed:
        _client = _new_client()
    return _client


def reset_http_client() -> None:
    """丢弃当前 client（不在这里 await aclose，避免事件循环问题）。下次 get 会新建。"""
    global _client
    _client = None


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        try:
            await _client.aclose()
        except Exception:
            pass
    _client = None


class _HttpClientProxy:
    """兼容旧代码：await http_client.get/post(...)"""

    def _client(self) -> httpx.AsyncClient:
        return get_http_client()

    async def get(self, *args, **kwargs):
        return await self._client().get(*args, **kwargs)

    async def post(self, *args, **kwargs):
        return await self._client().post(*args, **kwargs)

    async def put(self, *args, **kwargs):
        return await self._client().put(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        return await self._client().delete(*args, **kwargs)

    async def request(self, *args, **kwargs):
        return await self._client().request(*args, **kwargs)

    async def stream(self, *args, **kwargs):
        return self._client().stream(*args, **kwargs)

    async def aclose(self) -> None:
        await close_http_client()

    def __getattr__(self, name: str):
        # 其它属性转发到当前 client
        return getattr(self._client(), name)


http_client = _HttpClientProxy()
