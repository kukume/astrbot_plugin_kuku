from __future__ import annotations

from dataclasses import dataclass

from ..utils.http_client import DEFAULT_UA, http_client
from ..utils.login_utils import (
    QrcodeNotScannedException,
    QrcodeScannedException,
    parse_json_or_jsonp,
    render_set_cookie,
)

_REFERER = "https://zhiyou.smzdm.com/user/login/window/"


@dataclass
class SmZdmWechatQrcode:
    url: str
    scene_str: str


@dataclass
class SmZdmAppQrcode:
    url: str
    token: str


@dataclass
class SmZdmLogin:
    cookie: str


class SmZdmLogic:
    @staticmethod
    async def wechat_qrcode1() -> SmZdmWechatQrcode:
        node = parse_json_or_jsonp(
            (
                await http_client.post(
                    "https://zhiyou.smzdm.com/user/login/jsonp_weixin_qrcode_token",
                    headers={"Referer": _REFERER},
                )
            ).text
        )
        data = node["data"]
        return SmZdmWechatQrcode(url=data["QrCodeUrl"], scene_str=data["sceneStr"])

    @staticmethod
    async def wechat_qrcode2(qrcode: SmZdmWechatQrcode) -> SmZdmLogin:
        response = await http_client.post(
            "https://zhiyou.smzdm.com/user/login/jsonp_weixin_qrcode_check",
            data={"scene_str": qrcode.scene_str},
            headers={"Referer": _REFERER},
        )
        node = parse_json_or_jsonp(response.text)
        if int(node.get("error_code") or 0) != 0:
            raise RuntimeError(node.get("error_msg") or "什么值得买微信扫码失败")
        status = int(node["data"]["status"])
        if status == 1:
            raise QrcodeNotScannedException()
        if status == 2:
            raise QrcodeScannedException()
        if status != 3:
            raise RuntimeError("未知错误")
        cookie = render_set_cookie(response)
        await http_client.get(
            "https://www.smzdm.com/",
            headers={"Cookie": cookie, "User-Agent": DEFAULT_UA},
        )
        return SmZdmLogin(cookie=cookie)

    @staticmethod
    async def app_qrcode1() -> SmZdmAppQrcode:
        node = parse_json_or_jsonp(
            (
                await http_client.post(
                    "https://zhiyou.smzdm.com/user/login/jsonp_qrcode_token",
                    headers={"Referer": _REFERER},
                )
            ).text
        )
        data = node["data"]
        return SmZdmAppQrcode(url=data["url"], token=data["qrcode_token"])

    @staticmethod
    async def app_qrcode2(qrcode: SmZdmAppQrcode) -> SmZdmLogin:
        response = await http_client.post(
            "https://zhiyou.smzdm.com/user/login/jsonp_qrcode_check",
            data={"qrcode_token": qrcode.token},
            headers={"Referer": _REFERER},
        )
        node = parse_json_or_jsonp(response.text)
        if int(node.get("error_code") or 0) != 0:
            raise RuntimeError(node.get("error_msg") or "什么值得买 App 扫码失败")
        status = int(node["data"]["status"])
        if status == 1:
            raise QrcodeNotScannedException()
        if status == 2:
            raise QrcodeScannedException()
        if status != 3:
            raise RuntimeError("未知错误")
        cookie = render_set_cookie(response)
        await http_client.get(
            "https://www.smzdm.com/",
            headers={"Cookie": cookie, "User-Agent": DEFAULT_UA},
        )
        return SmZdmLogin(cookie=cookie)
