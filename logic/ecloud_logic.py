from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from ..utils.http_client import http_client
from ..utils.login_utils import location_of, parse_json_or_jsonp, render_set_cookie, rsa_encrypt, rsa_encrypt_to_hex


@dataclass
class ECloudLogin:
    e_cookie: str
    cookie: str


class ECloudLogic:
    @staticmethod
    async def login(username: str, password: str) -> ECloudLogin:
        first = await http_client.get(
            "https://cloud.189.cn/api/portal/loginUrl.action"
            "?redirectURL=https%3A%2F%2Fcloud.189.cn%2Fweb%2Fredirect.html"
            "&defaultSaveName=3&defaultSaveNameCheck=uncheck"
            "&browserId=c7044c4577d2d903bbb74a956c11274d"
        )
        loc = location_of(first)
        if not loc:
            raise RuntimeError("未能成功跳转")
        second = await http_client.get(loc)
        lt_url = location_of(second)
        if not lt_url:
            raise RuntimeError("未能成功跳转")
        query = parse_qs(urlparse(lt_url).query)
        lt = (query.get("lt") or [None])[0]
        req_id = (query.get("reqId") or [None])[0]
        if not lt:
            raise RuntimeError("未能成功获取lt")
        if not req_id:
            raise RuntimeError("未能成功获取reqId")
        cookie = render_set_cookie(second)
        headers = {
            "cookie": cookie,
            "lt": lt,
            "referer": lt_url,
            "reqId": req_id,
        }
        config_node = parse_json_or_jsonp(
            (
                await http_client.post(
                    "https://open.e.189.cn/api/logbox/oauth2/appConf.do",
                    data={"version": "2.0", "appKey": "cloud"},
                    headers=headers,
                )
            ).text
        )
        encrypt_node = parse_json_or_jsonp(
            (
                await http_client.post(
                    "https://open.e.189.cn/api/logbox/config/encryptConf.do",
                    data={"appId": "cloud"},
                )
            ).text
        )
        param_id = ((config_node.get("data") or {}).get("paramId")) if isinstance(config_node, dict) else None
        if not param_id:
            raise RuntimeError("not found paramId")
        encrypt_data = encrypt_node["data"]
        pre = encrypt_data["pre"]
        pub_key = encrypt_data["pubKey"]
        need = (
            await http_client.post(
                "https://open.e.189.cn/api/logbox/oauth2/needcaptcha.do",
                data={
                    "accountType": "01",
                    "userName": pre + rsa_encrypt(username, pub_key),
                    "appKey": "cloud",
                },
            )
        ).text
        if need.strip() != "0":
            raise RuntimeError("需要验证码，请在任意设备成功登陆一次再试")
        response = await http_client.post(
            "https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do",
            data={
                "version": "v2.0",
                "apToken": "",
                "appKey": "cloud",
                "accountType": "01",
                "userName": pre + rsa_encrypt_to_hex(username, pub_key),
                "epd": pre + rsa_encrypt_to_hex(password, pub_key),
                "captchaType": "",
                "validateCode": "",
                "smsValidateCode": "",
                "captchaToken": "",
                "returnUrl": (
                    "https%3A%2F%2Fcloud.189.cn%2Fapi%2Fportal%2FcallbackUnify.action"
                    "%3FredirectURL%3Dhttps%253A%252F%252Fcloud.189.cn%252Fweb%252Fredirect.html"
                ),
                "mailSuffix": "@189.cn",
                "dynamicCheck": "FALSE",
                "clientType": "1",
                "cb_SaveName": "3",
                "isOauth2": "false",
                "state": "",
                "paramId": param_id,
            },
            headers=headers,
        )
        node = parse_json_or_jsonp(response.text)
        if str(node.get("result")) != "0":
            raise RuntimeError(node.get("msg") or "天翼云盘登录失败")
        e_cookie = render_set_cookie(response)
        login_response = await http_client.get(node["toUrl"])
        return ECloudLogin(e_cookie=e_cookie, cookie=render_set_cookie(login_response))
