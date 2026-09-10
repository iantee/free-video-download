"""Thin yt-dlp wrapper. Do not fork or patch yt-dlp internals."""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from app.douyin_share import (
    DouyinShareError,
    DouyinShareMedia,
    SHARE_HEADERS,
    fetch_share_media,
    is_douyin_url,
    resolve_aweme_id,
)
from app.thumbs import to_proxy_url

ProgressCb = Callable[[int, str], None]


class YtdlpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


QUALITY_PRESETS: list[tuple[int | None, str, str, str]] = [
    (360, "360", "360p", "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    (720, "720", "720p", "bestvideo[height<=720]+bestaudio/best[height<=720]"),
    (1080, "1080", "1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
    (None, "best", "最佳画质", "bv*+ba/b"),
    (None, "audio", "仅音频", "bestaudio/best"),
]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


class _QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def is_http_url(url: str) -> bool:
    value = (url or "").strip()
    return value.startswith("http://") or value.startswith("https://")


def canonicalize_url(url: str) -> str:
    """Turn share/search pages into extractor-supported video URLs."""
    raw = (url or "").strip().replace("&amp;", "&")
    lowered = raw.lower()
    if "douyin.com" not in lowered and "iesdouyin.com" not in lowered:
        return raw

    video_id = None
    match = re.search(r"(?:modal_id|aweme_id|item_id)=([0-9]+)", raw, re.I)
    if match:
        video_id = match.group(1)
    if not video_id:
        path_match = re.search(r"/(?:video|note|share/video)/([0-9]+)", raw, re.I)
        if path_match:
            video_id = path_match.group(1)
    if video_id:
        return f"https://www.douyin.com/video/{video_id}"
    return raw


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "User-Agent": BROWSER_UA,
}
DOUYIN_HEADERS = {
    "Referer": "https://www.douyin.com/",
    "Origin": "https://www.douyin.com",
    "User-Agent": BROWSER_UA,
}


def _is_bilibili(url: str) -> bool:
    host = (url or "").lower()
    return any(token in host for token in ("bilibili.com", "b23.tv", "b23.wtf"))


def _is_douyin(url: str) -> bool:
    host = (url or "").lower()
    return "douyin.com" in host or "iesdouyin.com" in host


def _browser_tuple(spec: str) -> tuple[str, ...]:
    raw = spec.strip()
    if not raw:
        return ("chrome",)
    browser, _, profile = raw.partition(":")
    browser = browser.lower()
    if profile:
        return (browser, profile)
    return (browser,)


def _cookie_opt_variants(url: str) -> list[dict[str, Any]]:
    """Cookie sources to try. Optional; Douyin public videos use the share-page fallback instead."""
    cookiefile = os.getenv("COOKIES_FILE", "").strip()
    if cookiefile:
        return [{"cookiefile": cookiefile}]

    specified = os.getenv("COOKIES_FROM_BROWSER", "").strip()
    if not specified:
        return [{}]
    return [{"cookiesfrombrowser": _browser_tuple(specified)}]


def _is_cookie_lock(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "could not copy chrome cookie",
            "failed to load cookies",
            "cookie database",
        )
    )


def _needs_fresh_cookies(exc: Exception) -> bool:
    text = str(exc)
    return "Fresh cookies" in text or ("cookies" in text.lower() and "needed" in text.lower())


