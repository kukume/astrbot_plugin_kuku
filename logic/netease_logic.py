from __future__ import annotations

import base64
import json
import random
import string
import time
from dataclasses import dataclass

from ..utils.http_client import DEFAULT_UA, http_client
from ..utils.login_utils import (
    QrcodeExpireException,
    QrcodeNotScannedException,
    QrcodeScannedException,
    aes_cbc_encrypt,
    find_set_cookie,
    md5,
    render_set_cookie,
)

_ORIGIN = "https://music.163.com"
_PRESET_KEY = "0CoJUm6Qyw8W8jud"
_IV = "0102030405060708"
_RSA_PUBEXP = int("010001", 16)
_RSA_MODULUS = int(
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7",
    16,
)
_SECRET_CHARS = string.ascii_letters + string.digits
_DEVICE_CHARS = string.ascii_letters + string.digits


@dataclass
class NetEaseQrcode:
    unikey: str
    url: str
    chain_id: str
    cookie: str


@dataclass
class NetEaseLogin:
    cookie: str


@dataclass
class NetEaseSmsSession:
    cookie: str
    phone: str
    countrycode: str


def _random_secret(n: int = 16) -> str:
    return "".join(random.choice(_SECRET_CHARS) for _ in range(n))


def _aes(text: str, key: str) -> str:
    return base64.b64encode(aes_cbc_encrypt(text, key, _IV)).decode("ascii")


def _rsa(text: str) -> str:
    message = int.from_bytes(text[::-1].encode("utf-8"), "big")
    return format(pow(message, _RSA_PUBEXP, _RSA_MODULUS), "x").zfill(256)


def _weapi(payload: dict) -> dict[str, str]:
    secret = _random_secret()
    enc_text = _aes(_aes(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), _PRESET_KEY), secret)
    return {"params": enc_text, "encSecKey": _rsa(secret)}


def _cookie_value(cookie: str, name: str) -> str:
    prefix = name + "="
    for part in cookie.split(";"):
        item = part.strip()
        if item.startswith(prefix):
            return item[len(prefix) :]
    return ""


def _merge_cookie(*parts: str) -> str:
    seen: dict[str, str] = {}
    for part in parts:
        if not part:
            continue
        for item in part.split(";"):
            item = item.strip()
            if not item or "=" not in item:
                continue
            name, value = item.split("=", 1)
            name, value = name.strip(), value.strip()
            if not name or value in {"", "deleted"}:
                continue
            seen[name] = value
    return "; ".join(f"{k}={v}" for k, v in seen.items())


def _seed_cookie() -> str:
    now = int(time.time() * 1000)
    nuid = md5(f"{now}{_random_secret(16)}")
    wnmcid = "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    sdevice = "YD-" + "".join(random.choice(_DEVICE_CHARS) for _ in range(32))
    return (
        f"_iuqxldmzr_=32; WEVNSM=1.0.0; WNMCID={wnmcid}.{now}.01.0; "
        f"_ntes_nuid={nuid}; _ntes_nnid={nuid},{now}; sDeviceId={sdevice}"
    )


def _chain_id(cookie: str) -> str:
    device = _cookie_value(cookie, "sDeviceId") or f"unknown-{random.randint(0, 999999)}"
    return f"v1_{device}_web_login_{int(time.time() * 1000)}"


def _headers(
    cookie: str,
    *,
    chain_id: str | None = None,
    login_method: str | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": DEFAULT_UA,
        "Origin": _ORIGIN,
        "Referer": f"{_ORIGIN}/",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "x-os": "web",
        "nm-gcore-status": "1",
        "x-channelsource": "undefined",
        "Cookie": cookie.strip(),
    }
    if login_method:
        headers["X-loginMethod"] = login_method
    if chain_id:
        headers["X-loginMethod"] = "QrCode"
        headers["x-login-chain-id"] = chain_id
    return headers


