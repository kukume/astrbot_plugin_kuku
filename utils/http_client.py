from __future__ import annotations

import httpx

from utils.config_holder import get_socks_proxy_url

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_UNSET = object()


def _build_async_client(proxy: str | None = None) -> httpx.AsyncClient:
    kwargs: dict = {
        "timeout": httpx.Timeout(60.0, connect=20.0),
        "follow_redirects": False,
        "headers": {"User-Agent": DEFAULT_UA},
    }
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.AsyncClient(**kwargs)


class _HttpClient:
    """配置变更后按当前 SOCKS 代理重建，其它模块持有本对象引用仍然有效。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._proxy: object = _UNSET

    def _get_client(self) -> httpx.AsyncClient:
        proxy = get_socks_proxy_url()
        if self._client is None or proxy != self._proxy:
            self._proxy = proxy
            self._client = _build_async_client(proxy)
        return self._client

    def __getattr__(self, name: str):
        return getattr(self._get_client(), name)


http_client = _HttpClient()


def curl_session(**kwargs):
    """curl_cffi Session；配置了 SOCKS 时代入 proxy。"""
    from curl_cffi import requests as cf_requests

    if "proxy" not in kwargs:
        proxy = get_socks_proxy_url()
        if proxy:
            kwargs["proxy"] = proxy
    kwargs.setdefault("impersonate", "chrome")
    return cf_requests.Session(**kwargs)
