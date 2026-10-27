"""FastAPI entry: pages + parse/download/AI APIs."""

from __future__ import annotations

import json
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import ai_service, jobs, thumbs, whisper_service
from app.ytdlp_service import (
    YtdlpError,
    canonicalize_url,
    cues_to_plain_text,
    cues_to_srt,
    download_url,
    fetch_subtitle_cues,
    ffmpeg_available,
    is_http_url,
    parse_url,
)
from app.whisper_service import WhisperError

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    jobs.ensure_root()
    jobs.sweep_old()
    yield


app = FastAPI(title="秒存", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def error_response(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code, "message": message}, status_code=status_code)


def read_url(payload: dict) -> str:
    return canonicalize_url(str(payload.get("url") or "").strip())


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "ffmpeg": ffmpeg_available(),
        "llm": ai_service.llm_configured(),
        "whisper": whisper_service.whisper_available(),
    }


@app.post("/api/parse")
async def api_parse(payload: dict) -> JSONResponse:
    url = read_url(payload)
    if not is_http_url(url):
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    try:
        data = await run_in_threadpool(parse_url, url)
    except YtdlpError as exc:
        status = 415 if exc.code == "unsupported" else 400
        return error_response(exc.code, exc.message, status)
    return JSONResponse(data)


@app.get("/api/thumb")
async def api_thumb(url: str):
    try:
        data, content_type = await run_in_threadpool(thumbs.fetch_thumbnail, url)
    except thumbs.ThumbError as exc:
        return error_response(exc.code, exc.message, 400)
    return Response(content=data, media_type=content_type)


@app.post("/api/download")
async def api_download(payload: dict) -> StreamingResponse:
    url = read_url(payload)
    format_expr = str(payload.get("format") or "bv*+ba/b").strip()
    if not is_http_url(url):
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")

    job = jobs.create_job()
    events: queue.Queue[dict | None] = queue.Queue()

    def run() -> None:
        def on_progress(percent: int, status: str) -> None:
            jobs.update_job(job["id"], status=status, percent=percent)
            events.put({"status": status, "percent": percent, "job_id": job["id"]})

        try:
            path, filename = download_url(url, format_expr, job["dir"], on_progress)
            jobs.update_job(
                job["id"],
                status="done",
                percent=100,
                filepath=str(path),
                filename=filename,
            )
            events.put(
                {
                    "status": "done",
                    "percent": 100,
                    "job_id": job["id"],
                    "filename": filename,
                }
            )
        except YtdlpError as exc:
            jobs.update_job(job["id"], status="error", error=exc.message)
            events.put({"status": "error", "error": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            jobs.update_job(job["id"], status="error", error=str(exc))
            events.put({"status": "error", "error": "download_failed", "message": str(exc)})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/file/{job_id}")
async def api_file(job_id: str):
    job = jobs.get_job(job_id)
    if not job or not job.get("filepath"):
        return error_response("not_found", "文件不存在或已经清理", 404)
    path = Path(job["filepath"])
    if not path.is_file():
        return error_response("not_found", "文件不存在或已经清理", 404)

    filename = job.get("filename") or path.name
    quoted = quote(filename)
    jobs.schedule_cleanup(job_id, delay=12)
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"download{path.suffix}\"; filename*=UTF-8''{quoted}"
            )
        },
    )


async def _require_llm() -> JSONResponse | None:
    if not ai_service.llm_configured():
        return error_response(
            "llm_not_configured",
            "还没有配置 LLM_API_KEY，请复制 .env.example 为 .env 后填写",
        )
    return None


def _load_cues(url: str, dest: Path) -> tuple[list, str]:
    """Prefer platform subtitles. Without them, transcribe the audio.

    A locked browser cookie file is not a summary failure: keep going logged-out,
    then Whisper when there is no CC track.
    """
    try:
        return fetch_subtitle_cues(url, dest / "subs"), "subtitle"
    except YtdlpError as exc:
        if exc.code != "no_subtitles" and "读不了浏览器 Cookie" not in exc.message:
            raise
    try:
        audio_path, _filename = download_url(url, "bestaudio/best", dest / "audio")
    except YtdlpError as exc:
        if "读不了浏览器 Cookie" not in exc.message:
            raise
        audio_path, _filename = download_url(
            url, "bestaudio/best", dest / "audio", allow_cookies=False
        )
    return whisper_service.transcribe(audio_path), "whisper"


