from __future__ import annotations

from dataclasses import dataclass

from ..utils.http_client import http_client
from ..utils.login_utils import (
    QrcodeExpireException,
    QrcodeNotScannedException,
    random_num,
    render_set_cookie,
)


@dataclass
class HuYaQrcode:
    url: str
    id: str
    cookie: str
    request_id: str


@dataclass
class HuYaLogin:
    cookie: str


class HuYaLogic:
    @staticmethod
    async def get_qrcode() -> HuYaQrcode:
        request_id = random_num(8)
        response = await http_client.post(
            "https://udblgn.huya.com/qrLgn/getQrId",
            content=(
                '{"uri":"70001","version":"2.4",'
                '"context":"WB-b11031a6ccf245169759e35fc6adc5d9-C9D11B3412B00001BAEA164B1FD4176D-",'
                f'"requestId":"{request_id}","appId":"5002",'
                '"data":{"behavior":"%7B%22a%22%3A%22m%22%2C%22w%22%3A520%2C%22h%22%3A340%2C%22b%22%3A%5B%5D%7D",'
                '"type":"","domainList":"","page":"https%3A%2F%2Fwww.huya.com%2F"}}'
            ),
            headers={"Content-Type": "application/json"},
        )
        qr_id = response.json()["data"]["qrId"]
        return HuYaQrcode(
            url=f"https://udblgn.huya.com/qrLgn/getQrImg?k={qr_id}&appId=5002",
            id=qr_id,
            cookie=render_set_cookie(response),
            request_id=request_id,
        )

    @staticmethod
    async def check_qrcode(qrcode: HuYaQrcode) -> HuYaLogin:
        response = await http_client.post(
            "https://udblgn.huya.com/qrLgn/tryQrLogin",
            content=(
                '{"uri":"70003","version":"2.4",'
                '"context":"WB-b11031a6ccf245169759e35fc6adc5d9-C9D11B3412B00001BAEA164B1FD4176D-",'
                f'"requestId":"{qrcode.request_id}","appId":"5002",'
                f'"data":{{"qrId":"{qrcode.id}","remember":"1","domainList":"",'
                '"behavior":"%7B%22a%22%3A%22m%22%2C%22w%22%3A520%2C%22h%22%3A340%2C%22b%22%3A%5B%5D%7D",'
                '"page":"https%3A%2F%2Fwww.huya.com%2F"}}'
            ),
            headers={"Content-Type": "application/json", "Cookie": qrcode.cookie},
        )
        stage = int(response.json()["data"]["stage"])
        if stage in (0, 1):
            raise QrcodeNotScannedException()
        if stage == 2:
            return HuYaLogin(cookie=render_set_cookie(response))
        if stage == 5:
            raise QrcodeExpireException("虎牙登录二维码已过期")
        raise RuntimeError(f"错误代码为{stage}")
