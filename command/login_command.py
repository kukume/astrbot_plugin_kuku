from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.session_waiter import SessionController, session_waiter

from ..di.commands import cmd
from ..logic.baidu_logic import BaiduLogic
from ..logic.bilibili_logic import BiliBiliLogic
from ..logic.douyu_logic import DouYuLogic
from ..logic.ecloud_logic import ECloudLogic
from ..logic.huya_logic import HuYaLogic
from ..logic.kugou_logic import KuGouLogic
from ..logic.mihoyo_logic import MiHoYoLogic
from ..logic.netease_logic import NetEaseLogic
from ..logic.smzdm_logic import SmZdmLogic
from ..logic.step_logic import LeXinStepLogic, XiaomiStepLogic
from ..logic.weibo_logic import WeiboLogic
from ..utils.http_client import http_client
from ..utils.login_utils import QrcodeExpireException, QrcodeScanException, make_qrcode

_PLATFORMS: list[tuple[str, str]] = [
    ("baidu", "百度"),
    ("biliBili", "哔哩哔哩"),
    ("douYu", "斗鱼"),
    ("kuGou", "酷狗"),
    ("netEase", "网易云音乐"),
    ("miHoYo", "米哈游"),
    ("step", "刷步数"),
    ("weibo", "微博"),
    ("smZdm", "什么值得买"),
    ("eCloud", "天翼云盘"),
    ("huYa", "虎牙"),
]

_METHODS: dict[str, list[tuple[str, str]]] = {
    "baidu": [("qr", "使用百度系APP扫码登录")],
    "biliBili": [("qr", "使用哔哩哔哩APP扫码登录")],
    "douYu": [("qr", "使用斗鱼APP扫码登录")],
    "kuGou": [("sms", "使用手机验证码登录")],
    "netEase": [
        ("qr", "使用网易云音乐APP扫码登录"),
        ("sms", "使用手机验证码登录"),
    ],
    "miHoYo": [("qr", "使用米游社APP扫码登录")],
    "step": [
        ("xiaomi", "小米运动账号密码登录"),
        ("lexin", "乐心运动账号密码登录"),
    ],
    "weibo": [("qr", "使用微博APP扫码登录")],
    "smZdm": [
        ("wechat", "使用微信扫码登录"),
        ("app", "使用APP扫码登录"),
    ],
    "eCloud": [("password", "使用密码登录")],
    "huYa": [("qr", "使用虎牙APP扫码登录")],
}

_CANCEL = {"取消", "cancel", "/取消"}


def _menu(title: str, items: list[tuple[str, str]]) -> str:
    lines = [title, "发送序号，或发送 取消："]
    for i, (_, text) in enumerate(items, 1):
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


def _pick(text: str, items: list[tuple[str, str]]) -> tuple[str, str] | None:
    raw = text.strip()
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(items):
            return items[idx - 1]
        return None
    for key, label in items:
        if raw == key or raw == label:
            return key, label
    return None


async def _send_image(evt: AstrMessageEvent, image: bytes, hint: str) -> None:
    b64 = base64.b64encode(image).decode()
    await evt.send(evt.chain_result([Comp.Image.fromBase64(b64), Comp.Plain(hint)]))


async def _download(url: str) -> bytes:
    if url.startswith("//"):
        url = "https:" + url
    resp = await http_client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


async def _send_result(evt: AstrMessageEvent, fields: dict[str, Any]) -> None:
    lines = ["登录成功"]
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key}：\n{value}")
    text = "\n\n".join(lines)
    chunk = 3500
    for i in range(0, len(text), chunk):
        await evt.send(evt.plain_result(text[i : i + chunk]))


async def _poll_qrcode(
    check: Callable[[], Awaitable[Any]],
    *,
    tries: int,
    timeout_message: str,
) -> Any:
    for _ in range(tries):
        await asyncio.sleep(3)
        try:
            return await check()
        except QrcodeScanException:
            continue
        except QrcodeExpireException as e:
            raise RuntimeError(str(e) or timeout_message) from e
    raise RuntimeError(timeout_message)


class LoginCommands:
    @cmd("login")
    async def login(self, event: AstrMessageEvent):
        """登录并提取 cookie。用法: login"""
        yield event.plain_result(_menu("请选择需要登录的平台", _PLATFORMS))

        state: dict[str, Any] = {"step": "platform"}

        @session_waiter(timeout=180)
        async def waiter(controller: SessionController, evt: AstrMessageEvent):
            text = (evt.message_str or "").strip()
            if text in _CANCEL:
                await evt.send(evt.plain_result("已取消登录"))
                controller.stop()
                return
            try:
                await _handle(controller, evt, state, text)
            except Exception as e:
                await evt.send(evt.plain_result(str(e) or "登录失败"))
                controller.stop()

        try:
            await waiter(event)
        except TimeoutError:
            yield event.plain_result("登录超时")
        finally:
            event.stop_event()


