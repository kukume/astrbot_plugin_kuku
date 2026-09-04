"""共享的卡片出图：字体、排版、下图、Check.Place SVG（无浏览器）。"""

from __future__ import annotations

import asyncio
import html as htmlmod
import io
import json
import logging
import re
import threading
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image, ImageDraw, ImageFont

from ..utils.http_client import curl_session

logger = logging.getLogger("astrbot_plugin_kuku.card_image")

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_FONT_LOCK = threading.Lock()
_FONTS_DIR = Path(__file__).resolve().parent.parent / "tmp" / "fonts"
_SC_FONT = _FONTS_DIR / "NotoSansSC-VF.ttf"
_MONO_FONT = _FONTS_DIR / "NotoSansMono-Regular.ttf"
_BAR_FONT = _FONTS_DIR / "xyBarMono.woff"
_FONT_SOURCES: list[tuple[Path, list[str]]] = [
    (
        _SC_FONT,
        [
            "https://github.com/notofonts/noto-cjk/raw/main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf",
            "https://cdn.jsdelivr.net/gh/notofonts/noto-cjk@main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf",
        ],
    ),
    (
        _MONO_FONT,
        [
            "https://github.com/notofonts/noto-fonts/raw/main/hinted/ttf/NotoSansMono/NotoSansMono-Regular.ttf",
            "https://cdn.jsdelivr.net/gh/notofonts/noto-fonts@main/hinted/ttf/NotoSansMono/NotoSansMono-Regular.ttf",
        ],
    ),
    (
        _BAR_FONT,
        ["https://res.check.place/fonts/xyBarMono.woff"],
    ),
]
# xyBarMono：ASCII + 框线/勾叉 + 盲文块（Check.Place 进度条）
_XYBAR_CP = set(range(0x20, 0x7F)) | {
    0x00B0, 0x00B1, 0x00B7, 0x00D7, 0x00F7, 0x2103,
    0x2550, 0x2551, 0x255A, 0x2560, 0x2714, 0x2718,
} | set(range(0x2800, 0x2900))
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class CardPage:
    platform: str
    source_url: str
    answer_id: str
    page_title: str = ""
    question_title: str = ""
    question_description: str = ""
    author_name: str = ""
    author_nickname: str = ""
    avatar_url: str = ""
    avatar_referer: str | None = None
    created_time: str = ""
    badge: str = ""
    items: list[dict] = field(default_factory=list)


def format_created_time(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    tz_bj = timezone(timedelta(hours=8))
    if value.isdigit() and len(value) >= 10:
        try:
            ts = int(value[:10])
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz_bj).strftime(
                "%Y-%m-%d %H:%M:%S +08:00"
            )
        except Exception:
            return value
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", value)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            return dt.astimezone(tz_bj).strftime("%Y-%m-%d %H:%M:%S +08:00")
        except Exception:
            return f"{m.group(1)} {m.group(2)} +08:00"
    return value


def _font_ok(path: Path) -> bool:
    if not path.exists():
        return False
    # xyBarMono 只有约 10KB
    return path.stat().st_size > (5_000 if path == _BAR_FONT else 10_000)


def _download_font(path: Path, urls: list[str]) -> None:
    if _font_ok(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    last_err: Exception | None = None
    for url in urls:
        try:
            logger.info(f"下载字体 {path.name}: {url}")
            headers = {"User-Agent": _UA}
            if "check.place" in url.lower():
                headers["Referer"] = "https://report.check.place/"
            data, _ = _sync_get(url, headers, timeout=180)
            if len(data) < 10_000:
                raise RuntimeError(f"文件过小: {len(data)} bytes")
            tmp.write_bytes(data)
            tmp.replace(path)
            logger.info(f"字体已保存 {path} ({path.stat().st_size} bytes)")
            return
        except Exception as e:
            last_err = e
            logger.warning(f"字体下载失败 {url}: {e}")
    raise RuntimeError(f"无法下载字体 {path.name}: {last_err}")


def ensure_fonts_sync() -> None:
    with _FONT_LOCK:
        for path, urls in _FONT_SOURCES:
            if _font_ok(path):
                continue
            try:
                _download_font(path, urls)
            except Exception as e:
                logger.warning(f"字体 {path.name} 不可用: {e}")


async def ensure_fonts() -> None:
    await asyncio.to_thread(ensure_fonts_sync)


def _with_weight(font: ImageFont.FreeTypeFont, *, bold: bool) -> ImageFont.FreeTypeFont:
    try:
        font.set_variation_by_axes([700 if bold else 400])
    except Exception:
        pass
    return font


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    cached = _FONT_CACHE.get(key)
    if cached:
        return cached
    if _font_ok(_SC_FONT):
        font = _with_weight(ImageFont.truetype(str(_SC_FONT), size), bold=bold)
        _FONT_CACHE[key] = font
        return font
    return ImageFont.load_default()


def load_mono_font(size: int) -> ImageFont.FreeTypeFont:
    key = ("m", size)
    cached = _FONT_CACHE.get(key)
    if cached:
        return cached
    path = _MONO_FONT if _font_ok(_MONO_FONT) else _SC_FONT
    if _font_ok(path):
        font = ImageFont.truetype(str(path), size)
        _FONT_CACHE[key] = font
        return font
    return ImageFont.load_default()


def load_bar_font(size: int) -> ImageFont.FreeTypeFont | None:
    key = ("xy", size)
    cached = _FONT_CACHE.get(key)
    if cached:
        return cached
    if not _font_ok(_BAR_FONT):
        return None
    font = ImageFont.truetype(str(_BAR_FONT), size)
    _FONT_CACHE[key] = font
    return font


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in (text or "").split("\n"):
        if para == "":
            lines.append("")
            continue
        buf = ""
        for ch in para:
            trial = buf + ch
            if draw.textbbox((0, 0), trial, font=font)[2] <= max_width:
                buf = trial
            else:
                if buf:
                    lines.append(buf)
                buf = ch
        if buf:
            lines.append(buf)
    return lines


def draw_wrapped(draw, text, font, fill, x, y, max_width, line_gap=10) -> int:
    lines = wrap_text(draw, text, font, max_width)
    yy = y
    for line in lines:
        if line == "":
            yy += getattr(font, "size", 24) + line_gap
            continue
        draw.text((x, yy), line, font=font, fill=fill)
        yy += getattr(font, "size", 24) + line_gap
    return yy


def circle_crop(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img.convert("RGB"), (0, 0), mask)
    return out


def fit_image(img: Image.Image, inner_w: int, max_h: int) -> Image.Image:
    ratio = img.height / max(img.width, 1)
    h = min(int(inner_w * ratio), max_h)
    w = int(h / ratio) if ratio else inner_w
    resample = Image.Resampling.LANCZOS
    return img.resize((max(w, 1), max(h, 1)), resample)


def paste_framed(card: Image.Image, img: Image.Image, x: int, y: int, radius: int = 24) -> int:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    frame = Image.new("RGBA", (img.width + 20, img.height + 20), (248, 250, 252, 255))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle(
        (0, 0, img.width + 19, img.height + 19),
        radius=radius,
        fill=(248, 250, 252, 255),
        outline=(229, 231, 235, 255),
        width=2,
    )
    frame.alpha_composite(img, (10, 10))
    card.alpha_composite(frame, (x, y))
    return y + img.height + 34


def new_canvas(width: int, height: int, pad: int, *, bg="#f5f7fb", card="#ffffff", line="#e5e7eb", radius=34):
    bg_img = Image.new("RGBA", (width, height), bg)
    card_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card_img)
    cd.rounded_rectangle((pad, 40, width - pad, height - 40), radius=radius, fill=card, outline=line, width=2)
    return bg_img, card_img, cd


