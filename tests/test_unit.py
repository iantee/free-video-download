from app.thumbs import is_safe_image_url, normalize_image_url, to_proxy_url
from app.ytdlp_service import base_opts, canonicalize_url, is_http_url, simplify_formats, vtt_to_text


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