@app.post("/api/summary")
async def api_summary(payload: dict) -> JSONResponse:
    url = read_url(payload)
    if not is_http_url(url):
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    blocked = await _require_llm()
    if blocked:
        return blocked

    title = str(payload.get("title") or "").strip()
    job = jobs.create_job()
    try:
        cues, source = await run_in_threadpool(_load_cues, url, job["dir"])
        notes = await run_in_threadpool(ai_service.summarize, cues, title)
        jobs.update_job(
            job["id"],
            status="learn",
            cues=cues,
            source=source,
            title=title,
        )
        return JSONResponse(
            {
                "ok": True,
                "learn_id": job["id"],
                "overview": notes["overview"],
                "highlights": notes["highlights"],
                "chapters": notes["chapters"],
                "cues": notes["cues"],
                "markdown": notes["markdown"],
                "source": source,
            }
        )
    except YtdlpError as exc:
        jobs.schedule_cleanup(job["id"], delay=1)
        return error_response(exc.code, exc.message)
    except WhisperError as exc:
        jobs.schedule_cleanup(job["id"], delay=1)
        return error_response(exc.code, exc.message)
    except ai_service.LlmError as exc:
        jobs.schedule_cleanup(job["id"], delay=1)
        return error_response(exc.code, exc.message)


def _subtitle_payload(learn_id: str) -> dict | None:
    if not learn_id:
        return None
    job = jobs.get_job(learn_id)
    cues = (job or {}).get("cues") if job else None
    if not cues:
        return None
    srt = cues_to_srt(cues)
    if not srt.strip():
        return None
    return {
        "ok": True,
        "source": (job or {}).get("source") or "subtitle",
        "srt": srt,
        "cues": cues,
    }


@app.post("/api/subtitles")
async def api_subtitles(payload: dict) -> JSONResponse:
    """Subtitle file for the user to keep. Does not call the LLM."""
    cached = _subtitle_payload(str(payload.get("learn_id") or "").strip())
    if cached:
        return JSONResponse(cached)

    url = read_url(payload)
    if not is_http_url(url):
        if str(payload.get("learn_id") or "").strip():
            return error_response("learn_expired", "学习会话已过期，请重新总结，或再下载一次字幕")
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")

    job = jobs.create_job()
    try:
        cues, source = await run_in_threadpool(_load_cues, url, job["dir"])
        srt = cues_to_srt(cues)
        if not srt.strip():
            return error_response("no_subtitles", "没有可用的字幕文本")
        return JSONResponse({"ok": True, "source": source, "srt": srt, "cues": cues})
    except YtdlpError as exc:
        return error_response(exc.code, exc.message)
    except WhisperError as exc:
        return error_response(exc.code, exc.message)
    finally:
        jobs.schedule_cleanup(job["id"], delay=1)


@app.post("/api/translate")
async def api_translate(payload: dict) -> JSONResponse:
    url = read_url(payload)
    if not is_http_url(url):
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    blocked = await _require_llm()
    if blocked:
        return blocked

    target = str(payload.get("target_lang") or "zh").strip().lower()
    if target not in {"zh", "en"}:
        target = "zh"

    job = jobs.create_job()
    try:
        cues, _source = await run_in_threadpool(_load_cues, url, job["dir"])
        text = cues_to_plain_text(cues)
        if not text:
            return error_response("whisper_failed", "语音转写没有得到可用文本")
        result = await run_in_threadpool(ai_service.translate, text, target)
        return JSONResponse({"ok": True, "text": result})
    except YtdlpError as exc:
        return error_response(exc.code, exc.message)
    except WhisperError as exc:
        return error_response(exc.code, exc.message)
    except ai_service.LlmError as exc:
        return error_response(exc.code, exc.message)
    finally:
        jobs.schedule_cleanup(job["id"], delay=1)


@app.post("/api/ask")
async def api_ask(payload: dict) -> JSONResponse:
    blocked = await _require_llm()
    if blocked:
        return blocked

    learn_id = str(payload.get("learn_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not question:
        return error_response("llm_failed", "请先输入要问的问题")

    job = jobs.get_job(learn_id) if learn_id else None
    cues = (job or {}).get("cues") if job else None
    if not cues:
        return error_response("learn_expired", "学习会话已过期，请重新点一次「AI 总结」")

    try:
        result = await run_in_threadpool(ai_service.ask, cues, question)
        return JSONResponse({"ok": True, "text": result})
    except ai_service.LlmError as exc:
        return error_response(exc.code, exc.message)
