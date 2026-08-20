from __future__ import annotations

import re
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from di.commands import cmd

from utils.helpers import (
    extract_image_urls,
    send_video_lock,
    send_video_or_file,
)
from logic.bilibili_logic import BiliBiliLogic
from logic.epic_logic import EpicLogic
from logic.tool_logic import ToolLogic
from utils.http_client import http_client


class ToolCommands:
    @cmd("st")
    async def st(self, event: AstrMessageEvent):
        """随机涩图"""
        try:
            data = (await http_client.get("https://api.lolicon.app/setu/v2", follow_redirects=True)).json()
            url = data["data"][0]["urls"]["original"]
            yield event.image_result(url)
        except Exception as e:
            yield event.plain_result(f"失败: {e}")

    @cmd("bv")
    async def bv(self, event: AstrMessageEvent, param: str):
        """根据 BV 号/链接发送 B 站视频。用法: bv BV1xx / bv https://..."""
        rest = (event.message_str or "").strip()
        if rest and param and not rest.startswith(param):
            for prefix in ("bv", "/bv"):
                if rest.lower().startswith(prefix):
                    rest = rest[len(prefix) :].strip()
                    break
            if len(rest) > len(param):
                param = rest
        if not param:
            yield event.plain_result("请提供 BV 号或链接")
            return
        async with send_video_lock:
            yield event.plain_result("发送视频中，请稍后")
            try:
                video = await BiliBiliLogic.video_by_bv_id(param)
                yield event.chain_result(
                    [
                        Comp.Image.fromURL(video.pic),
                        Comp.Plain(f"{video.title}\n{video.desc}"),
                    ]
                )
                await send_video_or_file(event, video.file, video.file.name)
            except Exception as e:
                logger.exception(e)
                yield event.plain_result(f"失败: {e}")

    @cmd("dy")
    async def dy(self, event: AstrMessageEvent, link: str):
        """抖音视频下载。用法: dy https://v.douyin.com/xxx"""
        param = link
        msg = (event.message_str or "").strip()
        for prefix in ("dy", "/dy"):
            if msg.lower().startswith(prefix):
                msg = msg[len(prefix) :].strip()
                break
        if msg and len(msg) > len(link):
            param = msg
        if not param:
            yield event.plain_result("请提供抖音链接")
            return
        async with send_video_lock:
            yield event.plain_result("发送视频中，请稍等...")
            file_path: Path | None = None
            try:
                file_path = await ToolLogic.dy(param)
                await send_video_or_file(event, file_path, file_path.name)
            except Exception as e:
                logger.exception(e)
                yield event.plain_result(f"失败: {e}")
            finally:
                if file_path and file_path.exists():
                    file_path.unlink(missing_ok=True)

    @cmd("epic")
    async def epic(self, event: AstrMessageEvent):
        """Epic 免费游戏"""
        try:
            games = await EpicLogic.epic()
            if not games:
                yield event.plain_result("当前没有可领取的免费游戏")
                return
            for game in games:
                yield event.chain_result(
                    [
                        Comp.Image.fromURL(game.image_url),
                        Comp.Plain(game.text()),
                    ]
                )
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @cmd("rate")
    async def rate(self, event: AstrMessageEvent, amount: str, to: str, currency: str):
        """Visa 汇率。用法: rate 20USD to CNY"""
        try:
            if to.lower() != "to":
                raise RuntimeError("format error")
            m = re.fullmatch(r"([\d.]+)([A-Za-z]{3})", amount, re.I)
            if not m:
                raise RuntimeError("format error")
            amt = float(m.group(1))
            from_curr = m.group(2).upper()
            to_curr = currency.upper()
            result = await ToolLogic.rate_visa(from_curr, to_curr, amt)
            text = (
                f"1{result.trans_curr} = {result.conversion_rate}{result.crdhld_bill_curr}\n\n"
                f"{result.trans_amt}{result.trans_curr} = {result.crdhld_bill_amt}{result.crdhld_bill_curr}"
            )
            yield event.plain_result(text)
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @cmd("my")
    async def my(self, event: AstrMessageEvent):
        """摸鱼日历"""
        try:
            url = await ToolLogic.fishing()
            yield event.image_result(url)
        except Exception as e:
            yield event.plain_result(f"失败: {e}")

    @cmd("world")
    async def world(self, event: AstrMessageEvent):
        """每日早报图片"""
        try:
            url = await ToolLogic.watch_world()
            yield event.image_result(url)
        except Exception as e:
            yield event.plain_result(f"失败: {e}")

    @cmd("bin", attr_name="bin_cmd")
    async def bin_cmd(self, event: AstrMessageEvent, bin_code: str):
        """银行卡 BIN 查询。用法: bin 622202"""
        try:
            result = await ToolLogic.bin(bin_code)
            text = (
                f"🔢卡头：{result.number}\n"
                f"💳品牌：{result.scheme}\n"
                f"🔖类型：{result.type}\n"
                f"💹等级：{result.level}\n\n"
                f"🗺地区：{result.region}\n"
                f"💸货币：{result.currency}\n"
                f"🏦银行：{result.bank}"
            )
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(f"失败: {e}")

    @cmd("bgp")
    async def bgp(self, event: AstrMessageEvent, keyword: str):
        """BGP 路径图查询。用法: bgp AS15169"""
        try:
            file_path = await ToolLogic.bgp(keyword)
            yield event.image_result(str(file_path.resolve()))
            file_path.unlink(missing_ok=True)
        except Exception as e:
            yield event.plain_result(f"失败: {e}")

    @cmd("icp", alias={"备案"})
    async def icp(self, event: AstrMessageEvent, keyword: str):
        """ICP 备案查询。用法: icp qq.com / 备案 粤B2-20090059"""
        if not keyword:
            yield event.plain_result("请提供域名、备案号或主办单位名称")
            return
        try:
            records = await ToolLogic.icp(keyword)
            blocks = []
            for i, item in enumerate(records, 1):
                lines = [
                    f"{i}." if len(records) > 1 else None,
                    f"主办单位：{item.unit_name}" if item.unit_name else None,
                    f"单位性质：{item.nature_name}" if item.nature_name else None,
                    f"网站名称：{item.service_name}" if item.service_name else None,
                    f"域名：{item.domain}" if item.domain else None,
                    f"网站首页：{item.home_url}" if item.home_url else None,
                    f"备案号：{item.main_licence}" if item.main_licence else None,
                    f"网站备案号：{item.service_licence}" if item.service_licence else None,
                    f"审核日期：{item.update_record_time}" if item.update_record_time else None,
                    f"前置审批：{item.content_type_name}" if item.content_type_name else None,
                ]
                blocks.append("\n".join(line for line in lines if line))
            prefix = f"共找到 {len(records)} 条备案信息\n\n" if len(records) > 1 else ""
            yield event.plain_result(prefix + "\n\n".join(blocks))
        except Exception as e:
            logger.exception(e)
            yield event.plain_result(f"失败: {e}")

    @cmd("whois", alias={"whoisf"})
    async def whois(self, event: AstrMessageEvent, domain: str):
        """Whois 查询。用法: whois example.com / whoisf example.com"""
        full = False
        try:
            plain = getattr(event, "message_str", "") or ""
            full = plain.strip().lower().startswith("whoisf")
        except Exception:
            full = False
        try:
            result = await ToolLogic.whois(domain, full=full)
            text = (
                f"域名：{result.domain_name}\n"
                f"注册人：{result.registrant}\n"
                f"注册邮箱：{result.registrant_email}\n"
                f"注册机构：{result.registrar}\n"
                f"状态：{result.status}\n"
                f"NS：{result.nameserver}\n"
                f"创建日期：{result.creation_date}\n"
                f"过期日期：{result.expiry_date}"
            )
            yield event.plain_result(text)
        except Exception as e:
            yield event.plain_result(str(e))

    @cmd("表情", attr_name="face_package")
    async def face_package(self, event: AstrMessageEvent):
        """提取消息中的图片 URL"""
        urls = extract_image_urls(event)
        if not urls:
            yield event.plain_result("没有检测到图片")
            return
        yield event.plain_result("\n".join(urls))
