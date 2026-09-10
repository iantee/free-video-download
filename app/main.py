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

from app import ai_service, jobs, thumbs
from app.ytdlp_service import (
    YtdlpError,
    canonicalize_url,
    download_url,
    fetch_subtitles,
    ffmpeg_available,
    is_http_url,
    parse_url,
)

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


async def _subtitle_then_llm(url: str, action: str, target_lang: str = "zh") -> JSONResponse:
    if not is_http_url(url):
        return error_response("invalid_url", "请粘贴以 http 或 https 开头的视频链接")
    if not ai_service.llm_configured():
        return error_response(
            "llm_not_configured",
            "还没有配置 LLM_API_KEY，请复制 .env.example 为 .env 后填写",
        )

    job = jobs.create_job()
    try:
        text = await run_in_threadpool(fetch_subtitles, url, job["dir"])
        if action == "summary":
            result = await run_in_threadpool(ai_service.summarize, text)
        else:
            result = await run_in_threadpool(ai_service.translate, text, target_lang)
        return JSONResponse({"ok": True, "text": result})
    except YtdlpError as exc:
        return error_response(exc.code, exc.message)
    except ai_service.LlmError as exc:
        return error_response(exc.code, exc.message)
    finally:
        jobs.schedule_cleanup(job["id"], delay=1)


@app.post("/api/summary")
async def api_summary(payload: dict) -> JSONResponse:
    return await _subtitle_then_llm(read_url(payload), "summary")


@app.post("/api/translate")
async def api_translate(payload: dict) -> JSONResponse:
    target = str(payload.get("target_lang") or "zh").strip().lower()
    if target not in {"zh", "en"}:
        target = "zh"
    return await _subtitle_then_llm(read_url(payload), "translate", target)
