from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..utils.function_utils import run_ffmpeg, segments_download
from ..utils.http_client import http_client
from ..utils.login_utils import (
    QrcodeExpireException,
    QrcodeNotScannedException,
    QrcodeScannedException,
    render_set_cookie,
)
from ..utils.regex_utils import extract


@dataclass
class BiliBiliVideo:
    title: str
    desc: str
    pic: str
    owner_name: str
    owner_mid: str
    owner_face: str
    file: Path


MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class Wbi:
    _img = ""
    _sub = ""
    _day = -1

    @classmethod
    async def enc(cls, params: dict[str, object]) -> str:
        try:
            now_day = datetime.now(ZoneInfo("Asia/Shanghai")).day
        except Exception:
            # Windows 未安装 tzdata 时 ZoneInfo 会失败
            now_day = datetime.now().day
        if not cls._img or not cls._sub or now_day != cls._day:
            data = (await http_client.get("https://api.bilibili.com/x/web-interface/nav", follow_redirects=True)).json()
            wbi = data["data"]["wbi_img"]
            cls._img = wbi["img_url"].split("/")[-1].removesuffix(".png")
            cls._sub = wbi["sub_url"].split("/")[-1].removesuffix(".png")
            cls._day = now_day
        raw = cls._img + cls._sub
        mixin = "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]
        mutable = dict(params)
        mutable["wts"] = mutable.get("wts") or int(time.time())
        sorted_items = sorted(mutable.items(), key=lambda x: x[0])
        query = "&".join(f"{quote(str(k))}={quote(str(v))}" for k, v in sorted_items)
        w_rid = _md5(query + mixin)
        return f"{query}&w_rid={w_rid}"


class BiliBiliLogic:
    _tmp = Path("tmp")
    _tmp.mkdir(parents=True, exist_ok=True)

    @classmethod
    async def video_by_bv_id(cls, param: str) -> BiliBiliVideo:
        bv_regex = re.compile(r"BV[A-Za-z0-9]{10}")
        match = bv_regex.search(param)
        bv_id = match.group(0) if match else None
        if bv_id is None:
            short = re.search(r"https://b23\.tv/[a-zA-Z0-9]+", param)
            if short:
                response = await http_client.get(short.group(0), follow_redirects=False)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "")
                    m2 = bv_regex.search(location)
                    bv_id = m2.group(0) if m2 else None
        if not bv_id:
            raise RuntimeError("not found bvId")

        html_url = f"https://www.bilibili.com/video/{bv_id}/"
        response = await http_client.get(
            html_url,
            headers={"User-Agent": "Reqable/2.30.3"},
            follow_redirects=True,
        )
        if response.status_code != 200:
            raise RuntimeError(f"错误：{response.status_code}")

        html = response.text
        buvid3 = response.cookies.get("buvid3")
        state_raw = extract(html, "window.__INITIAL_STATE__=", ";(")
        if not state_raw:
            raise RuntimeError("未获取到内容")
        import json

        state = json.loads(state_raw)
        aid = state["aid"]
        bv_id = state["bvid"]
        cid = state["cid"]
        t = int(time.time())
        session = _md5(f"{buvid3 or ''}{t}")
        video_data = state["videoData"]
        title = video_data["title"]
        desc = video_data["desc"]
        pic = video_data["pic"]
        owner_name = video_data["owner"]["name"]
        owner_mid = str(video_data["owner"]["mid"])
        face = video_data["owner"]["face"]

        match_path = cls._tmp / f"{bv_id}output.mp4"
        if match_path.exists():
            return BiliBiliVideo(title, desc, pic, owner_name, owner_mid, face, match_path)

        url_params = await Wbi.enc(
            {
                "avid": aid,
                "bvid": bv_id,
                "cid": cid,
                "qn": 80,
                "fnver": 0,
                "fnval": 4048,
                "fourk": 1,
                "gaia_source": "",
                "from_client": "BROWSER",
                "is_main_page": "true",
                "need_fragment": "false",
                "isGaiaAvoided": "false",
                "session": session,
                "try_look": 1,
                "web_location": 1315873,
                "wts": t,
            }
        )
        video_json = (
            await http_client.get(
                f"https://api.bilibili.com/x/player/wbi/playurl?{url_params}",
                headers={"User-Agent": "Reqable/2.30.3"},
                follow_redirects=True,
            )
        ).json()
        video_url = video_json["data"]["dash"]["video"][0]["baseUrl"]
        audio_url = video_json["data"]["dash"]["audio"][0]["baseUrl"]

        video_file = cls._tmp / f"{bv_id}.mp4"
        await segments_download(video_file, video_url, headers={"Referer": html_url})
        audio_file = cls._tmp / f"{bv_id}.m4a"
        audio_resp = await http_client.get(
            audio_url,
            headers={"Referer": html_url},
            follow_redirects=True,
        )
        audio_file.write_bytes(audio_resp.content)

        output_path = cls._tmp / f"{bv_id}output.mp4"
        if output_path.exists():
            output_path.unlink()
        try:
            await run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_file.resolve()),
                    "-i",
                    str(audio_file.resolve()),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    str(output_path.resolve()),
                ]
            )
        finally:
            video_file.unlink(missing_ok=True)
            audio_file.unlink(missing_ok=True)
        return BiliBiliVideo(title, desc, pic, owner_name, owner_mid, face, output_path)

    @classmethod
    async def login_by_qr1(cls) -> BiliBiliQrcode:
        data = (
            await http_client.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header"
            )
        ).json()["data"]
        return BiliBiliQrcode(url=data["url"], key=data["qrcode_key"])

    @classmethod
    async def login_by_qr2(cls, qrcode: BiliBiliQrcode) -> BiliBiliLogin:
        response = await http_client.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
            f"?qrcode_key={qrcode.key}&source=main-fe-header"
        )
        data = response.json()["data"]
        code = int(data["code"])
        if code == 86101:
            raise QrcodeNotScannedException()
        if code == 86090:
            raise QrcodeScannedException()
        if code == 86038:
            raise QrcodeExpireException("哔哩哔哩二维码已超时")
        if code != 0:
            raise RuntimeError(data.get("message") or "哔哩哔哩登录失败")
        first_cookie = render_set_cookie(response)
        url = data["url"]
        token_match = re.search(r"bili_jct=([^&\\]+)", url)
        token = token_match.group(1) if token_match else ""
        sso_json = (
            await http_client.get(
                f"https://passport.bilibili.com/x/passport-login/web/sso/list?biliCSRF={token}",
                headers={"Cookie": first_cookie},
            )
        ).json()
        cookie = ""
        for inner_url in sso_json.get("data", {}).get("sso") or []:
            inner = await http_client.post(
                inner_url,
                data={},
                headers={
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                },
            )
            cookie = render_set_cookie(inner)
        userid_match = re.search(r"DedeUserID=([^;]+);", cookie)
        userid = userid_match.group(1).strip() if userid_match else ""
        finger = (await http_client.get("https://api.bilibili.com/x/frontend/finger/spi")).json()["data"]
        finger_cookie = f"buvid3={finger['b_3']}; buvid4={finger['b_4']}; "
        return BiliBiliLogin(cookie=cookie + finger_cookie, userid=userid, token=token)


@dataclass
class BiliBiliQrcode:
    url: str
    key: str


@dataclass
class BiliBiliLogin:
    cookie: str
    userid: str
    token: str