async def _handle(
    controller: SessionController,
    evt: AstrMessageEvent,
    state: dict[str, Any],
    text: str,
) -> None:
    step = state["step"]
    if step == "platform":
        picked = _pick(text, _PLATFORMS)
        if not picked:
            await evt.send(evt.plain_result("请输入正确的序号"))
            controller.keep(timeout=180, reset_timeout=True)
            return
        state["platform"] = picked[0]
        methods = _METHODS[picked[0]]
        if len(methods) == 1:
            state["method"] = methods[0][0]
            await _start_method(controller, evt, state)
            return
        state["step"] = "method"
        await evt.send(evt.plain_result(_menu(f"{picked[1]}\n请选择登录方式", methods)))
        controller.keep(timeout=180, reset_timeout=True)
        return

    if step == "method":
        methods = _METHODS[state["platform"]]
        picked = _pick(text, methods)
        if not picked:
            await evt.send(evt.plain_result("请输入正确的序号"))
            controller.keep(timeout=180, reset_timeout=True)
            return
        state["method"] = picked[0]
        await _start_method(controller, evt, state)
        return

    if step == "account":
        state["account"] = text
        state["step"] = "password"
        await evt.send(evt.plain_result(state["password_prompt"]))
        controller.keep(timeout=180, reset_timeout=True)
        return

    if step == "password":
        await _finish_password(evt, state, text)
        controller.stop()
        return

    if step == "phone":
        await _finish_phone(controller, evt, state, text)
        return

    if step == "code":
        if state["platform"] == "netEase":
            result = await NetEaseLogic.login_by_sms(state["netease"], text)
            await _send_result(evt, {"cookie": result.cookie})
        else:
            result = await KuGouLogic.verify_code(state["phone"], text, state["mid"])
            await _send_result(
                evt,
                {
                    "token": result.token,
                    "userid": result.userid,
                    "kuGoo": result.ku_goo,
                    "mid": result.mid,
                },
            )
        controller.stop()
        return

    await evt.send(evt.plain_result("请输入正确的内容，或发送 取消"))
    controller.keep(timeout=180, reset_timeout=True)


async def _start_method(
    controller: SessionController,
    evt: AstrMessageEvent,
    state: dict[str, Any],
) -> None:
    platform = state["platform"]
    method = state["method"]
    if method == "qr" or method in {"wechat", "app"}:
        await _run_qrcode(evt, platform, method)
        controller.stop()
        return
    if method == "sms":
        state["step"] = "phone"
        prompt = "请发送网易云登录的手机号" if platform == "netEase" else "请发送酷狗登录的手机号"
        await evt.send(evt.plain_result(prompt))
        controller.keep(timeout=180, reset_timeout=True)
        return
    if method in {"password", "xiaomi", "lexin"}:
        state["step"] = "account"
        prompts = {
            "eCloud": ("请发送天翼云盘账号", "请发送天翼云盘密码"),
            "xiaomi": ("请发送小米运动手机号", "请发送小米运动密码"),
            "lexin": ("请发送乐心运动手机号", "请发送乐心运动密码"),
        }
        key = method if method in prompts else platform
        account_prompt, password_prompt = prompts[key]
        state["password_prompt"] = password_prompt
        await evt.send(evt.plain_result(account_prompt))
        controller.keep(timeout=180, reset_timeout=True)
        return
    raise RuntimeError("未知登录方式")


