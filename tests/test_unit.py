from app.ai_service import LlmError, notes_to_markdown, parse_notes_payload, trim_cues
from app.whisper_service import segments_to_cues
from app.thumbs import is_safe_image_url, normalize_image_url, to_proxy_url
from yt_dlp.utils import DownloadError

from app.ytdlp_service import (
    _cookie_opt_variants,
    _friendly_error,
    _has_captions,
    _is_optional_cookie_failure,
    _no_subtitles_message,
    base_opts,
    canonicalize_url,
    cues_to_srt,
    format_cue_label,
    is_http_url,
    run_ydl,
    simplify_formats,
    vtt_to_cues,
    vtt_to_text,
)


def test_is_http_url():
    assert is_http_url("https://www.youtube.com/watch?v=1")
    assert is_http_url("http://example.com/a")
    assert not is_http_url("")
    assert not is_http_url("javascript:alert(1)")
    assert not is_http_url("ftp://x")


def test_canonicalize_douyin_search_modal():
    url = (
        "https://www.douyin.com/search/%E5%86%AC%E7%9C%A0"
        "?aid=14b165fa-abe2-4115-a878-2bcff0a8a483"
        "&modal_id=7528589239626353970&type=general"
        "&ug_source=jingmei_sem_baidu_dyweb_02_pinpai"
    )
    assert canonicalize_url(url) == "https://www.douyin.com/video/7528589239626353970"
    assert canonicalize_url(url.replace("&", "&amp;")) == "https://www.douyin.com/video/7528589239626353970"
    assert canonicalize_url("https://www.youtube.com/watch?v=1").startswith("https://www.youtube.com")
    assert canonicalize_url("https://www.douyin.com/search/foo").endswith("/search/foo")


def test_vtt_to_cues_keeps_timestamps():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:01.000
Hello

00:01:12.500 --> 00:01:14.000
Hello
<c>World</c>
"""
    cues = vtt_to_cues(vtt)
    assert cues[0]["label"] == "00:00"
    assert cues[0]["text"] == "Hello"
    assert cues[0]["end"] == 1
    assert cues[1]["label"] == "01:12"
    assert cues[1]["end"] == 74
    assert "World" in cues[1]["text"]
    assert format_cue_label(3723) == "1:02:03"


def test_cues_to_srt_keeps_real_ends_and_caps_long_gaps():
    timed = cues_to_srt(
        [
            {"start": 0, "end": 1.5, "text": "你好"},
            {"start": 65, "text": "下一句"},
        ]
    )
    assert timed.startswith("1\n00:00:00,000 --> 00:00:01,500\n你好\n")
    assert "2\n00:01:05,000 --> 00:01:09,000\n下一句\n" in timed

    gapped = cues_to_srt(
        [
            {"start": 0, "text": "第一句"},
            {"start": 30, "text": "很后面"},
        ]
    )
    assert "00:00:00,000 --> 00:00:04,000" in gapped
    assert "00:00:30,000 --> 00:00:34,000" in gapped
    assert cues_to_srt([{"start": 0, "text": "   "}]) == ""


def test_parse_notes_payload_from_fenced_json():
    raw = """```json
{"overview": "这是概述", "highlights": ["要点A", "要点B"], "chapters": [{"start": "00:10", "title": "开场", "summary": "先讲背景"}]}
```"""
    notes = parse_notes_payload(raw)
    assert notes["overview"] == "这是概述"
    assert notes["highlights"] == ["要点A", "要点B"]
    assert notes["chapters"][0]["start"] == "00:10"
    markdown = notes_to_markdown("测试视频", notes)
    assert "# 测试视频" in markdown
    assert "要点A" in markdown
    assert "00:10" in markdown


def test_parse_notes_payload_rejects_garbage():
    try:
        parse_notes_payload("这不是 JSON")
        assert False, "should have failed"
    except LlmError as exc:
        assert exc.code == "llm_failed"


def test_trim_cues_caps_length():
    cues = [{"start": i, "label": "00:00", "text": "字" * 20} for i in range(50)]
    kept, truncated = trim_cues(cues, max_chars=80)
    assert truncated
    assert kept
    assert len(kept) < 50


def test_vtt_to_text_strips_timestamps():
    vtt = """WEBVTT

