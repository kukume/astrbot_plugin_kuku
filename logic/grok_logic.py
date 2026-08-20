from __future__ import annotations

import asyncio

from ..utils.config_holder import get_config
from ..utils.http_client import http_client


class GrokLogic:
    @classmethod
    def _api_key(cls) -> str:
        key = get_config("openai_api_key")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return key

    @classmethod
    def _base_url(cls) -> str:
        return (get_config("openai_base_url") or "https://api.openai.com/v1").rstrip("/")

    @classmethod
    def _model(cls) -> str:
        return get_config("openai_video_model") or "grok-imagine-video"

    @classmethod
    async def video(cls, prompt: str, model: str | None = None) -> str:
        api_key = cls._api_key()
        base_url = cls._base_url()
        model = model or cls._model()
        create_resp = await http_client.post(
            f"{base_url}/videos",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "prompt": prompt},
            follow_redirects=True,
        )
        create_data = create_resp.json()
        video_id = create_data.get("id")
        if not video_id:
            raise RuntimeError(f"video id not found: {create_data}")
        video = create_data
        for _ in range(120):
            status = video.get("status")
            if status in ("completed", "succeeded"):
                return f"{base_url}/videos/{video_id}/content"
            if status == "done":
                url = (video.get("video") or {}).get("url")
                if not url:
                    raise RuntimeError(f"video url not found: {video}")
                return url
            if status in ("failed", "cancelled", "canceled"):
                raise RuntimeError(video.get("error") or f"video generation failed: {video}")
            await asyncio.sleep(5)
            poll = await http_client.get(
                f"{base_url}/videos/{video_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                follow_redirects=True,
            )
            video = poll.json()
        raise RuntimeError(f"video generation timeout: {video}")
