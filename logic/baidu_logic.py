from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import httpx

from ..utils.http_client import http_client
from ..utils.login_utils import (
    QrcodeNotScannedException,
    QrcodeScannedException,
    jsonp_to_json,
    render_set_cookie,
)


@dataclass
class BaiduQrcode:
    image: str
    sign: str
    uuid: str


@dataclass
class BaiduPojo:
    cookie: str


class BaiduLogic:
    @staticmethod
    async def get_qrcode() -> BaiduQrcode:
        gid = str(uuid.uuid4())
        now = int(time.time() * 1000)
        text = (
            await http_client.get(
                "https://passport.baidu.com/v2/api/getqrcode"
                f"?lp=pc&qrloginfrom=pc&gid={gid}&callback=tangram_guid_{now}"
                f"&apiver=v3&tt={now}&tpl=mn"
                "&logPage=traceId:pc_loginv5_1653405990,logPage:loginv5"
                f"&_={now}"
            )
        ).text
        node = jsonp_to_json(text)
        return BaiduQrcode(image="https://" + node["imgurl"], sign=node["sign"], uuid=gid)

    @staticmethod
    async def check_qrcode(qrcode: BaiduQrcode) -> BaiduPojo:
        now = int(time.time() * 1000)
        try:
            text = (
                await http_client.get(
                    "https://passport.baidu.com/channel/unicast"
                    f"?channel_id={qrcode.sign}&gid={qrcode.uuid}&tpl=mn&_sdkFrom=1"
                    f"&callback=tangram_guid_{now}&apiver=v3&tt={now}&_={now}",
                    timeout=15.0,
                )
            ).text
            node = jsonp_to_json(text)
        except httpx.TimeoutException:
            raise QrcodeNotScannedException() from None
        errno = int(node["errno"])
        if errno == 1:
            raise QrcodeNotScannedException()
        if errno != 0:
            raise RuntimeError("未知错误")
        ss = json.loads(node["channel_v"])
        if int(ss["status"]) != 0:
            raise QrcodeScannedException()
        response = await http_client.get(
            f"https://passport.baidu.com/v3/login/main/qrbdusslogin?v={now}&bduss={ss['v']}"
        )
        return BaiduPojo(cookie=render_set_cookie(response))