00:00:00.000 --> 00:00:01.000
Hello

00:00:01.000 --> 00:00:02.000
Hello
<c>World</c>
"""
    text = vtt_to_text(vtt)
    assert "WEBVTT" not in text
    assert "-->" not in text
    assert "Hello" in text
    assert "World" in text


def test_simplify_formats_buckets():
    info = {
        "formats": [
            {"height": 360, "vcodec": "avc1", "acodec": "none"},
            {"height": 720, "vcodec": "avc1", "acodec": "none"},
            {"height": 1080, "vcodec": "avc1", "acodec": "none"},
            {"vcodec": "none", "acodec": "mp4a"},
        ]
    }
    labels = [item["id"] for item in simplify_formats(info)]
    assert labels == ["360", "720", "1080", "best", "audio"]


def test_simplify_formats_low_only():
    info = {"formats": [{"height": 360, "vcodec": "avc1", "acodec": "mp4a"}]}
    labels = [item["id"] for item in simplify_formats(info)]
    assert "360" in labels
    assert "1080" not in labels
    assert "best" in labels


def test_whisper_segments_become_timestamped_cues():
    cues = segments_to_cues(
        [
            {"start": 0, "text": " 欢迎来到这节课 "},
            {"start": 8.2, "text": "欢迎来到这节课"},
            {"start": 65, "end": 80, "text": "光合作用需要阳光"},
            {"start": 90, "text": "   "},
        ]
    )
    assert [item["text"] for item in cues] == ["欢迎来到这节课", "光合作用需要阳光"]
    assert cues[0]["label"] == "00:00"
    assert cues[1]["label"] == "01:05"
    assert cues[1]["end"] == 80


def test_has_captions_ignores_bilibili_danmaku():
    assert not _has_captions({"subtitles": {"danmaku": [{"ext": "xml"}]}})
    assert _has_captions({"subtitles": {"zh-Hans": [{"ext": "vtt"}]}})
    assert _has_captions({"automatic_captions": {"en": [{"ext": "vtt"}]}})


def test_bilibili_no_subtitles_explains_hardsubs_and_cookies():
    message = _no_subtitles_message("https://www.bilibili.com/video/BV1wDhj6wEa8")
    assert "硬字幕" in message
    assert "COOKIES" in message
    assert _no_subtitles_message("https://www.youtube.com/watch?v=1") == "该视频没有字幕，无法总结/翻译"


def test_cookie_lock_message_is_site_aware():
    class LockError(Exception):
        pass

    exc = LockError("Could not copy Chrome cookie database")
    bili = _friendly_error(exc, "https://www.bilibili.com/video/BV1wDhj6wEa8")
    assert "抖音" not in bili
    assert "B 站" in bili
    douyin = _friendly_error(exc, "https://www.douyin.com/video/1")
    assert "抖音" in douyin
    assert "打开一次抖音" not in bili


def test_missing_firefox_cookies_are_optional():
    class Missing(Exception):
        pass

    assert _is_optional_cookie_failure(Missing("could not find firefox cookies database in Profiles"))
    assert not _is_optional_cookie_failure(Missing("HTTP 412 Precondition Failed"))


def test_cookie_variants_always_fall_back(monkeypatch):
    monkeypatch.setenv("COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    monkeypatch.setattr("app.ytdlp_service._try_browser_cookiefile", lambda spec: None)
    variants = _cookie_opt_variants("https://www.bilibili.com/video/BV1wDhj6wEa8")
    assert variants[-1] == {}
    assert any(item.get("cookiesfrombrowser") == ("edge",) for item in variants)


def _patch_cookie_ydl(monkeypatch, anon):
    monkeypatch.setenv("COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    monkeypatch.setattr("app.ytdlp_service._try_browser_cookiefile", lambda spec: None)
    monkeypatch.setattr("app.ytdlp_service.time.sleep", lambda _seconds: None)

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def close(self):
            return None

        def extract_info(self, url, download=False):
            if self.opts.get("cookiesfrombrowser"):
                raise DownloadError("Could not copy Chrome cookie database")
            return anon(self, url, download)

        def sanitize_info(self, info):
            return dict(info)

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)


def test_locked_browser_cookies_fall_through_to_logged_out(monkeypatch):
    _patch_cookie_ydl(monkeypatch, lambda self, url, download: {"id": "x", "title": "ok"})
    data = run_ydl("https://www.bilibili.com/video/BV1wDhj6wEa8", {}, download=False)
    assert data["title"] == "ok"
    assert data["_cookies_skipped"] is True


def test_cookie_lock_does_not_replace_logged_out_error(monkeypatch):
    def anon(self, url, download):
        raise DownloadError("HTTP Error 412: Precondition Failed")

    _patch_cookie_ydl(monkeypatch, anon)
    try:
        run_ydl("https://www.bilibili.com/video/BV1wDhj6wEa8", {}, download=False)
        assert False, "should have raised"
    except DownloadError as exc:
        assert "412" in str(exc)
        assert "cookie" not in str(exc).lower()


def test_cookie_lock_on_close_keeps_successful_extract(monkeypatch):
    monkeypatch.setenv("COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.delenv("COOKIES_FILE", raising=False)
    monkeypatch.setattr("app.ytdlp_service._try_browser_cookiefile", lambda spec: None)

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def close(self):
            raise DownloadError("Could not copy Chrome cookie database")

        def extract_info(self, url, download=False):
            if self.opts.get("cookiesfrombrowser"):
                raise DownloadError("Could not copy Chrome cookie database")
            return {"id": "x", "title": "kept"}

        def sanitize_info(self, info):
            return dict(info)

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYDL)
    data = run_ydl("https://www.bilibili.com/video/BV1wDhj6wEa8", {}, download=False)
    assert data["title"] == "kept"


def test_bilibili_opts_add_origin_referer():
    opts = base_opts("https://www.bilibili.com/video/BV1AJywBWEDn")
    assert opts["http_headers"]["Origin"] == "https://www.bilibili.com"
    assert opts["http_headers"]["Referer"] == "https://www.bilibili.com/"
    assert "Chrome" in opts["http_headers"]["User-Agent"]


def test_youtube_opts_skip_bili_headers():
    opts = base_opts("https://www.youtube.com/watch?v=1")
    assert "http_headers" not in opts


def test_thumbnail_proxy_upgrades_http_and_blocks_ssrf():
    raw = "http://i0.hdslb.com/bfs/archive/abc.jpg"
    assert normalize_image_url(raw).startswith("https://")
    proxied = to_proxy_url(raw)
    assert proxied.startswith("/api/thumb?url=")
    assert "https%3A" in proxied
    assert is_safe_image_url("https://i0.hdslb.com/bfs/archive/abc.jpg")
    assert not is_safe_image_url("http://127.0.0.1/secret")
    assert not is_safe_image_url("http://localhost/x")
    assert not is_safe_image_url("file:///etc/passwd")


def test_subtitles_endpoint_does_not_need_llm(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.main._load_cues",
        lambda url, dest: ([{"start": 0, "end": 1.25, "text": "Hello"}], "whisper"),
    )
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    denied = client.post("/api/subtitles", json={"url": "ftp://example.com/v"})
    assert denied.status_code == 400
    assert denied.json()["error"] == "invalid_url"

    expired = client.post("/api/subtitles", json={"learn_id": "missing"})
    assert expired.json()["error"] == "learn_expired"

    response = client.post("/api/subtitles", json={"url": "https://example.com/v"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "whisper"
    assert "00:00:00,000 --> 00:00:01,250" in body["srt"]
    assert "Hello" in body["srt"]


def test_subtitles_endpoint_reuses_learn_session(monkeypatch):
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    def fail_fetch(url, dest):
        raise AssertionError(url)

    monkeypatch.setattr("app.main._load_cues", fail_fetch)
    job = jobs.create_job()
    jobs.update_job(
        job["id"],
        status="learn",
        source="subtitle",
        cues=[{"start": 1, "end": 2, "text": "你好"}],
    )
    client = TestClient(app)
    response = client.post(
        "/api/subtitles",
        json={"learn_id": job["id"], "url": "https://example.com/v"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "你好" in body["srt"]
    assert "00:00:01,000 --> 00:00:02,000" in body["srt"]
    jobs.schedule_cleanup(job["id"], delay=0.1)


