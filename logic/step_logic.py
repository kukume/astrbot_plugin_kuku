from __future__ import annotations

import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from ..utils.http_client import http_client
from ..utils.login_utils import aes_cbc_encrypt, location_of, md5, render_set_cookie
from ..utils.regex_utils import extract


@dataclass
class LeXinStepLogin:
    lei_xin_cookie: str
    le_xin_userid: str
    lei_xin_access_token: str


@dataclass
class XiaomiStepLogin:
    mi_login_token: str


class LeXinStepLogic:
    @staticmethod
    async def login(phone: str, password: str) -> LeXinStepLogin:
        new_password = password if len(password) == 32 else md5(password)
        response = await http_client.post(
            "https://sports.lifesense.com/sessions_service/login"
            "?screenHeight=2267&screenWidth=1080&systemType=2&version=4.5",
            json={
                "password": new_password,
                "clientId": str(uuid.uuid4()),
                "appType": 6,
                "loginName": phone,
                "roleType": 0,
            },
        )
        node = response.json()
        if int(node.get("code") or 0) != 200:
            raise RuntimeError(node.get("msg") or "乐心运动登录失败")
        data = node["data"]
        return LeXinStepLogin(
            lei_xin_cookie=render_set_cookie(response),
            le_xin_userid=str(data["userId"]),
            lei_xin_access_token=str(data["accessToken"]),
        )


class XiaomiStepLogic:
    _UA = "MiFit6.14.0 (24129PN74C; Android 16; Density/2.75)"

    @classmethod
    async def login(cls, phone: str, password: str) -> XiaomiStepLogin:
        payload = {
            "emailOrPhone": f"+86{phone}",
            "password": password,
            "state": "REDIRECTION",
            "client_id": "HuaMi",
            "country_code": "CN",
            "token": "access",
            "region": "cn-northwest-1",
            "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
        }
        encrypted = aes_cbc_encrypt(urlencode(payload), "xeNtBVqzDc6tuNTh", "MAAAYAAAAAAAAABg")
        response = await http_client.post(
            "https://api-user.zepp.com/v2/registrations/tokens",
            content=encrypted,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "x-hm-ekv": "1",
                "app_name": "com.xiaomi.hm.health",
                "appname": "com.xiaomi.hm.health",
                "appplatform": "android_phone",
                "User-Agent": cls._UA,
            },
        )
        location_url = location_of(response)
        if not location_url:
            raise RuntimeError("登录失败")
        access = extract(location_url, "access=", "&")
        if not access:
            raise RuntimeError("账号或者密码错误")
        ss = {
            "app_name": "com.xiaomi.hm.health",
            "country_code": "CN",
            "code": access,
            "device_id": "37:83:85:5a:e8:93",
            "device_model": "android_phone",
            "app_version": "6.14.0",
            "grant_type": "access_token",
            "allow_registration": "false",
            "dn": (
                "account.huami.com,api-user.huami.com,api-watch.huami.com,"
                "api-analytics.huami.com,app-analytics.huami.com,api-mifit.huami.com"
            ),
            "third_name": "huami_phone",
            "source": "com.xiaomi.hm.health:4.5.0:50340",
        }
        node = (
            await http_client.post(
                "https://account.huami.com/v2/client/login",
                data=ss,
                headers={"User-Agent": cls._UA},
            )
        ).json()
        return XiaomiStepLogin(mi_login_token=node["token_info"]["login_token"])