def to_png_bytes(bg: Image.Image, card: Image.Image) -> bytes:
    buf = io.BytesIO()
    Image.alpha_composite(bg, card).convert("RGB").save(buf, format="PNG", quality=95)
    return buf.getvalue()


def _sync_get(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[bytes, str]:
    with curl_session() as session:
        resp = session.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        ctype = ""
        try:
            ctype = resp.headers.get("content-type") or ""
        except Exception:
            pass
        return resp.content, ctype


async def fetch_bytes(url: str, referer: str | None = None) -> tuple[bytes, str]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    if referer:
        headers["Referer"] = referer
    return await asyncio.to_thread(_sync_get, url, headers)


def _is_svg(url: str, data: bytes, content_type: str) -> bool:
    if url.lower().endswith(".svg") or url.startswith("data:image/svg+xml"):
        return True
    if "svg" in (content_type or "").lower():
        return True
    return data.lstrip().startswith(b"<svg")


def _looks_like_terminal_svg(svg: str) -> bool:
    head = svg[:2000]
    return bool(re.search(r'\b(width|height|x|y)="[\d.]+ch"', head) or "report.check.place" in head.lower())


def rasterize_terminal_svg(svg: bytes) -> Image.Image:
    """Check.Place 一类 ch/em 终端报告：用 xyBarMono + 中文字体按字符网格画。"""
    text = svg.decode("utf-8", "replace")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    scale = 3
    font_px = 14 * scale
    ch_px = font_px / 2  # xyBarMono: UPM 240, 字宽 120, ch = 0.5em
    em_px = float(font_px)

    def unit_to_px(value: str) -> float:
        m = re.match(r"^(-?[\d.]+)(ch|em|px|%)?$", (value or "").strip())
        if not m:
            return 0.0
        n = float(m.group(1))
        unit = (m.group(2) or "px").lower()
        if unit == "ch":
            return n * ch_px
        if unit == "em":
            return n * em_px
        if unit == "%":
            return 0.0
        return n * scale

    wm = re.search(r'\bwidth="([^"]+)"', text)
    hm = re.search(r'\bheight="([^"]+)"', text)
    width = int(round(unit_to_px(wm.group(1)))) if wm else int(82 * ch_px)
    height = int(round(unit_to_px(hm.group(1)))) if hm else int(49 * em_px)
    style_m = re.search(r"<style>([\s\S]*?)</style>", text, re.I)
    classes: dict[str, dict[str, str]] = {}
    if style_m:
        for m in re.finditer(r"\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}", style_m.group(1)):
            props: dict[str, str] = {}
            for pm in re.finditer(
                r"(fill|stroke|font-weight|font-style|text-decoration)\s*:\s*([^;]+)",
                m.group(2),
            ):
                props[pm.group(1)] = pm.group(2).strip().strip('"')
            classes[m.group(1)] = props

    bg = "#000000"
    bm = re.search(r'rect[^>]*width="100%"[^>]*style="fill:\s*([^"]+)"', text)
    if bm:
        bg = bm.group(1).strip()

    img = Image.new("RGB", (max(width, 1), max(height, 1)), bg)
    d = ImageDraw.Draw(img)
    font_bar = load_bar_font(font_px)
    font_ascii = font_bar or load_mono_font(font_px)
    font_cjk = load_font(font_px)
    font_cjk_bold = load_font(font_px, bold=True)

    for m in re.finditer(r"<rect\b([^>]*)/?>", text, re.I):
        attrs = m.group(1)
        if 'width="100%"' in attrs:
            continue

        def attr(name: str, default: str = "0") -> str:
            am = re.search(rf'\b{name}="([^"]+)"', attrs)
            return am.group(1) if am else default

        x = unit_to_px(attr("x"))
        y = unit_to_px(attr("y"))
        w = unit_to_px(attr("width"))
        h = unit_to_px(attr("height"))
        fill = None
        for c in attr("class", "").split():
            if "fill" in classes.get(c, {}):
                fill = classes[c]["fill"]
        if fill:
            d.rectangle([x, y, x + w, y + h], fill=fill)

    tspan_re = re.compile(r"<tspan([^>]*)>([\s\S]*?)</tspan>", re.I)
    for tm in re.finditer(r"<text\b([^>]*)>([\s\S]*?)</text>", text, re.I):
        tattrs, inner = tm.group(1), tm.group(2)
        xm = re.search(r'\bx="([^"]+)"', tattrs)
        ym = re.search(r'\by="([^"]+)"', tattrs)
        x0 = unit_to_px(xm.group(1) if xm else "0")
        y0 = unit_to_px(ym.group(1) if ym else "0")
        cursor = 0.0
        parts = tspan_re.findall(inner) or [("", inner)]
        for sattrs, stext in parts:
            stext = htmlmod.unescape(re.sub(r"<[^>]+>", "", stext)).replace("\r", "")
            cls = ""
            cm = re.search(r'\bclass="([^"]+)"', sattrs)
            if cm:
                cls = cm.group(1)
            fill = "#bbbbbb"
            underline = False
            bold = False
            for c in cls.split():
                props = classes.get(c, {})
                if "fill" in props:
                    fill = props["fill"]
                if "underline" in props.get("text-decoration", ""):
                    underline = True
                if props.get("font-weight", "").lower() in ("bold", "700"):
                    bold = True
                if c == "bold":
                    bold = True
                if c == "underline":
                    underline = True
            for ch in stext:
                if ch in "\n\x00":
                    continue
                if ch == "\x08":
                    cursor = max(0.0, cursor - ch_px)
                    continue
                if ch == "\t":
                    cursor += 8 * ch_px
                    continue
                wide = unicodedata.east_asian_width(ch) in ("F", "W")
                cells = 2 if wide else 1
                if (not wide) and ord(ch) in _XYBAR_CP:
                    font = font_ascii
                else:
                    font = font_cjk_bold if bold else font_cjk
                kw: dict = {"font": font, "fill": fill, "anchor": "lm"}
                if bold and font is font_ascii:
                    kw["stroke_width"] = max(1, scale // 2)
                    kw["stroke_fill"] = fill
                d.text((x0 + cursor, y0), ch, **kw)
                if underline:
                    tw = cells * ch_px
                    d.line(
                        (x0 + cursor, y0 + em_px * 0.35, x0 + cursor + tw, y0 + em_px * 0.35),
                        fill=fill,
                        width=max(1, scale),
                    )
                cursor += cells * ch_px

    out = img
    out._terminal_svg = True  # type: ignore[attr-defined]
    return out


def _flatten_svg_gradients(svg: str) -> str:
    """PyMuPDF 常画不出 linearGradient，改成纯色以免头像底变黑。"""
    for m in re.finditer(
        r'<linearGradient[^>]*id="([^"]+)"[\s\S]*?</linearGradient>', svg, re.I
    ):
        stops = re.findall(r'stop-color="([^"]+)"', m.group(0), re.I)
        if not stops:
            continue
        svg = svg.replace(f"url(#{m.group(1)})", stops[len(stops) // 2])
    return svg


def _rasterize_plain_svg(raw: bytes) -> Image.Image | None:
    try:
        import cairosvg

        png = cairosvg.svg2png(bytestring=raw)
        return Image.open(io.BytesIO(png)).convert("RGB")
    except Exception:
        pass
    try:
        import pymupdf

        svg = _flatten_svg_gradients(raw.decode("utf-8", "replace"))
        doc = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
        try:
            pix = doc[0].get_pixmap(dpi=144, alpha=True)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            return bg
        finally:
            doc.close()
    except Exception:
        return None


def decode_image_bytes(url: str, data: bytes, content_type: str = "") -> Image.Image | None:
    try:
        if _is_svg(url, data, content_type):
            raw = data
            if url.startswith("data:image/svg+xml"):
                import base64
                from urllib.parse import unquote

                payload = url.split(",", 1)[-1]
                if ";base64" in url[:64]:
                    raw = base64.b64decode(payload)
                else:
                    raw = unquote(payload).encode("utf-8", "ignore")
            text = raw.decode("utf-8", "replace")
            host = (urlparse(url).netloc or "").lower()
            if "check.place" in host or _looks_like_terminal_svg(text):
                return rasterize_terminal_svg(raw)
            img = _rasterize_plain_svg(raw)
            if img:
                return img
            if _looks_like_terminal_svg(text):
                return rasterize_terminal_svg(raw)
            return None
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


async def fetch_image(url: str, referer: str | None = None) -> Image.Image | None:
    if not url:
        return None
    try:
        await ensure_fonts()
        data, ctype = await fetch_bytes(url, referer=referer)
        return decode_image_bytes(url, data, ctype)
    except Exception:
        return None


async def fetch_images(urls: list[str], referer: str | None = None) -> list[Image.Image | None]:
    if not urls:
        return []
    return list(await asyncio.gather(*[fetch_image(u, referer) for u in urls]))


def _paste_avatar(card: Image.Image, avatar: Image.Image, x: int, y: int, size: int = 110) -> None:
    cropped = circle_crop(avatar, size)
    ring = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, size + 8, size + 8), fill=(219, 234, 254, 255))
    card.alpha_composite(ring, (x - 4, y - 4))
    card.alpha_composite(cropped, (x, y))


async def render_nodeseek_image(data: CardPage) -> bytes:
    await ensure_fonts()
    w, pad = 1320, 56
    inner_w = w - pad * 4
    bg_color, card_color, text, muted, line_c = "#f5f7fb", "#ffffff", "#111827", "#6b7280", "#e5e7eb"

    title_font = load_font(50, bold=True)
    body_font = load_font(32)
    author_font = load_font(34, bold=True)
    small_font = load_font(24)
    label_font = load_font(26, bold=True)
    d = ImageDraw.Draw(Image.new("RGB", (w, 400), bg_color))

    title_lines = wrap_text(d, data.question_title or data.page_title, title_font, inner_w)
    img_urls = [it["src"] for it in data.items if it.get("type") == "image"]
    raw_images = await fetch_images(img_urls, referer=data.source_url)
    prepared: list[Image.Image | None] = []
    total_h = 220 + len(title_lines) * 66 + 200
    for img in raw_images:
        if img:
            cap = 1800 if getattr(img, "_terminal_svg", False) else 900
            fitted = fit_image(img, inner_w, cap)
            prepared.append(fitted)
            total_h += fitted.height + 90
        else:
            prepared.append(None)
            total_h += 120
    for it in data.items:
        if it.get("type") == "text":
            total_h += max(50, len(wrap_text(d, it.get("text") or "", body_font, inner_w)) * 44) + 22
    total_h = max(total_h, 1800)

    bg, card, cd = new_canvas(w, total_h, pad, bg=bg_color, card=card_color, line=line_c)
    x, y = pad + 44, 84
    if data.avatar_url:
        avatar_img = await fetch_image(data.avatar_url, referer=data.avatar_referer)
        if avatar_img:
            _paste_avatar(card, avatar_img, x, y)
    cd.text((x + 136, y + 10), data.author_name or "", font=author_font, fill=text)
    cd.text((x + 136, y + 52), f"NodeSeek · {data.question_description or ''}".strip(), font=small_font, fill=muted)
    if data.created_time:
        cd.text((x + 136, y + 84), data.created_time, font=small_font, fill=muted)
    y += 148
    for line in title_lines:
        cd.text((x, y), line, font=title_font, fill=text)
        y += 66
    y += 16
    cd.line((x, y, w - pad - 44, y), fill=line_c, width=2)
    y += 26

    img_idx = 0
    for it in data.items:
        if it.get("type") == "text":
            y = draw_wrapped(cd, it.get("text") or "", body_font, text, x, y, inner_w, line_gap=12)
            y += 22
        elif it.get("type") == "image":
            cd.text((x, y), "原帖配图", font=label_font, fill=muted)
            y += 40
            img = prepared[img_idx] if img_idx < len(prepared) else None
            img_idx += 1
            if img:
                y = paste_framed(card, img, x, y)

    return to_png_bytes(bg, card)


async def render_linuxdo_image(data: CardPage, *, min_h: int = 2600) -> bytes:
    await ensure_fonts()
    w, pad = 1320, 56
    inner_w = w - pad * 4
    bg_c, card_c, text, muted, line_c = "#f5f7fb", "#ffffff", "#111827", "#6b7280", "#e5e7eb"
    accent, pre_bg, pre_text = "#2563eb", "#111827", "#e5e7eb"
    quote_bg, quote_bar = "#f8fafc", "#93c5fd"

    title_font = load_font(46, bold=True)
    body_font = load_font(30)
    author_font = load_font(34, bold=True)
    small_font = load_font(24)
    label_font = load_font(26, bold=True)
    code_font = load_mono_font(20)
    dummy = ImageDraw.Draw(Image.new("RGB", (w, 400), bg_c))

    title_lines = wrap_text(dummy, data.question_title or data.page_title, title_font, inner_w)
    img_urls = [it["src"] for it in data.items if it.get("type") == "image"]
    raw_images = await fetch_images(img_urls)
    prepared: list[Image.Image | None] = []
    total_h = 220 + len(title_lines) * 60 + 220
    img_i = 0
    for it in data.items:
        typ = it.get("type")
        if typ == "text":
            total_h += len(wrap_text(dummy, it.get("text") or "", body_font, inner_w)) * 38 + 20
        elif typ == "heading":
            total_h += len(wrap_text(dummy, it.get("text") or "", author_font, inner_w)) * 44 + 18
        elif typ == "code":
            total_h += len((it.get("text") or "").splitlines()) * 26 + 36
        elif typ == "quote":
            quote_text = ((it.get("author") or "") + "\n" + (it.get("text") or "")).strip()
            total_h += len(wrap_text(dummy, quote_text, body_font, inner_w - 48)) * 38 + 40
        elif typ == "divider":
            total_h += 24
        elif typ == "image":
            img = raw_images[img_i] if img_i < len(raw_images) else None
            img_i += 1
            if img:
                fitted = fit_image(img, inner_w, 860)
                prepared.append(fitted)
                total_h += fitted.height + 54
            else:
                prepared.append(None)
                total_h += 96
    total_h = max(total_h, min_h)

    bg, card, cd = new_canvas(w, total_h, pad, bg=bg_c, card=card_c, line=line_c)
    x, y = pad + 44, 84
    tag = data.badge or "linux.do · 首楼"
    tag_box = cd.textbbox((0, 0), tag, font=label_font)
    cd.rounded_rectangle((x, y, x + (tag_box[2] - tag_box[0]) + 26, y + 44), radius=22, fill=(232, 240, 255, 255))
    cd.text((x + 13, y + 7), tag, font=label_font, fill=accent)
    y += 66

    if data.avatar_url:
        avatar_img = await fetch_image(data.avatar_url, referer=data.avatar_referer)
        if avatar_img:
            _paste_avatar(card, avatar_img, x, y)
    cd.text((x + 136, y + 10), data.author_name or "", font=author_font, fill=text)
    nickname = f"@{data.author_nickname}" if data.author_nickname else ""
    cd.text((x + 136, y + 52), nickname, font=small_font, fill=muted)
    if data.created_time:
        cd.text((x + 136, y + 84), data.created_time, font=small_font, fill=muted)
    y += 148
    for line in title_lines:
        cd.text((x, y), line, font=title_font, fill=text)
        y += 60
    y += 16
    cd.line((x, y, w - pad - 44, y), fill=line_c, width=2)
    y += 26

    img_idx = 0
    shown = 0
    for it in data.items:
        typ = it.get("type")
        if typ == "text":
            y = draw_wrapped(cd, it.get("text") or "", body_font, text, x, y, inner_w, line_gap=8)
            y += 18
        elif typ == "heading":
            y = draw_wrapped(cd, it.get("text") or "", author_font, text, x, y, inner_w, line_gap=8)
            y += 16
        elif typ == "code":
            lines = (it.get("text") or "").splitlines()
            box_h = len(lines) * 26 + 24
            cd.rounded_rectangle((x, y, x + inner_w, y + box_h), radius=18, fill=pre_bg)
            py = y + 12
            for line in lines:
                cd.text((x + 16, py), line[:120], font=code_font, fill=pre_text)
                py += 26
            y += box_h + 18
        elif typ == "quote":
            quote_text = ((it.get("author") or "") + "\n" + (it.get("text") or "")).strip()
            lines = wrap_text(cd, quote_text, body_font, inner_w - 48)
            box_h = len(lines) * 38 + 24
            cd.rounded_rectangle((x, y, x + inner_w, y + box_h), radius=18, fill=quote_bg, outline=line_c, width=1)
            cd.rounded_rectangle((x, y, x + 10, y + box_h), radius=5, fill=quote_bar)
            qy = y + 12
            for line in lines:
                if line == "":
                    qy += 12
                else:
                    cd.text((x + 24, qy), line, font=body_font, fill=text)
                    qy += 38
            y += box_h + 18
        elif typ == "divider":
            cd.line((x, y + 8, w - pad - 44, y + 8), fill=line_c, width=2)
            y += 24
        elif typ == "image":
            shown += 1
            cd.text((x, y), f"图片 {shown}", font=label_font, fill=muted)
            y += 38
            img = prepared[img_idx] if img_idx < len(prepared) else None
            img_idx += 1
            if img:
                y = paste_framed(card, img, x, y)
            else:
                cd.rounded_rectangle((x, y, x + inner_w, y + 80), radius=18, fill=(248, 250, 252, 255), outline=line_c, width=1)
                cd.text((x + 20, y + 24), it.get("src") or "", font=small_font, fill=muted)
                y += 96

    return to_png_bytes(bg, card)


async def render_xhs_image(data: CardPage) -> bytes:
    await ensure_fonts()
    w, pad = 1320, 56
    inner_w = w - pad * 4
    bg_c, card_c, text, muted, line_c = "#f6f8fc", "#ffffff", "#111827", "#6b7280", "#e5e7eb"
    accent, author_bg = "#ef4444", "#fff1f2"

    title_font = load_font(54, bold=True)
    author_font = load_font(28, bold=True)
    head_font = load_font(34, bold=True)
    body_font = load_font(30)
    tag_font = load_font(26, bold=True)
    small_font = load_font(24)
    dummy = ImageDraw.Draw(Image.new("RGB", (w, 400), bg_c))

    title_lines = wrap_text(dummy, data.question_title or data.page_title, title_font, inner_w)
    img_urls = [it["src"] for it in data.items if it.get("type") == "image"]
    raw_images = await fetch_images(img_urls, referer="https://www.xiaohongshu.com/")
    prepared: list[Image.Image | None] = []
    total_h = 120 + len(title_lines) * 68 + (200 if data.avatar_url or data.author_name else 80)
    img_i = 0
    for it in data.items:
        if it.get("type") == "text":
            total_h += 44 + len(wrap_text(dummy, it.get("text") or "", body_font, inner_w)) * 38 + 24
        elif it.get("type") == "image":
            img = raw_images[img_i] if img_i < len(raw_images) else None
            img_i += 1
            if img:
                fitted = fit_image(img, inner_w, 780)
                prepared.append(fitted)
                total_h += 42 + fitted.height + 30
            else:
                prepared.append(None)
                total_h += 100
    total_h = max(total_h + 100, 2200)

    bg, card, cd = new_canvas(w, total_h, pad, bg=bg_c, card=card_c, line=line_c)
    x, y = pad + 44, 88
    for line in title_lines:
        cd.text((x, y), line, font=title_font, fill=text)
        y += 68
    avatar_img = None
    if data.avatar_url:
        avatar_img = await fetch_image(data.avatar_url, referer=data.avatar_referer or "https://www.xiaohongshu.com/")
    if avatar_img:
        _paste_avatar(card, avatar_img, x, y)
        cd.text((x + 136, y + 18), data.author_name or "", font=author_font, fill=accent)
        if data.created_time:
            cd.text((x + 136, y + 60), data.created_time, font=small_font, fill=muted)
        y += 136
    elif data.author_name:
        author_text = "by " + data.author_name
        box = cd.textbbox((0, 0), author_text, font=author_font)
        cd.rounded_rectangle((x, y + 4, x + (box[2] - box[0]) + 26, y + 46), radius=20, fill=author_bg)
        cd.text((x + 13, y + 10), author_text, font=author_font, fill=accent)
        y += 64
    cd.line((x, y, w - pad - 44, y), fill=line_c, width=2)
    y += 28

    img_idx = 0
    entered_images = False
    for it in data.items:
        if it.get("type") == "text":
            cd.text((x, y), it.get("label") or "", font=head_font, fill=text)
            y += 44
            y = draw_wrapped(cd, it.get("text") or "", body_font, "#1f2937", x, y, inner_w, line_gap=8)
            y += 24
        elif it.get("type") == "image":
            if not entered_images:
                cd.line((x, y, w - pad - 44, y), fill=line_c, width=2)
                y += 28
                cd.text((x, y), "配图", font=head_font, fill=text)
                y += 46
                entered_images = True
            cd.text((x, y), f"图片 {img_idx + 1}", font=tag_font, fill=muted)
            y += 38
            img = prepared[img_idx] if img_idx < len(prepared) else None
            img_idx += 1
            if img:
                y = paste_framed(card, img, x, y)
            else:
                cd.rounded_rectangle((x, y, x + inner_w, y + 84), radius=18, fill=(248, 250, 252, 255), outline=line_c, width=1)
                cd.text((x + 20, y + 24), "图片加载失败", font=small_font, fill=muted)
                y += 96

    return to_png_bytes(bg, card)


def _ns_norm(tag_or_text) -> str:
    if tag_or_text is None:
        return ""
    if hasattr(tag_or_text, "get_text"):
        return " ".join(tag_or_text.get_text(" ", strip=True).split())
    return " ".join(str(tag_or_text).split())


def _ns_abs(base: str, src: str) -> str:
    return urllib.parse.urljoin(base, src)


def _is_ansi_pre(node: Tag) -> bool:
    if node.name not in ("pre", "code"):
        return False
    code = node if node.name == "code" else node.find("code")
    cls = " ".join((code.get("class") if code else node.get("class")) or []).lower()
    if "ansi" in cls:
        return True
    text = node.get_text()
    return "[1m" in text or "[36m" in text or "\x1b[" in text


def _checkplace_svgs(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"https://Report\.Check\.Place/[^\s\"'<>\\]+?\.svg", text or "", re.I):
        src = m.group(0).rstrip(").,，。]")
        if src not in seen:
            seen.add(src)
            found.append(src)
    return found


def _add_img(items: list[dict], seen: set[str], src: str) -> None:
    if not src or src in seen:
        return
    seen.add(src)
    items.append({"type": "image", "src": src})


def _append_nodeseek_block(node: Tag, base: str, items: list[dict], seen_img: set[str]) -> None:
    """Tab/details 内容：有图用图；ANSI 终端块改用 Check.Place SVG，不再画乱码。"""
    html = str(node)
    for img in node.find_all("img"):
        src = img.get("src") or ""
        if src:
            _add_img(items, seen_img, _ns_abs(base, src))
    for src in _checkplace_svgs(html):
        _add_img(items, seen_img, src)
    if node.find("img") or _checkplace_svgs(html):
        return
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "p":
            _parse_nodeseek_paragraph(child, base, items)
        elif child.name == "ul":
            lines = ["• " + _ns_norm(li) for li in child.select("li") if _ns_norm(li)]
            if lines:
                items.append({"type": "text", "text": "\n".join(lines)})
        elif child.name == "pre":
            if _is_ansi_pre(child):
                continue
            txt = "\n".join(line.rstrip() for line in child.get_text("\n", strip=True).splitlines() if line.strip())
            if txt:
                items.append({"type": "text", "text": txt[:4000]})


def _is_svg_href(href: str) -> bool:
    h = (href or "").lower()
    return h.endswith(".svg") or "report.check.place" in h


def _collect_svg_links(node: Tag, base: str, seen: set[str], items: list[dict]) -> None:
    for a in node.find_all("a"):
        href = a.get("href") or ""
        if not _is_svg_href(href):
            continue
        src = _ns_abs(base, href)
        if src in seen:
            continue
        seen.add(src)
        items.append({"type": "image", "src": src})


def _parse_nodeseek_paragraph(child: Tag, base: str, items: list[dict]) -> None:
    text_buf: list[str] = []

    def flush() -> None:
        txt = " ".join("".join(text_buf).split()).strip()
        text_buf.clear()
        if txt:
            items.append({"type": "text", "text": txt})

    for node in child.children:
        if isinstance(node, Tag):
            if node.name == "img":
                flush()
                src = node.get("src") or ""
                if src:
                    items.append({"type": "image", "src": _ns_abs(base, src)})
            elif node.name == "br":
                text_buf.append("\n")
            else:
                text_buf.append(node.get_text(" ", strip=False))
        else:
            text_buf.append(str(node))
    flush()


def parse_nodeseek_html(url: str, body: str) -> CardPage:
    soup = BeautifulSoup(body, "html.parser")
    h1 = soup.select_one("h1")
    title = _ns_norm(h1)
    category = ""
    for a in soup.select('a[href^="/categories/"]'):
        txt = _ns_norm(a)
        if txt:
            category = txt
            break
    created_time = ""
    time_el = soup.select_one("time")
    if time_el:
        created_time = (time_el.get("title") or time_el.get("datetime") or _ns_norm(time_el)).strip()
    author_name = ""
    for a in soup.select('a[href^="/space/"]'):
        txt = _ns_norm(a)
        if txt:
            author_name = txt
            break
    avatar_url = ""
    img = soup.select_one('img[src*="/avatar/"]')
    if img and img.get("src"):
        avatar_url = _ns_abs("https://www.nodeseek.com", img.get("src"))

    items: list[dict] = []
    seen_img: set[str] = set()
    article = soup.select_one("article.post-content")
    if article:
        for child in article.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "h4":
                txt = _ns_norm(child)
                if txt:
                    items.append({"type": "text", "text": txt})
            elif child.name == "p":
                _parse_nodeseek_paragraph(child, url, items)
            elif child.name == "blockquote":
                txt = _ns_norm(child)
                if txt:
                    items.append({"type": "text", "text": "「" + txt + "」"})
            elif child.name == "ul":
                lines = ["• " + _ns_norm(li) for li in child.select("li") if _ns_norm(li)]
                if lines:
                    items.append({"type": "text", "text": "\n".join(lines)})
            elif child.name == "details":
                summary = child.select_one("summary")
                if summary:
                    txt = _ns_norm(summary)
                    if txt:
                        items.append({"type": "text", "text": "【" + txt + "】"})
                _append_nodeseek_block(child, url, items, seen_img)
            elif child.name == "div" and "nsk-magic-tabs" in (child.get("class") or []):
                tab_title = None
                for inner in child.children:
                    if not isinstance(inner, Tag):
                        continue
                    classes = inner.get("class") or []
                    if "nsk-magic-tab-title" in classes:
                        tab_title = _ns_norm(inner)
                    elif "nsk-magic-tab-body" in classes:
                        if tab_title:
                            items.append({"type": "text", "text": f"【{tab_title}】"})
                        _append_nodeseek_block(inner, url, items, seen_img)
                        tab_title = None
            _collect_svg_links(child, url, seen_img, items)
        for it in items:
            if it.get("type") == "image" and it.get("src"):
                seen_img.add(it["src"])
        _collect_svg_links(article, url, seen_img, items)

    m = re.search(r"/post-(\d+)", url)
    post_id = m.group(1) if m else str(int(datetime.now().timestamp()))
    return CardPage(
        platform="nodeseek",
        source_url=url,
        answer_id=post_id,
        page_title=title,
        question_title=title,
        question_description=category,
        author_name=author_name,
        author_nickname=author_name,
        avatar_url=avatar_url,
        avatar_referer=url,
        created_time=created_time,
        items=items,
    )


def parse_linuxdo_topic_id(url: str) -> str:
    path = (urlparse(url).path or "").rstrip("/")
    for pat in (r"/t/topic/(\d+)", r"/t/[^/]+/(\d+)", r"/t/(\d+)", r"/topic/(\d+)"):
        m = re.search(pat, path)
        if m:
            return m.group(1)
    raise ValueError("URL里没有找到 linux.do topic id")


def _ld_abs(src: str) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://linux.do" + src
    return src


def _ld_norm(s: str) -> str:
    s = (s or "").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return s.strip()


def parse_linuxdo_post(url: str, payload: dict) -> CardPage:
    topic_id = parse_linuxdo_topic_id(url)
    posts = (payload.get("post_stream") or {}).get("posts") or []
    if not posts:
        raise ValueError("linux.do 帖子没有首楼数据")
    post = posts[0]
    soup = BeautifulSoup(post.get("cooked") or "", "html.parser")
    root = soup.body or soup
    items: list[dict] = []

    def flush_text(buf: list[str]) -> None:
        txt = _ld_norm("".join(buf))
        if txt:
            items.append({"type": "text", "text": txt})

    def walk(node, buf: list[str]) -> None:
        if isinstance(node, NavigableString):
            buf.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        cls = set(node.get("class", []))
        if node.name == "br":
            buf.append("\n")
            return
        if node.name == "pre":
            flush_text(buf)
            buf.clear()
            code = node.get_text("\n", strip=False).strip("\n")
            if code.strip():
                items.append({"type": "code", "text": code})
            return
        if node.name == "img":
            flush_text(buf)
            buf.clear()
            src = _ld_abs(node.get("src") or node.get("data-src") or "")
            if src:
                items.append({"type": "image", "src": src, "alt": node.get("alt") or ""})
            return
        if node.name == "a" and "lightbox" in cls:
            flush_text(buf)
            buf.clear()
            href = _ld_abs(node.get("href") or "")
            if href:
                items.append({"type": "image", "src": href, "alt": node.get("title") or ""})
            return
        if node.name == "aside" and "quote" in cls:
            flush_text(buf)
            buf.clear()
            title_el = node.select_one(".title")
            quote_author = _ld_norm(title_el.get_text(" ", strip=True)) if title_el else ""
            q = node.find("blockquote")
            qtxt = _ld_norm(q.get_text("\n", strip=True)) if q else ""
            avatar = node.find("img", class_="avatar")
            avatar_src = _ld_abs(avatar.get("src") or "") if avatar else ""
            items.append({"type": "quote", "author": quote_author, "text": qtxt, "avatar": avatar_src})
            return
        for child in node.children:
            walk(child, buf)

    for child in root.children:
        if isinstance(child, NavigableString):
            t = _ld_norm(str(child))
            if t:
                items.append({"type": "text", "text": t})
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in ("ul", "ol"):
            lines = []
            for li in child.find_all("li", recursive=False):
                txt = _ld_norm(li.get_text(" ", strip=True))
                if txt:
                    lines.append("• " + txt)
            if lines:
                items.append({"type": "text", "text": "\n".join(lines)})
        elif child.name == "pre":
            code = child.get_text("\n", strip=False).strip("\n")
            if code.strip():
                items.append({"type": "code", "text": code})
        elif child.name == "hr":
            items.append({"type": "divider"})
        else:
            buf: list[str] = []
            walk(child, buf)
            flush_text(buf)

    clean: list[dict] = []
    for it in items:
        if it.get("type") == "text" and not (it.get("text") or "").strip():
            continue
        if clean and it == clean[-1]:
            continue
        clean.append(it)

    avatar_template = post.get("avatar_template") or ""
    avatar_url = ("https://linux.do" + avatar_template.replace("{size}", "240")) if avatar_template else ""
    return CardPage(
        platform="linuxdo",
        source_url=url,
        answer_id=topic_id,
        page_title=payload.get("title") or "",
        question_title=payload.get("title") or "",
        author_name=post.get("display_username") or post.get("username") or "",
        author_nickname=post.get("username") or "",
        avatar_url=avatar_url,
        created_time=format_created_time(post.get("created_at") or ""),
        items=clean,
    )


def card_from_xhs(url: str, detail) -> CardPage:
    from .xhs_logic import is_xhs_video_url

    items: list[dict] = []
    if getattr(detail, "description", ""):
        items.append({"type": "text", "label": "内容", "text": detail.description})
    if getattr(detail, "tags", ""):
        items.append({"type": "text", "label": "标签", "text": detail.tags})
    if getattr(detail, "push_time", ""):
        items.append({"type": "text", "label": "发布时间", "text": detail.push_time})
    if getattr(detail, "update_time", ""):
        items.append({"type": "text", "label": "最后更新时间", "text": detail.update_time})
    videos = set(getattr(detail, "video_urls", None) or [])
    for src in getattr(detail, "download_urls", None) or []:
        if src and src not in videos and not is_xhs_video_url(str(src)):
            items.append({"type": "image", "src": str(src)})
    answer_id = str(getattr(detail, "id", "") or int(datetime.now().timestamp()))
    title = getattr(detail, "title", "") or ""
    author = getattr(detail, "username", "") or ""
    return CardPage(
        platform="xhs",
        source_url=url,
        answer_id=answer_id,
        page_title=title,
        question_title=title,
        author_name=author,
        author_nickname=author,
        avatar_url=getattr(detail, "avatar", "") or "",
        avatar_referer="https://www.xiaohongshu.com/",
        created_time=getattr(detail, "push_time", "") or "",
        items=items,
    )


_V2EX_ALERT_RE = re.compile(r"^\[!(WARNING|IMPORTANT|NOTE|TIP|CAUTION)\]\s*", re.I)
_V2EX_TOPIC_RE = re.compile(r"/t/(\d+)")
_V2EX_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "ul", "ol", "hr", "div"}


def parse_v2ex_topic_id(url: str) -> str:
    path = (urlparse(url).path or "").rstrip("/")
    m = _V2EX_TOPIC_RE.search(path)
    if not m:
        raise ValueError("URL里没有找到 V2EX topic id")
    return m.group(1)


def _v2ex_abs(src: str) -> str:
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.v2ex.com" + src
    return src


def _v2ex_avatar(member: dict) -> str:
    for key in ("avatar_xxlarge", "avatar_xlarge", "avatar_large", "avatar_normal"):
        url = _v2ex_abs((member.get(key) or "").strip())
        if url:
            if "gravatar" in url:
                url = re.sub(r"([?&]s=)\d+", r"\g<1>240", url)
            return url
    return ""


def parse_v2ex_content(html: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    container = soup.find("div", class_="markdown_body") or soup.body or soup
    items: list[dict] = []

    def add_text(text: str) -> None:
        txt = _ld_norm(text)
        if txt:
            items.append({"type": "text", "text": txt})

    def handle_block(node: Tag) -> None:
        name = node.name
        if name == "hr":
            items.append({"type": "divider"})
            return
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            txt = _ld_norm(node.get_text(" ", strip=True))
            if txt:
                items.append({"type": "heading", "text": txt})
            return
        if name == "pre":
            code = node.get_text("\n", strip=False).strip("\n")
            if code.strip():
                items.append({"type": "code", "text": code})
            return
        if name == "blockquote":
            txt = _ld_norm(node.get_text("\n", strip=True))
            txt = _V2EX_ALERT_RE.sub("", txt)
            if txt:
                items.append({"type": "quote", "author": "", "text": txt})
            return
        if name in ("ul", "ol"):
            lines = []
            for li in node.find_all("li", recursive=False):
                txt = _ld_norm(li.get_text(" ", strip=True))
                if txt:
                    lines.append("• " + txt)
            if lines:
                items.append({"type": "text", "text": "\n".join(lines)})
            return
        buf: list[str] = []

        def flush() -> None:
            add_text("".join(buf))
            buf.clear()

        def walk(n) -> None:
            if isinstance(n, NavigableString):
                buf.append(str(n))
                return
            if not isinstance(n, Tag):
                return
            if n.name == "br":
                buf.append("\n")
                return
            if n.name == "img":
                flush()
                src = _v2ex_abs(n.get("src") or n.get("data-src") or "")
                if src:
                    items.append({"type": "image", "src": src, "alt": n.get("alt") or ""})
                return
            if n.name in ("pre", "blockquote", "ul", "ol", "hr"):
                flush()
                handle_block(n)
                return
            for child in n.children:
                walk(child)

        walk(node)
        flush()

    children = list(container.children)
    has_block = any(isinstance(c, Tag) and c.name in _V2EX_BLOCK_TAGS for c in children)
    if not has_block:
        handle_block(container)
    else:
        for child in children:
            if isinstance(child, NavigableString):
                add_text(str(child))
            elif isinstance(child, Tag):
                handle_block(child)

    clean: list[dict] = []
    for it in items:
        if it.get("type") == "text" and not (it.get("text") or "").strip():
            continue
        if clean and it == clean[-1]:
            continue
        clean.append(it)
    return clean


def parse_v2ex_topic(url: str, payload: dict) -> CardPage:
    topic_id = str(payload.get("id") or parse_v2ex_topic_id(url))
    member = payload.get("member") or {}
    node = payload.get("node") or {}
    title = (payload.get("title") or "").strip()
    node_title = (node.get("title") or node.get("name") or "").strip()
    username = (member.get("username") or "").strip()
    html = payload.get("content_rendered") or ""
    if not html and payload.get("content"):
        html = htmlmod.escape(str(payload.get("content"))).replace("\n", "<br />")
    return CardPage(
        platform="v2ex",
        source_url=payload.get("url") or url,
        answer_id=topic_id,
        page_title=title,
        question_title=title,
        question_description=node_title,
        author_name=username,
        author_nickname=username,
        avatar_url=_v2ex_avatar(member),
        avatar_referer="https://www.v2ex.com/",
        created_time=format_created_time(str(payload.get("created") or "")),
        badge=f"V2EX · {node_title}" if node_title else "V2EX · 主帖",
        items=parse_v2ex_content(html),
    )


def parse_v2ex_html(url: str, body: str) -> CardPage:
    soup = BeautifulSoup(body, "html.parser")
    header = soup.select_one(".header") or soup
    title = _ns_norm(header.select_one("h1") or soup.select_one("h1"))
    author_name = ""
    for a in header.select('a[href^="/member/"]'):
        txt = _ns_norm(a)
        if txt:
            author_name = txt
            break
    avatar_url = ""
    img = header.select_one("img.avatar")
    if img and img.get("src"):
        avatar_url = _v2ex_abs(img.get("src"))
        if "gravatar" in avatar_url:
            avatar_url = re.sub(r"([?&]s=)\d+", r"\g<1>240", avatar_url)
    node_title = ""
    for a in header.select('a[href^="/go/"]'):
        txt = _ns_norm(a)
        if txt:
            node_title = txt
            break
    created_time = ""
    time_el = header.select_one("span[title]")
    if time_el:
        created_time = (time_el.get("title") or "").strip()
    content = soup.select_one(".topic_content")
    html = str(content) if content else ""
    topic_id = parse_v2ex_topic_id(url)
    return CardPage(
        platform="v2ex",
        source_url=url,
        answer_id=topic_id,
        page_title=title,
        question_title=title,
        question_description=node_title,
        author_name=author_name,
        author_nickname=author_name,
        avatar_url=avatar_url,
        avatar_referer="https://www.v2ex.com/",
        created_time=created_time,
        badge=f"V2EX · {node_title}" if node_title else "V2EX · 主帖",
        items=parse_v2ex_content(html),
    )


async def render_v2ex_image(data: CardPage) -> bytes:
    return await render_linuxdo_image(data, min_h=1200)


async def render_nodeseek(url: str) -> bytes:
    data, _ = await fetch_bytes(url, referer="https://www.nodeseek.com/")
    return await render_nodeseek_image(parse_nodeseek_html(url, data.decode("utf-8", "ignore")))


async def render_linuxdo(url: str) -> bytes:
    topic_id = parse_linuxdo_topic_id(url)
    raw, _ = await fetch_bytes(f"https://linux.do/t/topic/{topic_id}.json", referer="https://linux.do/")
    payload = json.loads(raw.decode("utf-8", "ignore"))
    return await render_linuxdo_image(parse_linuxdo_post(url, payload))


async def render_v2ex(url: str) -> bytes:
    topic_id = parse_v2ex_topic_id(url)
    try:
        raw, _ = await fetch_bytes(
            f"https://www.v2ex.com/api/topics/show.json?id={topic_id}",
            referer="https://www.v2ex.com/",
        )
        payload = json.loads(raw.decode("utf-8", "ignore"))
        if isinstance(payload, list):
            if not payload:
                raise ValueError("V2EX 帖子不存在")
            payload = payload[0]
        if not isinstance(payload, dict) or not payload.get("id"):
            raise ValueError("V2EX API 返回异常")
        page = parse_v2ex_topic(url, payload)
    except Exception as e:
        logger.warning(f"v2ex api failed, fallback html: {e}")
        raw, _ = await fetch_bytes(f"https://www.v2ex.com/t/{topic_id}", referer="https://www.v2ex.com/")
        page = parse_v2ex_html(url, raw.decode("utf-8", "ignore"))
    return await render_v2ex_image(page)


async def render_xhs(url: str, detail=None) -> bytes:
    from .xhs_logic import XhsLogic

    if detail is None:
        detail = await XhsLogic.detail(url)
    return await render_xhs_image(card_from_xhs(url, detail))


def card_from_heybox(url: str, detail) -> CardPage:
    items: list[dict] = []
    if getattr(detail, "description", ""):
        items.append({"type": "text", "text": detail.description})
    if getattr(detail, "tags", ""):
        items.append({"type": "text", "text": "#" + " #".join(str(detail.tags).split())})
    for src in getattr(detail, "images", None) or []:
        if src:
            items.append({"type": "image", "src": str(src)})
    topic = getattr(detail, "topic", "") or ""
    author = getattr(detail, "username", "") or ""
    title = getattr(detail, "title", "") or ""
    created = getattr(detail, "push_time", "") or ""
    ip_location = getattr(detail, "ip_location", "") or ""
    if ip_location:
        created = f"{created} · {ip_location}".strip(" ·")
    return CardPage(
        platform="heybox",
        source_url=url,
        answer_id=str(getattr(detail, "id", "") or int(datetime.now().timestamp())),
        page_title=title,
        question_title=title,
        author_name=author,
        author_nickname=author,
        avatar_url=getattr(detail, "avatar", "") or "",
        avatar_referer="https://www.xiaoheihe.cn/",
        created_time=created,
        badge=f"小黑盒 · {topic}" if topic else "小黑盒",
        items=items,
    )


async def render_heybox(url: str, detail=None) -> bytes:
    from .heybox_logic import HeyboxLogic

    if detail is None:
        detail = await HeyboxLogic.detail(url)
    return await render_linuxdo_image(card_from_heybox(url, detail), min_h=1200)

