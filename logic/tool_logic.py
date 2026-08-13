from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cf_requests

from utils.cache import CacheManager
from utils.config_holder import get_config
from utils.function_utils import segments_download
from utils.http_client import DEFAULT_UA, http_client
from utils.regex_utils import extract


@dataclass
class Bin:
    number: str
    scheme: str
    type: str
    level: str
    region: str
    currency: str
    bank: str


@dataclass
class Rate:
    trans_curr: str
    conversion_rate: str
    crdhld_bill_curr: str
    trans_amt: str
    crdhld_bill_amt: str


@dataclass
class Whois:
    domain_name: str | None
    domain_id: str | None
    registrant: str | None
    registrant_email: str | None
    registrar: str | None
    status: str | None
    nameserver: str | None
    creation_date: str | None
    expiry_date: str | None


class ToolLogic:
    _tmp = Path("tmp")
    _tmp.mkdir(parents=True, exist_ok=True)
    _fish_cache = CacheManager.get_cache("fishing", ttl_seconds=3600)
    _currency_visa: dict[str, str] | None = None
    _VISA_PAGE = (
        "https://www.visa.com.hk/zh_HK/support/consumer/"
        "travel-support/exchange-rate-calculator.html"
    )

    @classmethod
    async def fishing(cls) -> str:
        cached = cls._fish_cache.get_if_present("fishing")
        if cached:
            return cached
        url = (
            "https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum"
            "&__biz=MzAxOTYyMzczNA%3D%3D&album_id=3743225907507462153&count=10"
            "&begin_msgid&begin_itemidx&uin&key&pass_ticket&wxtoken&devicetype"
            "&clientversion&__biz=MzAxOTYyMzczNA%3D%3D&appmsg_token&x5=0&f=json"
        )
        resp = await http_client.get(url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        article_url = data["getalbum_resp"]["article_list"][0]["url"]
        article_resp = await http_client.get(article_url, follow_redirects=False)
        if article_resp.status_code in (301, 302, 307, 308):
            location = article_resp.headers["Location"]
            article_resp = await http_client.get(
                location,
                headers={
                    "Referer": article_url,
                    "User-Agent": DEFAULT_UA,
                },
                follow_redirects=True,
            )
        html = article_resp.text
        document = BeautifulSoup(html, "lxml")
        images = [img for img in document.find_all("img") if img.has_attr("data-src")]
        image_url = None
        for element in images:
            if element.get("data-w") == "540":
                image_url = element.get("data-src")
                break
        if not image_url:
            raise RuntimeError("not found fishing image")
        cls._fish_cache.put("fishing", image_url)
        return image_url

    @classmethod
    async def bin(cls, bin_code: str, key: str | None = None) -> Bin:
        api_key = key or get_config("rapid_key")
        if not api_key:
            raise RuntimeError("未配置 rapid_key")
        resp = await http_client.post(
            f"https://bin-ip-checker.p.rapidapi.com/?bin={bin_code}",
            json={"bin": bin_code},
            headers={
                "x-rapidapi-key": api_key,
                "x-rapidapi-host": "bin-ip-checker.p.rapidapi.com",
            },
            follow_redirects=True,
        )
        json_node = resp.json()
        if json_node.get("code") == 200:
            bin_node = json_node["BIN"]
            country_node = bin_node["country"]
            return Bin(
                number=bin_node["number"],
                scheme=bin_node["scheme"],
                type=bin_node["type"],
                level=bin_node["level"],
                region=country_node["name"],
                currency=country_node["currency"],
                bank=bin_node["issuer"]["name"],
            )
        raise RuntimeError(json_node.get("message") or "bin query failed")

    @classmethod
    async def dy(cls, param: str) -> Path:
        match = re.search(r"https://v\.douyin\.com/[A-Za-z0-9_-]*/?", param)
        if not match:
            raise RuntimeError("未找到抖音链接")
        url_arg = match.group(0)
        location_resp = await http_client.get(
            url_arg,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                    "Mobile/15E148 Safari/604.1"
                )
            },
            follow_redirects=False,
        )
        html_url = location_resp.headers.get("Location")
        if not html_url:
            raise RuntimeError("获取抖音视频失败")
        if "/note/" in html_url:
            raise RuntimeError("发的链接是笔记，无法下载视频")
        html_resp = await http_client.get(
            html_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                    "Mobile/15E148 Safari/604.1"
                )
            },
            follow_redirects=True,
        )
        video_id = extract(html_resp.text, "video_id=", '"')
        if not video_id:
            raise RuntimeError("获取抖音视频失败")
        play_resp = await http_client.get(
            f"https://m.douyin.com/aweme/v1/playwm/?video_id={video_id}&ratio=720p&line=0",
            follow_redirects=False,
        )
        video_url = play_resp.headers.get("Location")
        if not video_url:
            raise RuntimeError("获取抖音视频失败")
        video_file = cls._tmp / f"{uuid.uuid4()}.mp4"
        await segments_download(video_file, video_url)
        return video_file

    @classmethod
    def _load_currency_visa(cls, session: cf_requests.Session) -> dict[str, str]:
        if cls._currency_visa is not None:
            return cls._currency_visa
        html = session.get(cls._VISA_PAGE, timeout=30).text
        raw = extract(html, "<dm-calculator content='", "'></dm-calculator>")
        if not raw:
            raise RuntimeError("unsuccess get currency")
        data = json.loads(html_lib.unescape(raw))
        cls._currency_visa = {item["key"]: item["value"] for item in data["currencyList"]}
        return cls._currency_visa

    @classmethod
    def _rate_visa_sync(cls, from_curr: str, to_curr: str, amt: float) -> Rate:
        """对应 Kotlin rateVisa(from, to, amt)；curl_cffi 过 Cloudflare。"""
        from_curr = from_curr.upper()
        to_curr = to_curr.upper()
        with cf_requests.Session(impersonate="chrome") as session:
            currency = cls._load_currency_visa(session)
            if from_curr not in currency:
                raise RuntimeError(f"incorrect currency: {from_curr}")
            if to_curr not in currency:
                raise RuntimeError(f"incorrect currency: {to_curr}")

            now = datetime.now()
            month = f"{now.month:02d}"
            day = f"{now.day:02d}"
            year = now.year
            # Kotlin: fromCurr=$to&toCurr=$from
            url = (
                "https://www.visa.com.hk/cmsapi/fx/rates"
                f"?amount={amt}&fee=0&utcConvertedDate={month}%2F{day}%2F{year}"
                f"&exchangedate={month}%2F{day}%2F{year}&fromCurr={to_curr}&toCurr={from_curr}"
            )
            try:
                from astrbot.api import logger
            except Exception:  # noqa: BLE001
                import logging

                logger = logging.getLogger(__name__)
            logger.info(f"[visa] rate request url={url}")
            payload = session.get(url, timeout=30).json()
            data = payload["originalValues"]
            return Rate(
                trans_curr=data["fromCurrency"],
                conversion_rate=data["fxRateWithAdditionalFee"],
                crdhld_bill_curr=data["toCurrency"],
                trans_amt=data["fromAmount"],
                crdhld_bill_amt=data["toAmountWithAdditionalFee"],
            )

    @classmethod
    async def rate_visa(cls, from_curr: str, to_curr: str, amt: float) -> Rate:
        return await asyncio.to_thread(cls._rate_visa_sync, from_curr, to_curr, amt)

    @classmethod
    async def watch_world(cls) -> str:
        data = (await http_client.get("https://zaobao.wpush.cn/api/zaobao/today", follow_redirects=True)).json()
        return data["data"]["image"]


    @classmethod
    async def bgp(cls, keyword: str) -> Path:
        response = await http_client.get(f"https://bgp.tools/search?q={keyword}", follow_redirects=False)
        while response.status_code in (307, 302, 301, 308):
            location = response.headers.get("Location")
            if not location:
                break
            if location.startswith("/"):
                location = "https://bgp.tools" + location
            response = await http_client.get(location, follow_redirects=False)

        # 跟随完后拿最终内容
        if response.status_code >= 300:
            response = await http_client.get(str(response.url), follow_redirects=True)
        else:
            # 某些情况下 body 已在当前响应
            pass

        final_url = str(response.url)
        html = response.text
        if "prefix/" in final_url:
            key = final_url[len("https://bgp.tools") :].replace("/", "_")
            image_url = f"https://bgp.tools/pathimg/rt-{key}?{uuid.uuid4()}&loggedin"
        elif "prefix-selector" in final_url:
            soup = BeautifulSoup(html, "lxml")
            elements = soup.select(".smallonmobile.nowrap")
            lines = []
            for i in range(0, len(elements), 3):
                chunk = elements[i : i + 3]
                if len(chunk) >= 2:
                    lines.append(f"{chunk[0].get_text(strip=True)} {chunk[1].get_text(strip=True)}")
            raise RuntimeError("该ip有多个前缀\n" + "\n".join(lines) + "\n你应该可直接搜索asn或者前缀")
        elif "/as" in final_url:
            asn = final_url.rstrip("/").split("/")[-1]
            soup = BeautifulSoup(html, "lxml")
            option = soup.select_one("#netpolicydropdown option[selected]")
            if not option:
                raise RuntimeError("not found")
            down = option.get("value")
            image_url = f"https://bgp.tools/pathimg/{asn}-{down}?"
        else:
            raise RuntimeError("not found")

        svg_text = (await http_client.get(image_url, follow_redirects=True)).text.replace(
            "transparent", "none", 1
        )
        uid = str(uuid.uuid4())
        svg_path = cls._tmp / f"{uid}.svg"
        jpg_path = cls._tmp / f"{uid}.jpg"
        svg_path.write_text(svg_text, encoding="utf-8")
        try:
            import cairosvg
            from PIL import Image
            from io import BytesIO

            png_bytes = cairosvg.svg2png(bytestring=svg_text.encode("utf-8"))
            image = Image.open(BytesIO(png_bytes)).convert("RGB")
            image.save(jpg_path, "JPEG", quality=90)
        except Exception as e:
            raise RuntimeError(f"SVG 转 JPG 失败，请安装 cairosvg/cairo: {e}") from e
        finally:
            if svg_path.exists():
                svg_path.unlink(missing_ok=True)
        return jpg_path

    @classmethod
    async def whois(cls, domain: str, full: bool = False) -> Whois:
        json_node = (
            await http_client.get(
                f"https://whois.233333.best/api/?domain={domain}",
                follow_redirects=True,
            )
        ).json()
        if json_node.get("code") != 0:
            raise RuntimeError(json_node.get("msg") or "whois failed")
        data = json_node["data"]["whoisData"]
        if full:
            raise RuntimeError(data)
        mapping: dict[str, str] = {}
        for line in data.split("\n"):
            split = line.split(": ", 1)
            if len(split) != 2:
                continue
            key = split[0].strip()
            value = split[1].strip()
            if key in mapping:
                mapping[key] = f"{mapping[key]},{value}"
            else:
                mapping[key] = value
        return Whois(
            domain_name=mapping.get("Domain Name"),
            domain_id=mapping.get("Registry Domain ID"),
            registrant=mapping.get("Registrant") or mapping.get("Registrant Organization"),
            registrant_email=mapping.get("Registrant Contact Email") or mapping.get("Registrant Email"),
            registrar=mapping.get("Registrar") or mapping.get("Sponsoring Registrar"),
            status=mapping.get("Domain Status"),
            nameserver=mapping.get("Name Server"),
            creation_date=mapping.get("Creation Date") or mapping.get("Registration Time"),
            expiry_date=mapping.get("Registry Expiry Date") or mapping.get("Expiration Time"),
        )
