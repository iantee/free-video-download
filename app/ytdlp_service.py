"""Thin yt-dlp wrapper. Do not fork or patch yt-dlp internals."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
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


_COOKIEFILE_CACHE: tuple[str, str] | None = None


def _copy_win_shared(src: str, dst: str) -> None:
    """Copy a file Chrome still has open. shutil.copy hits PermissionError on Windows."""
    import ctypes
    from ctypes import wintypes

    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateFileW(
        src,
        generic_read,
        share_all,
        None,
        open_existing,
        0x80,
        None,
    )
    if handle == wintypes.HANDLE(-1).value or handle in (0, 0xFFFFFFFF):
        raise PermissionError(13, "Could not open cookie database", src)
    try:
        size = wintypes.LARGE_INTEGER()
        if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            raise PermissionError(13, "Could not read cookie database size", src)
        remaining = int(size.QuadPart)
        buf = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        with open(dst, "wb") as out:
            while remaining > 0:
                chunk = min(len(buf), remaining)
                if not kernel32.ReadFile(handle, buf, chunk, ctypes.byref(read), None) or read.value == 0:
                    break
                out.write(buf.raw[: read.value])
                remaining -= read.value
    finally:
        kernel32.CloseHandle(handle)


def _with_unlock_copy(func: Callable[[], Any]) -> Any:
    """If Chrome locks the SQLite file, retry the copy with Windows share flags."""
    import yt_dlp.cookies as cookies_mod

    original = cookies_mod.shutil.copy

    def copy_or_share(src, dst, *args, **kwargs):
        try:
            return original(src, dst, *args, **kwargs)
        except PermissionError:
            if os.name != "nt":
                raise
            _copy_win_shared(str(src), str(dst))
            return dst

    cookies_mod.shutil.copy = copy_or_share
    try:
        return func()
    finally:
        cookies_mod.shutil.copy = original


def _try_browser_cookiefile(spec: str) -> str | None:
    """Export browser cookies to a Netscape file so later calls do not re-lock Chrome."""
    global _COOKIEFILE_CACHE
    if _COOKIEFILE_CACHE and _COOKIEFILE_CACHE[0] == spec and Path(_COOKIEFILE_CACHE[1]).is_file():
        return _COOKIEFILE_CACHE[1]
    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        browser, *rest = _browser_tuple(spec)
        profile = rest[0] if rest else None
        jar = _with_unlock_copy(lambda: extract_cookies_from_browser(browser, profile))
    except Exception:
        return None
    if jar is None or len(jar) == 0:
        return None
    dest = Path(tempfile.gettempdir()) / "miaocun-browser.cookies.txt"
    try:
        jar.save(str(dest))
    except OSError:
        return None
    _COOKIEFILE_CACHE = (spec, str(dest))
    return str(dest)


def _cookie_opt_variants(url: str) -> list[dict[str, Any]]:
    """Cookie sources to try. Always end with no-cookies so a locked Chrome cannot block parse."""
    variants: list[dict[str, Any]] = []
    cookiefile = os.getenv("COOKIES_FILE", "").strip()
    if cookiefile:
        variants.append({"cookiefile": cookiefile})
        variants.append({})
        return variants

    specified = os.getenv("COOKIES_FROM_BROWSER", "").strip()
    if not specified:
        return [{}]

    exported = _try_browser_cookiefile(specified)
    if exported:
        variants.append({"cookiefile": exported})
    else:
        seen = {_browser_tuple(specified)[0]}
        for spec in ("edge", "firefox", "chrome"):
            if spec in seen:
                continue
            seen.add(spec)
            variants.append({"cookiesfrombrowser": (spec,)})
    variants.append({})
    return variants


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


def _is_optional_cookie_failure(exc: Exception) -> bool:
    """Cookie source is optional; missing Firefox profile etc. should fall through."""
    if _is_cookie_lock(exc) or _needs_fresh_cookies(exc):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "could not find firefox cookies",
            "could not find safari cookies",
            "unknown browser",
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


_MEDIA_SUFFIXES = {
    ".m4a",
    ".mp3",
    ".mp4",
    ".webm",
    ".mkv",
    ".opus",
    ".ogg",
    ".aac",
    ".wav",
    ".flv",
    ".m4v",
}


def _finished_media(opts: dict[str, Any]) -> Path | None:
    """A finished media file already on disk. Cookie cleanup must not throw it away."""
    outtmpl = opts.get("outtmpl")
    if not isinstance(outtmpl, str) or not outtmpl:
        return None
    dest = Path(outtmpl).parent
    if not dest.is_dir():
        return None
    files = []
    for path in dest.iterdir():
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".part" in name or name.endswith(".ytdl") or " frag" in name:
            continue
        if path.suffix.lower() not in _MEDIA_SUFFIXES or path.stat().st_size <= 0:
            continue
        files.append(path)
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_size)


def run_ydl(
    url: str,
    opts: dict[str, Any],
    *,
    download: bool,
    allow_cookies: bool = True,
) -> dict[str, Any]:
    """Try configured cookies, then the same call with no cookies.

    A locked Chrome must not replace a later logged-out result, and closing
    YoutubeDL must not discard an extract that already succeeded.
    """
    last: Exception | None = None
    saw_cookie_lock = False
    variants = _cookie_opt_variants(url) if allow_cookies else [{}]
    bili_attempts = 3 if _is_bilibili(url) else 1
    for index, cookie_opts in enumerate(variants):
        attempt_opts = dict(opts)
        attempt_opts.pop("cookiefile", None)
        attempt_opts.pop("cookiesfrombrowser", None)
        attempt_opts.update(cookie_opts)
        more = index + 1 < len(variants)
        ydl = yt_dlp.YoutubeDL(attempt_opts)
        try:
            info = None
            variant_error: Exception | None = None
            for attempt in range(bili_attempts):
                try:
                    info = ydl.extract_info(url.strip(), download=download)
                    variant_error = None
                    break
                except UnsupportedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    variant_error = exc
                    last = exc
                    if _is_cookie_lock(exc):
                        saw_cookie_lock = True
                        break
                    if attempt + 1 < bili_attempts and _is_precondition_failed(exc):
                        time.sleep(0.7 * (attempt + 1))
                        continue
                    break
            if info is not None:
                data = ydl.sanitize_info(info) or {}
                if saw_cookie_lock and not cookie_opts:
                    data["_cookies_skipped"] = True
                return data
            if variant_error is not None and _is_cookie_lock(variant_error) and _finished_media(attempt_opts):
                return {"_cookies_skipped": True}
            if more and cookie_opts:
                continue
            raise variant_error or last or RuntimeError("yt-dlp failed")
        finally:
            try:
                ydl.close()
            except Exception:
                pass
    raise last or RuntimeError("yt-dlp failed")


def _caption_langs(mapping: Any) -> list[str]:
    if not isinstance(mapping, dict):
        return []
    return [str(lang) for lang in mapping if str(lang).lower() != "danmaku"]


def _has_captions(info: dict[str, Any]) -> bool:
    return bool(_caption_langs(info.get("subtitles"))) or bool(_caption_langs(info.get("automatic_captions")))


def _no_subtitles_message(url: str) -> str:
    if _is_bilibili(url):
        return (
            "该视频没有可提取的字幕。B 站画面上的字经常是硬字幕（烧进画面）；"
            "CC / AI 字幕需要登录后才能拉取，请在 .env 填写 COOKIES_FROM_BROWSER 或 COOKIES_FILE 后重启。"
        )
    return "该视频没有字幕，无法总结/翻译"


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
    skipped = bool(info.pop("_cookies_skipped", False))
    webpage = info.get("webpage_url") or info.get("original_url") or ""
    payload = {
        "ok": True,
        "type": "video",
        "title": info.get("title") or "未命名视频",
        "thumbnail": to_proxy_url(_pick_thumbnail(info)),
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "webpage_url": webpage,
        "extractor": info.get("extractor"),
        "has_subtitles": _has_captions(info),
        "formats": simplify_formats(info),
    }
    if skipped and _is_bilibili(str(webpage)):
        payload["cookie_warning"] = (
            "浏览器 Cookie 被锁住，已改为未登录解析。B 站视频可以看；"
            "CC 字幕可能拉不到。请完全退出浏览器后再试，退出后不要立刻重新打开。"
        )
    return payload


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

    parse_extra: dict[str, Any] = {
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    if _is_bilibili(url):
        parse_extra["listsubtitles"] = True
    opts = base_opts(url, parse_extra)
    try:
        info = run_ydl(url, opts, download=False)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持解析") from exc
    except (DownloadError, ExtractorError, Exception) as exc:  # noqa: BLE001
        if share_error:
            raise YtdlpError("parse_failed", share_error.message) from exc
        raise YtdlpError("parse_failed", _friendly_error(exc, url)) from exc

    if not info:
        raise YtdlpError("parse_failed", "没有解析到视频信息")

    if info.get("_type") == "playlist":
        payload = _playlist_payload(info)
        if not payload["entries"]:
            raise YtdlpError("parse_failed", "播放列表是空的，或条目无法打开")
        return payload
    return _video_payload(info)


def _friendly_error(exc: Exception, url: str = "") -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\s+", " ", text)
    if "412" in text or "Precondition Failed" in text:
        return "平台拦截了这次解析（HTTP 412）。请确认链接可在浏览器打开后重试。"
    if _is_cookie_lock(exc):
        if _is_bilibili(url):
            return (
                "读不了浏览器 Cookie：Chrome/Edge 正在运行并锁住了文件。"
                "B 站视频不用关浏览器也能解析；若要拉 CC 字幕，请完全退出浏览器后再试（退出后不要立刻重新打开），"
                "或把 Cookie 导出成 Netscape 文件后填写 COOKIES_FILE。"
            )
        if _is_douyin(url):
            return (
                "读不了浏览器 Cookie：Chrome/Edge 正在运行并锁住了文件。"
                "抖音公开视频一般不需要 Cookie；若仍失败，请换一条能打开的视频链接。"
            )
        return (
            "读不了浏览器 Cookie：Chrome/Edge 正在运行并锁住了文件。"
            "请完全退出该浏览器后再试，退出后不要立刻重新打开；或改用 COOKIES_FILE。"
        )
    if _needs_fresh_cookies(exc):
        if _is_douyin(url):
            return "抖音公开视频一般不需要登录。请换一条能在浏览器打开的视频链接后重试。"
        return "平台要求更新登录状态。请重新打开对应网站后再试，或改用 COOKIES_FILE。"
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
        raise YtdlpError("download_failed", _friendly_error(exc, media_url)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("download_failed", _friendly_error(exc, media_url)) from exc
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
    *,
    allow_cookies: bool = True,
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
        info = run_ydl(url, opts, download=True, allow_cookies=allow_cookies)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持下载") from exc
    except (DownloadError, ExtractorError) as exc:
        raise YtdlpError("download_failed", _friendly_error(exc, url)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("download_failed", _friendly_error(exc, url)) from exc

    output = _find_output(dest)
    if output is None:
        raise YtdlpError("download_failed", "下载完成但没有找到文件")
    title = (info or {}).get("title") or "video"
    filename = _safe_filename(title, output.suffix)
    return output, filename


_TAG_RE = re.compile(r"<[^>]+>")


def _parse_vtt_stamp(stamp: str) -> float:
    raw = (stamp or "").strip().replace(",", ".")
    if not raw:
        return 0.0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, rest = parts
            seconds, _, frac = rest.partition(".")
            millis = int((frac + "000")[:3]) if frac else 0
            return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + millis / 1000
        if len(parts) == 2:
            minutes, rest = parts
            seconds, _, frac = rest.partition(".")
            millis = int((frac + "000")[:3]) if frac else 0
            return int(minutes) * 60 + int(seconds) + millis / 1000
    except ValueError:
        return 0.0
    return 0.0


def format_cue_label(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def vtt_to_cues(content: str) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    current_start: float | None = None
    current_end: float | None = None
    texts: list[str] = []

    def flush() -> None:
        nonlocal current_start, current_end, texts
        if current_start is None:
            texts = []
            current_end = None
            return
        joined = " ".join(texts).strip()
        end = current_end if current_end and current_end > current_start else None
        if joined and cues and cues[-1]["text"] == joined:
            prev_end = cues[-1].get("end")
            if end and (prev_end is None or end > prev_end):
                cues[-1]["end"] = round(end, 3)
        elif joined:
            item: dict[str, Any] = {
                "start": round(current_start, 3),
                "label": format_cue_label(current_start),
                "text": joined,
            }
            if end:
                item["end"] = round(end, 3)
            cues.append(item)
        current_start = None
        current_end = None
        texts = []

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:", "STYLE")):
            continue
        if "-->" in line:
            flush()
            left, right = line.split("-->", 1)
            start_raw = left.strip().split()[0]
            end_raw = right.strip().split()[0] if right.strip() else ""
            current_start = _parse_vtt_stamp(start_raw)
            current_end = _parse_vtt_stamp(end_raw) if end_raw else None
            continue
        if current_start is None:
            continue
        if line.isdigit():
            continue
        cleaned = _TAG_RE.sub("", line).strip()
        if cleaned:
            texts.append(cleaned)
    flush()
    return cues


def vtt_to_text(content: str) -> str:
    return cues_to_plain_text(vtt_to_cues(content))


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def cues_to_srt(cues: list[dict[str, Any]]) -> str:
    """SRT with real end times when we have them. Missing ends stop at the next cue."""
    usable = [cue for cue in cues if str(cue.get("text") or "").strip()]
    blocks: list[str] = []
    for index, cue in enumerate(usable):
        start = float(cue.get("start") or 0)
        end_raw = cue.get("end")
        if end_raw is not None and float(end_raw) > start:
            end = float(end_raw)
        else:
            nxt = usable[index + 1] if index + 1 < len(usable) else None
            if nxt is not None and float(nxt.get("start") or 0) - start > 0.2:
                gap_end = float(nxt.get("start") or 0)
                end = gap_end if gap_end - start <= 12 else start + 4
            else:
                end = start + 4
        if end <= start:
            end = start + 1
        text = str(cue.get("text") or "").strip()
        blocks.append(f"{index + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}")
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def cues_to_plain_text(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for cue in cues:
        text = str(cue.get("text") or "").strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)
    return "\n".join(lines)


def fetch_subtitle_cues(url: str, dest: Path) -> list[dict[str, Any]]:
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
            "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en", "en-US", "all"],
            "subtitlesformat": "vtt",
            "outtmpl": str(dest / "subs"),
        },
    )
    try:
        run_ydl(url, opts, download=True)
    except UnsupportedError as exc:
        raise YtdlpError("unsupported", "这个链接暂时不支持") from exc
    except (DownloadError, ExtractorError) as exc:
        raise YtdlpError("no_subtitles", _friendly_error(exc, url)) from exc
    except Exception as exc:  # noqa: BLE001
        raise YtdlpError("no_subtitles", _friendly_error(exc, url)) from exc

    vtts = list(dest.rglob("*.vtt"))
    if not vtts:
        raise YtdlpError("no_subtitles", _no_subtitles_message(url))

    def score(path: Path) -> int:
        name = path.name.lower()
        if "zh" in name:
            return 0
        if ".en" in name:
            return 1
        return 2

    for path in sorted(vtts, key=score):
        try:
            cues = vtt_to_cues(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        if cues:
            return cues
    raise YtdlpError("no_subtitles", _no_subtitles_message(url))


def fetch_subtitles(url: str, dest: Path) -> str:
    text = cues_to_plain_text(fetch_subtitle_cues(url, dest))
    if not text:
        raise YtdlpError("no_subtitles", _no_subtitles_message(url))
    return text
