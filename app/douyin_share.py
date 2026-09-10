"""Douyin public share-page fallback. No browser cookies, no yt-dlp fork.

Mirrors the iesdouyin SSR approach used by open-source tools such as
qgeng1465/douyin-watermark-free-downloader: mobile UA + share/video HTML.
`note_aweme_type=0` is required for current pages to embed videoInfoRes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
SHARE_HEADERS = {
    "User-Agent": MOBILE_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.iesdouyin.com/",
}
_ID_RE = re.compile(r"/(?:video|note|share/video|slides)/(\d+)", re.I)
_SHORT_RE = re.compile(r"https?://v\.douyin\.com/", re.I)


class DouyinShareError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class DouyinShareMedia:
    id: str
    title: str
    uploader: str | None
    thumbnail: str | None
    duration: int | None
    height: int | None
    webpage_url: str
    play_url: str | None
    music_url: str | None


def is_douyin_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return host.endswith("douyin.com") or host.endswith("iesdouyin.com")


def _first_http_url(node: object) -> str | None:
    if isinstance(node, str) and node.startswith("http"):
        return node
    if isinstance(node, list):
        for item in node:
            found = _first_http_url(item)
            if found:
                return found
        return None
    if isinstance(node, dict):
        for key in ("url_list", "urlList", "url"):
            found = _first_http_url(node.get(key))
            if found:
                return found
    return None


def _walk_find(obj: object, key: str) -> object | None:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _walk_find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _walk_find(value, key)
            if found is not None:
                return found
    return None


def _parse_router_data(html: str) -> dict:
    marker = "window._ROUTER_DATA"
    start = html.find(marker)
    if start < 0:
        raise DouyinShareError("分享页没有视频数据")
    brace = html.find("{", start)
    if brace < 0:
        raise DouyinShareError("分享页数据格式异常")
    try:
        data, _ = json.JSONDecoder().raw_decode(html[brace:])
    except json.JSONDecodeError as exc:
        raise DouyinShareError("分享页数据无法解析") from exc
    if not isinstance(data, dict):
        raise DouyinShareError("分享页数据为空")
    return data


def _item_from_router(data: dict) -> dict | None:
    page = ((data.get("loaderData") or {}).get("video_(id)/page") or {})
    res = page.get("videoInfoRes")
    if isinstance(res, dict):
        items = res.get("item_list") or []
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
    items = _walk_find(data, "item_list")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return None


def no_watermark(url: str) -> str:
    return url.replace("/playwm/", "/play/").replace("playwm", "play")


def resolve_aweme_id(url: str) -> str | None:
    match = _ID_RE.search(url or "")
    if match:
        return match.group(1)
    if not _SHORT_RE.search(url or ""):
        return None
    try:
        response = httpx.get(
            url.strip(),
            headers=SHARE_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        )
    except httpx.HTTPError:
        return None
    match = _ID_RE.search(str(response.url))
    return match.group(1) if match else None


def fetch_share_media(video_id: str) -> DouyinShareMedia:
    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/?note_aweme_type=0"
    last_error = "分享页没有返回视频"
    with httpx.Client(headers=SHARE_HEADERS, timeout=20.0, follow_redirects=False) as client:
        for _ in range(4):
            try:
                response = client.get(share_url)
            except httpx.HTTPError as exc:
                last_error = f"分享页请求失败：{exc.__class__.__name__}"
                continue
            if response.status_code != 200 or "play_addr" not in response.text:
                last_error = "分享页暂时没有视频直链，正在重试"
                continue
            data = _parse_router_data(response.text)
            item = _item_from_router(data)
            if not item:
                last_error = "分享页没有作品信息"
                continue
            video = item.get("video") if isinstance(item.get("video"), dict) else {}
            play_url = _first_http_url((video or {}).get("play_addr") or (video or {}).get("playAddr"))
            if play_url:
                play_url = no_watermark(play_url)
            music = item.get("music") if isinstance(item.get("music"), dict) else {}
            music_url = _first_http_url((music or {}).get("play_url") or {})
            cover = _first_http_url((video or {}).get("cover") or {})
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            duration_ms = (video or {}).get("duration") or item.get("duration")
            duration = None
            if isinstance(duration_ms, (int, float)) and duration_ms > 0:
                duration = int(duration_ms / 1000) if duration_ms > 10000 else int(duration_ms)
            title = (item.get("desc") or "").strip() or f"抖音视频 {video_id}"
            height = (video or {}).get("height")
            return DouyinShareMedia(
                id=str(item.get("aweme_id") or video_id),
                title=title,
                uploader=(author or {}).get("nickname"),
                thumbnail=cover,
                duration=duration,
                height=int(height) if isinstance(height, (int, float)) else None,
                webpage_url=f"https://www.douyin.com/video/{video_id}",
                play_url=play_url,
                music_url=music_url,
            )
    raise DouyinShareError(last_error)
