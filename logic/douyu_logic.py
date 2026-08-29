from __future__ import annotations

import time
from dataclasses import dataclass

from ..utils.http_client import http_client
from ..utils.login_utils import QrcodeNotScannedException, render_set_cookie

_REFERER = (
    "https://passport.douyu.com/index/login?passport_reg_callback=PASSPORT_REG_SUCCESS_CALLBACK"
    "&passport_login_callback=PASSPORT_LOGIN_SUCCESS_CALLBACK"
    "&passport_close_callback=PASSPORT_CLOSE_CALLBACK"
    "&passport_dp_callback=PASSPORT_DP_CALLBACK&type=login&client_id=1"
    "&state=https%3A%2F%2Fwww.douyu.com%2F"
)


@dataclass
class DouYuQrcode:
    url: str
    code: str


@dataclass
class DouYuLogin:
    cookie: str


class DouYuLogic:
    @staticmethod
    async def get_qrcode() -> DouYuQrcode:
        node = (
            await http_client.post(
                "https://passport.douyu.com/scan/generateCode",
                data={"client_id": "1", "isMultiAccount": "0"},
                headers={"Referer": _REFERER},
            )
        ).json()
        if int(node["error"]) != 0:
            raise RuntimeError(str(node.get("data") or "斗鱼获取二维码失败"))
        data = node["data"]
        return DouYuQrcode(url=data["url"], code=data["code"])

    @staticmethod
    async def check_qrcode(qrcode: DouYuQrcode) -> DouYuLogin:
        check = await http_client.get(
            f"https://passport.douyu.com/japi/scan/auth?time={int(time.time() * 1000)}&code={qrcode.code}",
            headers={"Referer": _REFERER},
        )
        node = check.json()
        err = int(node["error"])
        if err in (-2, 1):
            raise QrcodeNotScannedException()
        if err != 0:
            raise RuntimeError(str(node.get("data") or "斗鱼登录失败"))
        url = node["data"]["url"]
        response = await http_client.get(
            f"{url}&callback=appClient_json_callback&_={int(time.time() * 1000)}",
            headers={"Referer": "https://passport.douyu.com/"},
        )
        return DouYuLogin(cookie=render_set_cookie(response) + render_set_cookie(check))
