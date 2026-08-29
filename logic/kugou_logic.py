from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass

from ..utils.http_client import http_client
from ..utils.login_utils import find_set_cookie, kugou_encrypt_mobile_code, md5


@dataclass
class KuGouLogin:
    token: str
    userid: int
    ku_goo: str
    mid: str | None


class KuGouLogic:
    @classmethod
    def mid(cls) -> str:
        def e() -> str:
            return format(int(65536 * (1 + random.random())), "x")

        return md5(f"{e()}{e()}-{e()}-{e()}-{e()}-{e()}{e()}{e()}")

    @staticmethod
    def _client_time() -> int:
        return int(time.time())

    @staticmethod
    def _signature_wrap(secret: str, params: dict[str, str], other: str = "") -> str:
        items = deque(f"{k}={v}" for k, v in params.items())
        sb = "".join(f"{k}={v}&" for k, v in params.items())
        sorted_items = deque(sorted(items))
        sorted_items.appendleft(secret)
        sorted_items.append(other)
        sorted_items.append(secret)
        return sb + "signature=" + md5("".join(sorted_items))

    @classmethod
    def _signature2(cls, params: dict[str, str], other: str = "") -> str:
        return cls._signature_wrap("NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt", params, other)

    @classmethod
    async def send_mobile_code(cls, phone: str, mid: str) -> None:
        now = int(time.time() * 1000)
        params = {
            "appid": "3116",
            "clientver": "1000",
            "clienttime": str(now),
            "mid": mid,
            "uuid": mid,
            "dfid": "-",
            "srcappid": "2919",
        }
        enc_params, pk = kugou_encrypt_mobile_code(phone, now)
        mobile = phone[:2] + "********" + phone[-1:]
        other = (
            f'{{"plat":4,"clienttime_ms":{now},"businessid":5,'
            f'"pk":"{pk}","params":"{enc_params}","mobile":"{mobile}"}}'
        )
        node = (
            await http_client.post(
                f"https://gateway.kugou.com/v8/send_mobile_code?{cls._signature2(params, other)}",
                content=other,
                headers={
                    "Content-Type": "text/plain",
                    "x-router": "loginservice.kugou.com",
                    "referer": "https://m3ws.kugou.com/",
                },
            )
        ).json()
        if int(node["error_code"]) != 0:
            raise RuntimeError(str(node.get("data") or "酷狗发送验证码失败"))

    @classmethod
    async def verify_code(cls, phone: str, code: str, mid: str) -> KuGouLogin:
        now = cls._client_time()
        params = {
            "appid": "3116",
            "clientver": "10",
            "clienttime": str(now),
            "mid": mid,
            "uuid": mid,
            "dfid": "-",
            "srcappid": "2919",
        }
        other = (
            f'{{"plat":4,"mobile":"{phone}","code":"{code}","expire_day":60,'
            '"support_multi":1,"userid":"","force_login":0}'
        )
        response = await http_client.post(
            f"https://login-user.kugou.com/v2/loginbyverifycode/?{cls._signature2(params, other)}",
            content=other,
            headers={
                "Content-Type": "text/plain",
                "x-router": "loginservice.kugou.com",
                "referer": "https://m3ws.kugou.com/",
            },
        )
        node = response.json()
        if int(node["error_code"]) != 0:
            raise RuntimeError(str(node.get("data") or "酷狗验证码登录失败"))
        ku_goo = node["data"]["value"]
        token = find_set_cookie(response, "t") or ""
        userid = find_set_cookie(response, "KugooID")
        if not userid:
            raise RuntimeError("未获取到酷狗用户ID")
        return KuGouLogin(token=token, userid=int(userid), ku_goo=ku_goo, mid=mid)
