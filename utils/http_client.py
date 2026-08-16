from __future__ import annotations

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(60.0, connect=20.0),
    follow_redirects=False,
    headers={"User-Agent": DEFAULT_UA},
)
