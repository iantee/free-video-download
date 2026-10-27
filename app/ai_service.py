"""OpenAI-compatible LLM calls. Never fake a successful summary."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


class LlmError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


MAX_CHARS = 12000
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def llm_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY", "").strip())


def trim_cues(cues: list[dict[str, Any]], max_chars: int = MAX_CHARS) -> tuple[list[dict[str, Any]], bool]:
    kept: list[dict[str, Any]] = []
    size = 0
    truncated = False
    for cue in cues:
        label = str(cue.get("label") or "00:00")
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        line_len = len(label) + len(text) + 4
        if kept and size + line_len > max_chars:
            truncated = True
            break
        kept.append(
            {
                "start": cue.get("start") or 0,
                "label": label,
                "text": text,
            }
        )
        size += line_len
    return kept, truncated


def cues_to_prompt(cues: list[dict[str, Any]], truncated: bool = False) -> str:
    lines = [f"[{cue['label']}] {cue['text']}" for cue in cues]
    text = "\n".join(lines)
    if truncated:
        text += "\n\n[字幕已截断]"
    return text


def parse_notes_payload(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LlmError("llm_failed", "模型没有返回可用的笔记结构")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LlmError("llm_failed", "模型返回的笔记无法解析") from exc
    if not isinstance(data, dict):
        raise LlmError("llm_failed", "模型没有返回可用的笔记结构")

    overview = str(data.get("overview") or "").strip()
    if not overview:
        raise LlmError("llm_failed", "模型没有返回概述")

    highlights: list[str] = []
    raw_highlights = data.get("highlights") or []
    if not isinstance(raw_highlights, list):
        raw_highlights = []
    for item in raw_highlights:
        value = str(item).strip()
        if value:
            highlights.append(value)
        if len(highlights) >= 8:
            break

    chapters: list[dict[str, str]] = []
    raw_chapters = data.get("chapters") or []
    if not isinstance(raw_chapters, list):
        raw_chapters = []
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        start_label = str(item.get("start") or "").strip() or "00:00"
        if not title and not summary:
            continue
        chapters.append({"start": start_label, "title": title or "章节", "summary": summary})
        if len(chapters) >= 16:
            break

    return {"overview": overview, "highlights": highlights, "chapters": chapters}


def notes_to_markdown(title: str, notes: dict[str, Any]) -> str:
    lines = [f"# {title or '视频学习笔记'}", "", "## 概述", "", notes.get("overview") or "", ""]
    highlights = notes.get("highlights") or []
    if highlights:
        lines.extend(["## 要点", ""])
        for item in highlights:
            lines.append(f"- {item}")
        lines.append("")
    chapters = notes.get("chapters") or []
    if chapters:
        lines.extend(["## 章节", ""])
        for chapter in chapters:
            heading = f"### {chapter.get('start') or '00:00'} {chapter.get('title') or ''}".rstrip()
            lines.append(heading)
            lines.append("")
            if chapter.get("summary"):
                lines.append(chapter["summary"])
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def _chat(system: str, user: str, timeout: float = 90.0) -> str:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise LlmError("llm_not_configured", "还没有配置 LLM_API_KEY，请复制 .env.example 为 .env 后填写")

    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    content = user if len(user) <= MAX_CHARS + 80 else user[:MAX_CHARS] + "\n\n[字幕已截断]"

    try:
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
    except LlmError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LlmError("llm_failed", f"模型调用失败：{exc}") from exc

    if not text or not str(text).strip():
        raise LlmError("llm_failed", "模型没有返回内容")
    return str(text).strip()


def summarize(cues: list[dict[str, Any]], title: str = "") -> dict[str, Any]:
    kept, truncated = trim_cues(cues)
    if not kept:
        raise LlmError("llm_failed", "没有可用的字幕文本")
    prompt = cues_to_prompt(kept, truncated)
    heading = f"视频标题：{title}\n\n" if title else ""
    raw = _chat(
        "你是视频学习助手。只根据字幕写中文学习笔记，不要编造字幕里没有的信息。"
        "章节 start 必须接近字幕里真实出现的时间戳（用 MM:SS 或 H:MM:SS）。"
        "只输出 JSON，不要 Markdown 说明："
        '{"overview":"200到400字概述","highlights":["要点最多8条"],'
        '"chapters":[{"start":"00:00","title":"短标题","summary":"该段几句话"}]}。'
        "短视频 2–4 个章节，长视频 4–10 个。",
        f"{heading}字幕如下：\n{prompt}",
        timeout=120.0,
    )
    notes = parse_notes_payload(raw)
    notes["cues"] = kept
    notes["markdown"] = notes_to_markdown(title, notes)
    return notes


def ask(cues: list[dict[str, Any]], question: str) -> str:
    query = (question or "").strip()
    if not query:
        raise LlmError("llm_failed", "请先输入要问的问题")
    kept, truncated = trim_cues(cues)
    if not kept:
        raise LlmError("llm_failed", "没有可用的字幕文本")
    prompt = cues_to_prompt(kept, truncated)
    return _chat(
        "你是视频学习助手。只根据字幕回答问题。字幕里没有就直说没有，不要编造。用中文回答。",
        f"字幕如下：\n{prompt}\n\n问题：{query}",
    )


def translate(subtitle_text: str, target_lang: str) -> str:
    lang = "简体中文" if target_lang == "zh" else "English"
    return _chat(
        f"你是字幕翻译。把内容译成{lang}，保留段落，不要添加解说。",
        subtitle_text,
    )
