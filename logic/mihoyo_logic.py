from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..utils.http_client import http_client
from ..utils.login_utils import (
    QrcodeNotScannedException,
    find_set_cookie,
    random_letter,
    render_set_cookie,
)


@dataclass
class MiHoYoFix:
    referer: str = "https://user.miyoushe.com/"
    fp: str = field(default_factory=lambda: random_letter(13))
    device: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
    )
    app: str = "bll8iq97cem8"

    def append_headers(self) -> dict[str, str]:
        return {
            "referer": self.referer,
            "X-Rpc-Device_fp": self.fp,
            "X-Rpc-Device_id": self.device,
            "User-Agent": self.user_agent,
            "X-Rpc-App_id": self.app,
        }


@dataclass
class MiHoYoQrcode:
    fix: MiHoYoFix
    url: str
    ticket: str


@dataclass
class MiHoYoLogin:
    fix: MiHoYoFix
    cookie: str | None
    aid: str
    mid: str
    token: str | None
    ticket: str | None
    s_token: str | None = None


class MiHoYoLogic:
    @staticmethod
    def _check(node: dict) -> None:
        if int(node.get("retcode", -1)) != 0:
            raise RuntimeError(node.get("message") or "米哈游请求失败")

    @classmethod
    async def qrcode_login1(cls) -> MiHoYoQrcode:
        fix = MiHoYoFix()
        node = (
            await http_client.post(
                "https://passport-api.miyoushe.com/account/ma-cn-passport/web/createQRLogin",
                headers=fix.append_headers(),
            )
        ).json()
        cls._check(node)
        data = node["data"]
        return MiHoYoQrcode(fix=fix, url=data["url"], ticket=data["ticket"])

    @classmethod
    async def qrcode_login2(cls, qrcode: MiHoYoQrcode) -> MiHoYoLogin:
        response = await http_client.post(
            "https://passport-api.miyoushe.com/account/ma-cn-passport/web/queryQRLoginStatus",
            json={"ticket": qrcode.ticket},
            headers=qrcode.fix.append_headers(),
        )
        node = response.json()
        cls._check(node)
        data = node["data"]
        status = data["status"]
        if status in ("Created", "Scanned"):
            raise QrcodeNotScannedException()
        if status != "Confirmed":
            raise RuntimeError(f"米游社登陆失败，未知的状态：{status}")
        cookie = render_set_cookie(response)
        login_response = await http_client.post(
            "https://bbs-api.miyoushe.com/user/wapi/login",
            json={"gids": "2"},
            headers={**qrcode.fix.append_headers(), "Cookie": cookie},
        )
        cls._check(login_response.json())
        cookie += render_set_cookie(login_response)
        user = data["user_info"]
        token = find_set_cookie(login_response, "cookie_token_v2") or ""
        return MiHoYoLogin(
            fix=qrcode.fix,
            cookie=cookie,
            aid=str(user["aid"]),
            mid=str(user["mid"]),
            token=token,
            ticket=None,
        )