def base_opts(url: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Shared YoutubeDL options. Bilibili 412 needs Origin + Referer."""
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "logger": _QuietLogger(),
    }
    if extra:
        extra = dict(extra)
        extra_headers = extra.pop("http_headers", None)
        opts.update(extra)
        if extra_headers:
            merged = dict(opts.get("http_headers") or {})
            merged.update(extra_headers)
            opts["http_headers"] = merged
    if _is_bilibili(url):
        headers = dict(opts.get("http_headers") or {})
        headers.update(BILI_HEADERS)
        opts["http_headers"] = headers
    elif _is_douyin(url):
        headers = dict(opts.get("http_headers") or {})
        headers.update(DOUYIN_HEADERS)
        opts["http_headers"] = headers
    return opts


def _is_precondition_failed(exc: Exception) -> bool:
    text = str(exc)
    return "412" in text or "Precondition Failed" in text


def run_ydl(url: str, opts: dict[str, Any], *, download: bool) -> dict[str, Any]:
    last: Exception | None = None
    cookie_lock: Exception | None = None
    bili_attempts = 3 if _is_bilibili(url) else 1
    variants = _cookie_opt_variants(url)
    for index, cookie_opts in enumerate(variants):
        attempt_opts = dict(opts)
        attempt_opts.pop("cookiefile", None)
        attempt_opts.pop("cookiesfrombrowser", None)
        attempt_opts.update(cookie_opts)
        more_browsers = index + 1 < len(variants)
        with yt_dlp.YoutubeDL(attempt_opts) as ydl:
            for attempt in range(bili_attempts):
                try:
                    info = ydl.extract_info(url.strip(), download=download)
                    return ydl.sanitize_info(info) if info else {}
                except UnsupportedError:
                    raise
                except (DownloadError, ExtractorError) as exc:
                    last = exc
                    if _is_cookie_lock(exc):
                        cookie_lock = exc
                    if more_browsers and (_is_cookie_lock(exc) or _needs_fresh_cookies(exc)):
                        break
                    if attempt + 1 < bili_attempts and _is_precondition_failed(exc):
                        time.sleep(0.7 * (attempt + 1))
                        continue
                    raise cookie_lock or exc
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if _is_cookie_lock(exc):
                        cookie_lock = exc
                    if more_browsers and (_is_cookie_lock(exc) or _needs_fresh_cookies(exc)):
                        break
                    if attempt + 1 < bili_attempts and _is_precondition_failed(exc):
                        time.sleep(0.7 * (attempt + 1))
                        continue
                    raise cookie_lock or exc
    raise cookie_lock or last or RuntimeError("yt-dlp failed")


def _has_captions(info: dict[str, Any]) -> bool:
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    return bool(subs) or bool(auto)


def _entry_url(entry: dict[str, Any]) -> str | None:
    for key in ("webpage_url", "original_url", "url"):
        value = entry.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    video_id = entry.get("id")
    ie = str(entry.get("ie_key") or entry.get("extractor") or "").lower()
    if video_id and "youtube" in ie:
        return f"https://www.youtube.com/watch?v={video_id}"
    if video_id and "bili" in ie:
        bvid = video_id if str(video_id).startswith("BV") else f"BV{video_id}"
        return f"https://www.bilibili.com/video/{bvid}"
    return None


def _pick_thumbnail(info: dict[str, Any]) -> str | None:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb:
        return thumb
    thumbs = info.get("thumbnails") or []
    if thumbs:
        best = max(thumbs, key=lambda item: item.get("height") or item.get("width") or 0)
        url = best.get("url")
        if isinstance(url, str):
            return url
    return None


def simplify_formats(info: dict[str, Any]) -> list[dict[str, str]]:
    formats = info.get("formats") or []
    heights: list[int] = []
    has_audio = False
    for item in formats:
        height = item.get("height")
        vcodec = item.get("vcodec")
        acodec = item.get("acodec")
        if height and vcodec not in (None, "none"):
            heights.append(int(height))
        if acodec not in (None, "none"):
            has_audio = True

    max_height = max(heights) if heights else 0
    result: list[dict[str, str]] = []
    for limit, fmt_id, label, expr in QUALITY_PRESETS:
        if fmt_id == "audio":
            if has_audio or max_height:
                result.append({"id": fmt_id, "label": label, "format": expr})
            continue
        if fmt_id == "best":
            if max_height or formats:
                result.append({"id": fmt_id, "label": label, "format": expr})
            continue
        if max_height and limit is not None and limit <= max_height:
            result.append({"id": fmt_id, "label": label, "format": expr})
    if not result:
        result.append({"id": "best", "label": "默认", "format": "bv*+ba/b"})
    return result


def _playlist_payload(info: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for raw in info.get("entries") or []:
        if not raw:
            continue
        url = _entry_url(raw)
        if not url:
            continue
        entries.append(
            {
                "id": raw.get("id"),
                "title": raw.get("title") or "未命名",
                "url": url,
                "thumbnail": to_proxy_url(_pick_thumbnail(raw)),
                "duration": raw.get("duration"),
            }
        )
        if len(entries) >= 50:
            break
    return {
        "ok": True,
        "type": "playlist",
        "title": info.get("title") or "播放列表",
        "thumbnail": to_proxy_url(_pick_thumbnail(info)),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "extractor": info.get("extractor"),
        "entries": entries,
    }


def _share_video_payload(media: DouyinShareMedia) -> dict[str, Any]:
    if not media.play_url:
        raise YtdlpError("parse_failed", "这是图文笔记或未公开视频，第一期只支持可播放的抖音视频")
    label = f"{media.height}p" if media.height else "最佳画质"
    formats = [{"id": "best", "label": label, "format": "best"}]
    if media.music_url:
        formats.append({"id": "audio", "label": "仅音频", "format": "bestaudio/best"})
    return {
        "ok": True,
        "type": "video",
        "title": media.title or "未命名视频",
        "thumbnail": to_proxy_url(media.thumbnail),
        "duration": media.duration,
        "uploader": media.uploader,
        "webpage_url": media.webpage_url,
        "extractor": "douyin_share",
        "has_subtitles": False,
        "formats": formats,
    }


def _fetch_douyin_share(url: str) -> DouyinShareMedia | None:
    if not is_douyin_url(url):
        return None
    aweme_id = resolve_aweme_id(url)
    if not aweme_id:
        return None
    return fetch_share_media(aweme_id)


def _video_payload(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "type": "video",
        "title": info.get("title") or "未命名视频",
        "thumbnail": to_proxy_url(_pick_thumbnail(info)),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": info.get("webpage_url") or info.get("original_url"),
        "extractor": info.get("extractor"),
        "has_subtitles": _has_captions(info),
        "formats": simplify_formats(info),
    }


def parse_url(url: str) -> dict[str, Any]:
    if not is_http_url(url):
        raise YtdlpError("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    url = canonicalize_url(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("douyin.com") and parsed.path.lower().startswith("/search"):
        raise YtdlpError(
            "unsupported",
            "这是抖音搜索页，不是视频链接。请点开具体视频后再复制地址（带 /video/ 或 modal_id）。",
        )

    share_error: DouyinShareError | None = None
    try:
        media = _fetch_douyin_share(url)
    except DouyinShareError as exc:
        share_error = exc
        media = None
    if media:
        return _share_video_payload(media)

    opts = base_opts(
        url,
        {
            "skip_download": True,
            "extract_flat": "in_playlist",
        },
    )
    try:
        info = run_ydl(url, opts, download=False)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持解析") from exc
    except (DownloadError, ExtractorError, Exception) as exc:  # noqa: BLE001
        if share_error:
            raise YtdlpError("parse_failed", share_error.message) from exc
        raise YtdlpError("parse_failed", _friendly_error(exc)) from exc

    if not info:
        raise YtdlpError("parse_failed", "没有解析到视频信息")

    if info.get("_type") == "playlist":
        payload = _playlist_payload(info)
        if not payload["entries"]:
            raise YtdlpError("parse_failed", "播放列表是空的，或条目无法打开")
        return payload
    return _video_payload(info)


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\s+", " ", text)
    if "412" in text or "Precondition Failed" in text:
        return "平台拦截了这次解析（HTTP 412）。请确认链接可在浏览器打开后重试。"
    if _is_cookie_lock(exc):
        return (
            "读不了 Chrome/Edge 的 Cookie：浏览器正在运行并锁住了文件。"
            "请完全退出 Chrome 和 Edge（系统托盘里也不能留进程），用其中一个打开一次抖音，再回到这里解析。"
        )
    if _needs_fresh_cookies(exc):
        return (
            "抖音需要浏览器访问记录（不必登录）。"
            "请用 Chrome 或 Edge 打开一次抖音，完全退出该浏览器后再解析。"
        )
    if len(text) > 240:
        text = text[:237] + "..."
    return text


def _safe_filename(title: str, suffix: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", title or "video").strip(" .") or "video"
    name = name[:80]
    ext = suffix.lstrip(".") or "mp4"
    return f"{name}.{ext}"


def _find_output(dest: Path) -> Path | None:
    files = [
        path
        for path in dest.iterdir()
        if path.is_file() and path.suffix not in {".part", ".ytdl", ".json"}
        and " frag" not in path.name
        and not path.name.endswith(".part")
    ]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_size)


def _progress_hook(on_progress: ProgressCb | None):
    def hook(event: dict[str, Any]) -> None:
        if not on_progress:
            return
        status = event.get("status")
        if status == "downloading":
            total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
            downloaded = event.get("downloaded_bytes") or 0
            percent = int(downloaded * 100 / total) if total else 0
            percent = max(0, min(percent, 99))
            on_progress(percent, "downloading")
        elif status == "finished":
            on_progress(99, "processing")

    return hook


def _download_direct(
    media_url: str,
    title: str,
    dest: Path,
    outtmpl: str,
    on_progress: ProgressCb | None,
) -> tuple[Path, str]:
    opts = base_opts(
        media_url,
        {
            "noplaylist": True,
            "format": "best",
            "outtmpl": outtmpl,
            "progress_hooks": [_progress_hook(on_progress)],
            "noprogress": True,
            "restrictfilenames": False,
            "http_headers": dict(SHARE_HEADERS),
        },
    )
    try:
        info = run_ydl(media_url, opts, download=True)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持下载") from exc
    except (DownloadError, ExtractorError) as exc:
        raise YtdlpError("download_failed", _friendly_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("download_failed", _friendly_error(exc)) from exc
    output = _find_output(dest)
    if output is None:
        raise YtdlpError("download_failed", "下载完成但没有找到文件")
    filename = _safe_filename((info or {}).get("title") or title or "video", output.suffix)
    return output, filename


def download_url(
    url: str,
    format_expr: str,
    dest: Path,
    on_progress: ProgressCb | None = None,
) -> tuple[Path, str]:
    if not is_http_url(url):
        raise YtdlpError("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    url = canonicalize_url(url)
    if "+" in (format_expr or "") and not ffmpeg_available():
        raise YtdlpError("ffmpeg_missing", "合并画质需要本机安装 ffmpeg 并加入 PATH")

    dest.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest / "media.%(ext)s")
    audio_only = (
        "bestaudio" in (format_expr or "")
        and "bestvideo" not in (format_expr or "")
        and "bv" not in (format_expr or "")
    )

    try:
        media = _fetch_douyin_share(url)
    except DouyinShareError as exc:
        media = None
        share_error = exc
    else:
        share_error = None
    if media:
        target = media.music_url if audio_only and media.music_url else media.play_url
        if audio_only and not media.music_url:
            raise YtdlpError("download_failed", "该抖音视频没有可单独保存的音频")
        if not target:
            raise YtdlpError("download_failed", share_error.message if share_error else "没有可用的抖音下载地址")
        return _download_direct(target, media.title, dest, outtmpl, on_progress)

    hook = _progress_hook(on_progress)
    opts = base_opts(
        url,
        {
            "noplaylist": True,
            "format": format_expr or "bv*+ba/b",
            "outtmpl": outtmpl,
            "progress_hooks": [hook],
            "noprogress": True,
            "restrictfilenames": False,
        },
    )
    if not audio_only:
        opts["merge_output_format"] = "mp4"
    try:
        info = run_ydl(url, opts, download=True)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持下载") from exc
    except (DownloadError, ExtractorError) as exc:
        raise YtdlpError("download_failed", _friendly_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("download_failed", _friendly_error(exc)) from exc

    output = _find_output(dest)
    if output is None:
        raise YtdlpError("download_failed", "下载完成但没有找到文件")
    title = (info or {}).get("title") or "video"
    filename = _safe_filename(title, output.suffix)
    return output, filename


_TAG_RE = re.compile(r"<[^>]+>")


def vtt_to_text(content: str) -> str:
    lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")):
            continue
        if "-->" in line:
            continue
        if line.isdigit():
            continue
        line = _TAG_RE.sub("", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return "\n".join(lines)


def fetch_subtitles(url: str, dest: Path) -> str:
    if not is_http_url(url):
        raise YtdlpError("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    url = canonicalize_url(url)

    dest.mkdir(parents=True, exist_ok=True)
    opts = base_opts(
        url,
        {
            "skip_download": True,
            "noplaylist": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en", "en-US"],
            "subtitlesformat": "vtt",
            "outtmpl": str(dest / "subs"),
        },
    )
    try:
        run_ydl(url, opts, download=True)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持") from exc
    except (DownloadError, ExtractorError) as exc:
        raise YtdlpError("no_subtitles", _friendly_error(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("no_subtitles", _friendly_error(exc)) from exc

    vtts = list(dest.rglob("*.vtt"))
    if not vtts:
        raise YtdlpError("no_subtitles", "该视频没有字幕，无法总结/翻译")

    def score(path: Path) -> int:
        name = path.name.lower()
        if "zh" in name:
            return 0
        if ".en" in name:
            return 1
        return 2

    for path in sorted(vtts, key=score):
        try:
            text = vtt_to_text(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if text:
            return text
    raise YtdlpError("no_subtitles", "该视频没有字幕，无法总结/翻译")
