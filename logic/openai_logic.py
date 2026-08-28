from __future__ import annotations

import base64

from openai import AsyncOpenAI

from ..utils.config_holder import get_config
from ..utils.http_client import http_client


def _detect_image_type_from_bytes(data: bytes) -> str | None:
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
        return "jpg"
    if len(data) >= 2 and data[0] == 0x89 and data[1] == 0x50:
        return "png"
    if len(data) >= 2 and data[0] == 0x47 and data[1] == 0x49:
        return "gif"
    if len(data) >= 2 and data[0] == 0x42 and data[1] == 0x4D:
        return "bmp"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


class OpenaiLogic:
    @classmethod
    def _client(cls) -> AsyncOpenAI:
        api_key = get_config("openai_api_key")
        if not api_key:
            raise RuntimeError("未配置 openai_api_key")
        base_url = get_config("openai_base_url") or "https://api.openai.com/v1"
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    async def image(cls, prompt: str, image: bytes | None = None, model: str | None = None) -> str:
        """返回 base64 图片字符串。"""
        client = cls._client()
        model = model or get_config("openai_image_model") or "gpt-image-1"
        if image is None:
            result = await client.images.generate(model=model, prompt=prompt)
        else:
            img_type = _detect_image_type_from_bytes(image) or "png"
            filename = f"image.{img_type}"
            result = await client.images.edit(
                model=model,
                prompt=prompt,
                image=(filename, image),
            )
        data = result.data[0]
        b64 = getattr(data, "b64_json", None)
        if b64:
            return b64
        url = getattr(data, "url", None)
        if not url:
            raise RuntimeError("生成图片失败")
        resp = await http_client.get(url, follow_redirects=True)
        resp.raise_for_status()
        if not resp.content:
            raise RuntimeError("生成图片失败")
        return base64.b64encode(resp.content).decode()
