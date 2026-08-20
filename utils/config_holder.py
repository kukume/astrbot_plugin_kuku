from __future__ import annotations

from typing import Any

_config: dict[str, Any] = {}


def set_config(config: dict[str, Any] | None) -> None:
    global _config
    _config = dict(config or {})


def get_config(key: str, default: Any = None) -> Any:
    """读 AstrBot 插件配置。"""
    return _config.get(key, default)


def get_socks_proxy_url() -> str | None:
    """有 IP 和端口时返回 socks5h://host:port，否则不走代理。"""
    host = str(get_config("socks_proxy_host") or "").strip()
    try:
        port = int(get_config("socks_proxy_port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or port <= 0:
        return None
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"socks5h://{host}:{port}"
