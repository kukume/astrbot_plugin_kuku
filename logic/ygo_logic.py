from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, Tag

from utils.http_client import http_client


@dataclass
class Card:
    chinese_name: str
    japanese_name: str
    english_name: str
    card_password: str
    effect: str
    url: str
    image_url: str


class YgoLogic:
    @staticmethod
    async def search(name: str) -> list[Card]:
        resp = await http_client.get(
            f"https://ygocdb.com/?search={quote(name)}",
            follow_redirects=True,
        )
        elements = BeautifulSoup(resp.text, "lxml").select(".card")
        result: list[Card] = []
        for element in elements:
            spans = element.select("span")
            if not spans:
                continue
            chinese_name = spans[0].get_text(strip=True)
            japanese_name = spans[1].get_text(strip=True) if len(spans) > 1 else ""
            if len(spans) == 6:
                english_name = spans[2].get_text(strip=True)
                card_password = spans[3].get_text(strip=True)
            else:
                english_name = ""
                card_password = spans[2].get_text(strip=True) if len(spans) > 2 else ""
            a = element.select_one(".cardimg a")
            if not a:
                continue
            href = a.get("href") or ""
            url = f"https://ygocdb.com{href}"
            img = a.select_one("img")
            img_url = img.get("src") if img else ""
            desc = element.select_one(".desc")
            if not desc:
                continue
            name_html = "".join(str(n) for n in desc.select(".name"))
            effect_html = str(desc).replace(name_html, "")
            effect = (
                effect_html.replace("<hr>", "\n")
                .replace("<hr/>", "\n")
                .replace("<br>", "\n")
                .replace("<br/>", "\n")
                .replace("\n\n", "\n")
            )
            effect = BeautifulSoup(effect, "lxml").get_text("\n")
            result.append(
                Card(
                    chinese_name=chinese_name,
                    japanese_name=japanese_name,
                    english_name=english_name,
                    card_password=card_password,
                    effect=effect.strip(),
                    url=url,
                    image_url=img_url,
                )
            )
        return result

    @staticmethod
    async def search_detail(card_id: int) -> Card:
        url = f"https://ygocdb.com/card/{card_id}"
        resp = await http_client.get(url, follow_redirects=True)
        document = BeautifulSoup(resp.text, "lxml")
        img = document.select_one(".cardimg img")
        image_url = (img.get("srcset") or "").split(" ")[0] if img else ""
        chinese_name = (document.select_one('span[lang="zh-Hans"]') or Tag(name="span")).get_text(strip=True)
        japanese_name = (document.select_one('span[lang="ja-Jpan"]') or Tag(name="span")).get_text(strip=True)
        english_name = (document.select_one('span[lang="en"]') or Tag(name="span")).get_text(strip=True)
        desc = document.select_one(".desc")
        sb: list[str] = []
        if desc:
            for child in desc.children:
                text = str(child)
                if text in ("<br>", "<br/>", "<hr>", "<hr/>"):
                    sb.append("\n")
                elif isinstance(child, Tag):
                    sb.append(child.get_text())
                elif isinstance(child, NavigableString):
                    sb.append(str(child))
                else:
                    sb.append(text)
        return Card(
            chinese_name=chinese_name,
            japanese_name=japanese_name,
            english_name=english_name,
            card_password=str(card_id),
            effect="".join(sb),
            url=url,
            image_url=image_url,
        )