async def _run_qrcode(evt: AstrMessageEvent, platform: str, method: str) -> None:
    if platform == "baidu":
        qr = await BaiduLogic.get_qrcode()
        await _send_image(evt, await _download(qr.image), "请使用百度app扫描以下二维码登陆，百度网盘等均可")
        result = await _poll_qrcode(
            lambda: BaiduLogic.check_qrcode(qr), tries=20, timeout_message="百度二维码已超时"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "biliBili":
        qr = await BiliBiliLogic.login_by_qr1()
        await _send_image(evt, make_qrcode(qr.url), "请使用哔哩哔哩app扫描以下二维码登陆")
        result = await _poll_qrcode(
            lambda: BiliBiliLogic.login_by_qr2(qr), tries=10, timeout_message="哔哩哔哩二维码已超时"
        )
        await _send_result(evt, {"cookie": result.cookie, "userid": result.userid, "token": result.token})
        return
    if platform == "douYu":
        qr = await DouYuLogic.get_qrcode()
        await _send_image(evt, make_qrcode(qr.url), "请使用斗鱼app扫码二维码登录")
        result = await _poll_qrcode(
            lambda: DouYuLogic.check_qrcode(qr), tries=20, timeout_message="斗鱼登录二维码已失效"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "netEase":
        qr = await NetEaseLogic.get_qrcode()
        await _send_image(evt, make_qrcode(qr.url), "请使用网易云音乐App扫描以下二维码登录")
        result = await _poll_qrcode(
            lambda: NetEaseLogic.check_qrcode(qr), tries=20, timeout_message="网易云二维码已过期"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "miHoYo":
        qr = await MiHoYoLogic.qrcode_login1()
        await _send_image(evt, make_qrcode(qr.url), "请使用米游社扫描下面二维码登录")
        result = await _poll_qrcode(
            lambda: MiHoYoLogic.qrcode_login2(qr), tries=20, timeout_message="米游社二维码已过期"
        )
        await _send_result(
            evt,
            {"cookie": result.cookie, "aid": result.aid, "mid": result.mid, "token": result.token},
        )
        return
    if platform == "weibo":
        qr = await WeiboLogic.login1()
        await _send_image(evt, await _download(qr.image), "使用微博app扫码登陆")
        result = await _poll_qrcode(
            lambda: WeiboLogic.login2(qr), tries=20, timeout_message="微博二维码已过期"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "huYa":
        qr = await HuYaLogic.get_qrcode()
        await _send_image(evt, await _download(qr.url), "请使用虎牙App扫描二维码登录")
        result = await _poll_qrcode(
            lambda: HuYaLogic.check_qrcode(qr), tries=20, timeout_message="虎牙登录二维码已过期"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "smZdm" and method == "wechat":
        qr = await SmZdmLogic.wechat_qrcode1()
        await _send_image(
            evt,
            await _download(qr.url),
            "请先在网页成功使用微信扫码成功登录一次，使用微信扫码登录，如未关注公众号，扫码关注公众号后再扫一次",
        )
        result = await _poll_qrcode(
            lambda: SmZdmLogic.wechat_qrcode2(qr), tries=20, timeout_message="什么值得买二维码已过期"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    if platform == "smZdm" and method == "app":
        qr = await SmZdmLogic.app_qrcode1()
        await _send_image(evt, make_qrcode(qr.url), "请使用什么值得买App扫码登陆")
        result = await _poll_qrcode(
            lambda: SmZdmLogic.app_qrcode2(qr), tries=20, timeout_message="什么值得买二维码已过期"
        )
        await _send_result(evt, {"cookie": result.cookie})
        return
    raise RuntimeError("未知扫码登录")


async def _finish_password(evt: AstrMessageEvent, state: dict[str, Any], password: str) -> None:
    account = state["account"]
    platform = state["platform"]
    method = state["method"]
    if platform == "eCloud":
        result = await ECloudLogic.login(account, password)
        await _send_result(evt, {"cookie": result.cookie, "eCookie": result.e_cookie})
        return
    if method == "xiaomi":
        result = await XiaomiStepLogic.login(account, password)
        await _send_result(evt, {"miLoginToken": result.mi_login_token})
        return
    if method == "lexin":
        result = await LeXinStepLogic.login(account, password)
        await _send_result(
            evt,
            {
                "leXinCookie": result.lei_xin_cookie,
                "leXinUserid": result.le_xin_userid,
                "leXinAccessToken": result.lei_xin_access_token,
            },
        )
        return
    raise RuntimeError("未知密码登录")


async def _finish_phone(
    controller: SessionController,
    evt: AstrMessageEvent,
    state: dict[str, Any],
    phone: str,
) -> None:
    if state["platform"] == "netEase":
        session = await NetEaseLogic.send_code(phone)
        state["step"] = "code"
        state["netease"] = session
        await evt.send(evt.plain_result("请发送网易云登录的验证码"))
        controller.keep(timeout=180, reset_timeout=True)
        return
    mid = KuGouLogic.mid()
    await KuGouLogic.send_mobile_code(phone, mid)
    state["step"] = "code"
    state["phone"] = phone
    state["mid"] = mid
    await evt.send(evt.plain_result("请发送酷狗登录的验证码"))
    controller.keep(timeout=180, reset_timeout=True)
