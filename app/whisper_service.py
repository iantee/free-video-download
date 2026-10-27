"""Local speech-to-text fallback. Only used when a video has no extractable subtitles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.ytdlp_service import format_cue_label

class WhisperError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_model: Any = None
_model_name: str | None = None


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def _segment_value(segment: Any, name: str, default: Any = None) -> Any:
    if isinstance(segment, dict):
        return segment.get(name, default)
    return getattr(segment, name, default)


def segments_to_cues(segments: list[Any]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    for segment in segments:
        text = str(_segment_value(segment, "text", "") or "").strip()
        start = float(_segment_value(segment, "start", 0) or 0)
        end_raw = _segment_value(segment, "end", None)
        end = float(end_raw) if end_raw is not None else None
        if not text:
            continue
        if cues and cues[-1]["text"] == text:
            if end is not None and end > float(cues[-1].get("end") or 0):
                cues[-1]["end"] = round(end, 3)
            continue
        item: dict[str, Any] = {
            "start": round(start, 3),
            "label": format_cue_label(start),
            "text": text,
        }
        if end is not None and end > start:
            item["end"] = round(end, 3)
        cues.append(item)
    return cues


def transcribe(audio_path: Path) -> list[dict[str, Any]]:
    global _model, _model_name
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise WhisperError(
            "whisper_missing",
            "还没有安装 faster-whisper，无法对没有字幕的视频做语音转写",
        ) from exc

    model_name = os.getenv("WHISPER_MODEL", "base").strip() or "base"
    language = os.getenv("WHISPER_LANGUAGE", "").strip() or None
    # Xet downloads 401 against the public mirror. Regular HTTPS is enough for these weights.
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if _model is None or _model_name != model_name:
        try:
            _model = WhisperModel(model_name, device="cpu", compute_type="int8")
            _model_name = model_name
        except Exception as exc:  # noqa: BLE001
            raise WhisperError("whisper_failed", f"语音模型加载失败：{exc}") from exc

    try:
        segments, _info = _model.transcribe(str(audio_path), language=language, vad_filter=True)
        cues = segments_to_cues(list(segments))
    except WhisperError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WhisperError("whisper_failed", f"语音转写失败：{exc}") from exc

    if not cues:
        raise WhisperError("whisper_failed", "语音转写没有得到可用文本")
    return cues
