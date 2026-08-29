from __future__ import annotations

from dataclasses import dataclass

from ..utils.http_client import http_client
from ..utils.login_utils import (
    QrcodeExpireException,
    QrcodeNotScannedException,
    QrcodeScannedException,
    location_of,
    render_set_cookie,
)

_REFERER = (
    "https://passport.weibo.com/sso/signin?entry=wapsso&source=wapssowb"
    "&url=https%3A%2F%2Fm.weibo.cn%2F"
)


@dataclass
class WeiboQrcode:
    qr_id: str
    image: str


@dataclass
class WeiboLogin:
    cookie: str


class WeiboLogic:
    @staticmethod
    async def login1() -> WeiboQrcode:
        data = (
            await http_client.get(
                "https://passport.weibo.com/sso/v2/qrcode/image?entry=wapsso&size=180",
                headers={"Referer": _REFERER},
            )
        ).json()["data"]
        return WeiboQrcode(qr_id=data["qrid"], image=data["image"])

    @staticmethod
    async def login2(qrcode: WeiboQrcode) -> WeiboLogin:
        node = (
            await http_client.get(
                "https://passport.weibo.com/sso/v2/qrcode/check"
                f"?entry=wapsso&source=wapssowb&url=https:%2F%2Fm.weibo.cn%2F&qrid={qrcode.qr_id}",
                headers={"Referer": _REFERER},
            )
        ).json()
        code = int(node["retcode"])
        if code == 50114001:
            raise QrcodeNotScannedException()
        if code == 50114002:
            raise QrcodeScannedException()
        if code != 20000000:
            raise QrcodeExpireException("微博二维码已过期")
        url = node["data"]["url"]
        response = await http_client.get(url)
        for _ in range(5):
            loc = location_of(response)
            if not loc:
                break
            response = await http_client.get(loc, headers={"Referer": "https://passport.weibo.com/"})
        return WeiboLogin(cookie=render_set_cookie(response))
