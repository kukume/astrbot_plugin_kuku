from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.config_holder import get_config
from ..utils.http_client import http_client


@dataclass
class XhsDetail:
    id: str
    title: str
    description: str
    push_time: str
    update_time: str
    username: str
    userid: str
    download_urls: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "XhsDetail":
        return cls(
            id=str(data.get("作品ID") or data.get("id") or ""),
            title=str(data.get("作品标题") or data.get("title") or ""),
            description=str(data.get("作品描述") or data.get("description") or ""),
            push_time=str(data.get("发布时间") or data.get("push_time") or ""),
            update_time=str(data.get("最后更新时间") or data.get("update_time") or ""),
            username=str(data.get("作者昵称") or data.get("username") or ""),
            userid=str(data.get("作者ID") or data.get("userid") or ""),
            download_urls=list(data.get("下载地址") or data.get("download_urls") or []),
        )


class XhsLogic:
    @staticmethod
    async def detail(url: str) -> XhsDetail:
        base = (get_config("xhs_url") or "http://localhost:5556").rstrip("/")
        resp = await http_client.no_proxy.post(
            f"{base}/xhs/detail",
            json={"url": url},
            follow_redirects=True,
        )
        json_node = resp.json()
        data = json_node.get("data")
        if not data:
            raise RuntimeError("未获取到数据")
        return XhsDetail.from_dict(data)
