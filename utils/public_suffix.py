from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

PSL_URL = "https://publicsuffix.org/list/public_suffix_list.dat"
_CACHE_TTL_SECONDS = 7 * 24 * 3600
_CACHE_FILE = Path(__file__).resolve().parent.parent / "cache" / "public_suffix_list.dat"


@dataclass(frozen=True)
class PublicSuffixList:
    exact: frozenset[str]
    wildcards: frozenset[str]
    exceptions: frozenset[str]

    def public_suffix(self, domain: str) -> str | None:
        labels = _split_labels(domain)
        if not labels:
            return None

        for i, _ in enumerate(labels):
            candidate = ".".join(labels[i:])
            if candidate in self.exceptions:
                rest = labels[i + 1 :]
                return ".".join(rest) if rest else None

        best: str | None = None
        best_len = -1
        for i, _ in enumerate(labels):
            candidate = ".".join(labels[i:])
            parent = ".".join(labels[i + 1 :]) if i + 1 < len(labels) else ""
            matched = candidate in self.exact or parent in self.wildcards
            if matched and candidate.count(".") >= best_len:
                best = candidate
                best_len = candidate.count(".")

        if best is None:
            return labels[-1]
        return best

    def registrable_domain(self, domain: str) -> str | None:
        """eTLD+1：最长公共后缀再加一级。一级以上全部丢弃。"""
        labels = _split_labels(domain)
        if not labels:
            return None
        suffix = self.public_suffix(domain)
        if not suffix:
            return None
        suffix_labels = suffix.split(".")
        if len(labels) <= len(suffix_labels):
            return None
        n = len(suffix_labels) + 1
        return ".".join(labels[-n:])


_psl: PublicSuffixList | None = None
_psl_loaded_at: float = 0.0


def _split_labels(domain: str) -> list[str]:
    text = (domain or "").strip().lower().rstrip(".")
    if not text:
        return []
    return [part for part in text.split(".") if part]


def parse_psl(text: str) -> PublicSuffixList:
    exact: set[str] = set()
    wildcards: set[str] = set()
    exceptions: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("*."):
            parent = line[2:].lstrip(".").lower()
            if parent:
                wildcards.add(parent)
            continue
        if line.startswith("!"):
            exception = line[1:].lstrip(".").lower()
            if exception:
                exceptions.add(exception)
            continue
        exact.add(line.lstrip(".").lower())
    return PublicSuffixList(
        exact=frozenset(exact),
        wildcards=frozenset(wildcards),
        exceptions=frozenset(exceptions),
    )


def _download_psl() -> str:
    from .config_holder import get_socks_proxy_url

    import httpx

    kwargs: dict = {
        "timeout": 30.0,
        "headers": {"User-Agent": "astrbot_plugin_kuku/public-suffix"},
        "follow_redirects": True,
    }
    proxy = get_socks_proxy_url()
    if proxy:
        kwargs["proxy"] = proxy
    with httpx.Client(**kwargs) as client:
        resp = client.get(PSL_URL)
        resp.raise_for_status()
        return resp.text


def _read_cache_file() -> str | None:
    if not _CACHE_FILE.is_file():
        return None
    age = time.time() - _CACHE_FILE.stat().st_mtime
    if age > _CACHE_TTL_SECONDS:
        return None
    return _CACHE_FILE.read_text(encoding="utf-8")


def _write_cache_file(text: str) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(text, encoding="utf-8")


def load_psl(force_refresh: bool = False) -> PublicSuffixList:
    global _psl, _psl_loaded_at
    now = time.time()
    if (
        not force_refresh
        and _psl is not None
        and now - _psl_loaded_at < _CACHE_TTL_SECONDS
    ):
        return _psl

    text = None if force_refresh else _read_cache_file()
    if text is None:
        try:
            text = _download_psl()
            _write_cache_file(text)
        except Exception:
            if _CACHE_FILE.is_file():
                text = _CACHE_FILE.read_text(encoding="utf-8")
            elif _psl is not None:
                return _psl
            else:
                raise

    _psl = parse_psl(text)
    _psl_loaded_at = now
    return _psl


def extract_host(keyword: str) -> str | None:
    text = (keyword or "").strip()
    if not text:
        return None
    if "://" in text:
        text = text.split("://", 1)[1]
    elif text.lower().startswith("//"):
        text = text[2:]
    text = text.split("/", 1)[0]
    text = text.split("?", 1)[0]
    text = text.split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[-1]
    if text.startswith("["):
        return None
    if ":" in text:
        host, port = text.rsplit(":", 1)
        if port.isdigit():
            text = host
    text = text.strip().rstrip(".")
    if not text or "." not in text:
        return None
    labels = _split_labels(text)
    if len(labels) < 2:
        return None
    return ".".join(labels)


def to_icp_domain(keyword: str) -> str | None:
    """把 URL/主机名收成备案查询用的注册域（公共后缀 + 一级）。"""
    host = extract_host(keyword)
    if not host:
        return None
    return load_psl().registrable_domain(host)
