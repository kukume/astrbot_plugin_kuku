from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from utils.http_client import http_client

_IMAGE_TYPE_PRIORITY = (
    "OfferImageWide",
    "DieselStoreFrontWide",
    "OfferImageTall",
    "DieselStoreFrontTall",
    "Thumbnail",
    "OgImage",
)


@dataclass
class EpicFreeGame:
    title: str
    inner_title: str
    description: str
    long_description: str
    original_price: str
    discount_price: str
    url: str
    image_url: str
    diff: int

    def text(self) -> str:
        return (
            f"#Epic免费游戏推送\n游戏名称: {self.title}\n游戏内部名称: {self.inner_title}\n"
            f"游戏描述: {self.description}\n原价: {self.original_price}\n折扣价: {self.discount_price}\n"
            f"订单地址：{self.url}"
        )


def _parse_epic_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _first_offer(offers: Any) -> dict[str, Any] | None:
    try:
        return offers[0]["promotionalOffers"][0]
    except Exception:
        return None


def _pick_image(element: dict[str, Any]) -> str:
    images = element.get("keyImages") or []
    by_type = {img.get("type"): img.get("url") for img in images if img.get("url")}
    for image_type in _IMAGE_TYPE_PRIORITY:
        if by_type.get(image_type):
            return by_type[image_type]
    return next((img.get("url") for img in images if img.get("url")), "")


def _display_price(fmt_price: str | None, amount: int) -> str:
    if amount == 0:
        return "免费"
    return fmt_price or str(amount)


class EpicLogic:
    @staticmethod
    async def epic() -> list[EpicFreeGame]:
        resp = await http_client.get(
            "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
            "?locale=zh-CN&country=CN&allowCountries=CN",
            follow_redirects=True,
        )
        resp.raise_for_status()
        elements = [
            el
            for el in resp.json()["data"]["Catalog"]["searchStore"]["elements"]
            if el.get("status") == "ACTIVE"
        ]
        now = datetime.now(timezone.utc)
        now_ts = int(time.time() * 1000)
        result: list[EpicFreeGame] = []
        for element in elements:
            promotions = element.get("promotions") or {}
            promotion = _first_offer(promotions.get("promotionalOffers"))
            if not promotion:
                continue
            start = _parse_epic_dt(promotion["startDate"])
            end = _parse_epic_dt(promotion["endDate"])
            if not (start <= now < end):
                continue
            if (promotion.get("discountSetting") or {}).get("discountPercentage") != 0:
                continue

            total_price = ((element.get("price") or {}).get("totalPrice") or {})
            original_amount = int(total_price.get("originalPrice") or 0)
            discount_amount = int(total_price.get("discountPrice") or 0)
            # 跳过本身免费的游戏，只保留限免（原价 > 0 且现价为 0）
            if original_amount <= 0 or discount_amount != 0:
                continue

            namespace = element.get("namespace")
            offer_id = element.get("id")
            if not namespace or not offer_id:
                continue

            fmt_price = total_price.get("fmtPrice") or {}
            title = element.get("title") or ""
            description = element.get("description") or ""
            result.append(
                EpicFreeGame(
                    title=title,
                    inner_title=title,
                    description=description,
                    long_description=description,
                    original_price=_display_price(fmt_price.get("originalPrice"), original_amount),
                    discount_price=_display_price(fmt_price.get("discountPrice"), discount_amount),
                    url=(
                        "https://store.epicgames.com/purchase?highlightColor=0078f2"
                        f"&offers=1-{namespace}-{offer_id}&showNavigation=true#/purchase/payment-methods"
                    ),
                    image_url=_pick_image(element),
                    diff=now_ts - int(start.timestamp() * 1000),
                )
            )
        return result
