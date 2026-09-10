"""Proxy video covers so the browser does not send a localhost Referer (Bilibili CDN 403)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote, urljoin, urlparse

import httpx

MAX_BYTES = 2_000_000
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ThumbError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_image_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("http://"):
        return "https://" + value[len("http://") :]
    return value


def to_proxy_url(url: str | None) -> str | None:
    if not url:
        return None
    normalized = normalize_image_url(url)
    if not normalized.startswith("http"):
        return None
    return "/api/thumb?url=" + quote(normalized, safe="")


def referer_for(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if any(token in host for token in ("hdslb.com", "bilibili.com", "bilivideo.com", "b23.tv")):
        return "https://www.bilibili.com/"
    if any(
        token in host
        for token in ("douyinpic.com", "douyinvod.com", "byteimg.com", "douyin.com", "iesdouyin.com")
    ):
        return "https://www.iesdouyin.com/"
    return None


def _host_is_public(hostname: str) -> bool:
    host = hostname.strip("[]")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def is_safe_image_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = parsed.hostname
    if not host:
        return False
    return _host_is_public(host)


def fetch_thumbnail(url: str) -> tuple[bytes, str]:
    current = normalize_image_url(url)
    if not is_safe_image_url(current):
        raise ThumbError("invalid_url", "封面地址不安全或无效")

    headers = {"User-Agent": BROWSER_UA}
    referer = referer_for(current)
    if referer:
        headers["Referer"] = referer
        parsed_ref = urlparse(referer)
        headers["Origin"] = f"{parsed_ref.scheme}://{parsed_ref.netloc}"

    with httpx.Client(follow_redirects=False, timeout=12.0) as client:
        for _ in range(4):
            if not is_safe_image_url(current):
                raise ThumbError("invalid_url", "封面地址不安全或无效")
            response = client.get(current, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ThumbError("thumb_failed", "封面重定向失败")
                current = urljoin(current, location)
                continue
            if response.status_code >= 400:
                raise ThumbError("thumb_failed", f"封面无法加载（HTTP {response.status_code}）")
            data = response.content[: MAX_BYTES + 1]
            if len(data) > MAX_BYTES:
                raise ThumbError("thumb_failed", "封面文件过大")
            content_type = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
            if not content_type.startswith("image/") and not _looks_like_image(data):
                raise ThumbError("thumb_failed", "返回的不是图片")
            if not content_type.startswith("image/"):
                content_type = "image/jpeg"
            return data, content_type
    raise ThumbError("thumb_failed", "封面重定向过多")


def _looks_like_image(data: bytes) -> bool:
    return data.startswith((b"\xff\xd8\xff", b"\x89PNG", b"GIF8", b"RIFF"))
