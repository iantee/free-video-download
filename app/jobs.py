"""In-memory download jobs and temp files. No database in phase 1."""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent / "downloads"
MAX_AGE_SECONDS = 30 * 60

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def ensure_root() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)


def create_job() -> dict[str, Any]:
    ensure_root()
    job_id = uuid4().hex
    dest = ROOT / job_id
    dest.mkdir(parents=True, exist_ok=True)
    job = {
        "id": job_id,
        "status": "queued",
        "percent": 0,
        "filepath": None,
        "filename": None,
        "error": None,
        "created_at": time.time(),
        "dir": dest,
    }
    with _lock:
        _jobs[job_id] = job
    return job


def update_job(job_id: str, **kwargs: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(kwargs)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def snapshot(job_id: str) -> dict[str, Any] | None:
    return get_job(job_id)


def schedule_cleanup(job_id: str, delay: float = 8.0) -> None:
    def _run() -> None:
        time.sleep(delay)
        with _lock:
            job = _jobs.pop(job_id, None)
        if job:
            shutil.rmtree(job["dir"], ignore_errors=True)

    threading.Thread(target=_run, daemon=True).start()


def sweep_old(max_age: int = MAX_AGE_SECONDS) -> None:
    now = time.time()
    with _lock:
        stale_ids = [
            job_id
            for job_id, job in _jobs.items()
            if now - job.get("created_at", now) > max_age
        ]
    for job_id in stale_ids:
        with _lock:
            job = _jobs.pop(job_id, None)
        if job:
            shutil.rmtree(job["dir"], ignore_errors=True)

    if ROOT.exists():
        known_dirs = set()
        with _lock:
            known_dirs = {str(job["dir"]) for job in _jobs.values()}
        for path in ROOT.iterdir():
            if not path.is_dir():
                continue
            if str(path) in known_dirs:
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age > max_age:
                shutil.rmtree(path, ignore_errors=True)