class NetEaseLogic:
    @classmethod
    async def _bootstrap(cls) -> str:
        home = await http_client.get(_ORIGIN + "/", headers={"Referer": f"{_ORIGIN}/"}, follow_redirects=True)
        cookie = _merge_cookie(_seed_cookie(), render_set_cookie(home))
        device = await http_client.post(
            f"{_ORIGIN}/weapi/middle/device-info/web/get",
            data=_weapi({"ydDeviceType": "WebOnline", "ydDeviceToken": ""}),
            headers=_headers(cookie),
        )
        try:
            data = device.json().get("data") or {}
        except Exception:
            data = {}
        sdevice = find_set_cookie(device, "sDeviceId") or data.get("sDeviceId")
        extra = render_set_cookie(device)
        if sdevice:
            extra = _merge_cookie(extra, f"sDeviceId={sdevice}; ")
        return _merge_cookie(cookie, extra)

    @classmethod
    async def get_qrcode(cls) -> NetEaseQrcode:
        cookie = await cls._bootstrap()
        chain_id = _chain_id(cookie)
        response = await http_client.post(
            f"{_ORIGIN}/weapi/login/qrcode/unikey",
            data=_weapi({"type": 1, "noCheckToken": True}),
            headers=_headers(cookie),
        )
        node = response.json()
        code = int(node.get("code", -1))
        if code == 8821:
            raise RuntimeError(node.get("message") or "请切换其他登录方式或升级新版本再试")
        if code != 200 or not node.get("unikey"):
            raise RuntimeError(node.get("message") or "网易云获取二维码失败")
        cookie = _merge_cookie(cookie, render_set_cookie(response))
        unikey = node["unikey"]
        url = (
            f"{_ORIGIN}/st/platform/scanlogin?codekey={unikey}"
            f"&chainId={chain_id}&hdw_device=web&hdw_appid=web&hitExp=1"
        )
        return NetEaseQrcode(unikey=unikey, url=url, chain_id=chain_id, cookie=cookie)

    @classmethod
    async def check_qrcode(cls, qrcode: NetEaseQrcode) -> NetEaseLogin:
        response = await http_client.post(
            f"{_ORIGIN}/weapi/login/qrcode/client/login",
            data=_weapi(
                {
                    "type": 1,
                    "noCheckToken": True,
                    "key": qrcode.unikey,
                    "ydDeviceToken": "",
                }
            ),
            headers=_headers(qrcode.cookie, chain_id=qrcode.chain_id),
        )
        node = response.json()
        code = int(node.get("code", -1))
        if code == 801:
            raise QrcodeNotScannedException()
        if code == 802:
            raise QrcodeScannedException()
        if code == 800:
            raise QrcodeExpireException("网易云二维码已过期")
        if code == 8821:
            raise RuntimeError(node.get("message") or "请切换其他登录方式或升级新版本再试")
        if code != 803:
            raise RuntimeError(node.get("message") or f"网易云登录失败，错误代码 {code}")
        cookie = _merge_cookie(qrcode.cookie, render_set_cookie(response))
        if "MUSIC_U=" not in cookie:
            raise RuntimeError("未获取到网易云登录 cookie")
        return NetEaseLogin(cookie=cookie)

    @classmethod
    def _login_ok(cls, response, cookie: str, node: dict) -> NetEaseLogin:
        code = int(node.get("code", -1))
        if code == 8821:
            raise RuntimeError(node.get("message") or "请切换其他登录方式或升级新版本再试")
        if code not in (200, 803) and not node.get("profile") and not (node.get("data") or {}).get("userId"):
            raise RuntimeError(node.get("message") or f"网易云登录失败，错误代码 {code}")
        cookie = _merge_cookie(cookie, render_set_cookie(response))
        if "MUSIC_U=" not in cookie:
            raise RuntimeError("未获取到网易云登录 cookie")
        return NetEaseLogin(cookie=cookie)

    @classmethod
    async def send_code(cls, phone: str, countrycode: str = "86") -> NetEaseSmsSession:
        phone = phone.strip()
        if not phone.isdigit():
            raise RuntimeError("请输入正确的手机号")
        cookie = await cls._bootstrap()
        response = await http_client.post(
            f"{_ORIGIN}/weapi/sms/captcha/sent",
            data=_weapi(
                {
                    "cellphone": phone,
                    "ctcode": countrycode,
                    "secrete": "music_user_login",
                    "noCheckToken": True,
                }
            ),
            headers=_headers(cookie),
        )
        node = response.json()
        code = int(node.get("code", -1))
        if code == -12:
            raise RuntimeError("网易云要求图形验证码，请改用扫码登录")
        if code == 8821:
            raise RuntimeError(node.get("message") or "请切换其他登录方式或升级新版本再试")
        if code != 200:
            raise RuntimeError(node.get("message") or f"网易云发送验证码失败，错误代码 {code}")
        cookie = _merge_cookie(cookie, render_set_cookie(response))
        return NetEaseSmsSession(cookie=cookie, phone=phone, countrycode=countrycode)

    @classmethod
    async def login_by_sms(cls, session: NetEaseSmsSession, captcha: str) -> NetEaseLogin:
        captcha = captcha.strip()
        if not captcha:
            raise RuntimeError("请输入验证码")
        response = await http_client.post(
            f"{_ORIGIN}/weapi/login/cellphone",
            data=_weapi(
                {
                    "countrycode": session.countrycode,
                    "phone": session.phone,
                    "captcha": captcha,
                    "rememberLogin": "true",
                    "noCheckToken": True,
                    "ydDeviceToken": "",
                }
            ),
            headers=_headers(session.cookie, login_method="Cellphone"),
        )
        node = response.json()
        return cls._login_ok(response, session.cookie, node)
