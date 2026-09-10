"""OpenAI-compatible LLM calls. Never fake a successful summary."""

from __future__ import annotations

import os

import httpx


class LlmError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


MAX_CHARS = 12000


def llm_configured() -> bool:
    return bool(os.getenv("LLM_API_KEY", "").strip())


def _chat(system: str, user: str) -> str:
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not api_key:
        raise LlmError("llm_not_configured", "还没有配置 LLM_API_KEY，请复制 .env.example 为 .env 后填写")

    base = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    content = user if len(user) <= MAX_CHARS else user[:MAX_CHARS] + "\n\n[字幕已截断]"

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
            timeout=90.0,
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


def summarize(subtitle_text: str) -> str:
    return _chat(
        "你是视频学习助手。根据字幕写中文总结：先给 5 条以内要点，再写一段 120 字内概述。不要编造字幕里没有的信息。",
        f"字幕如下：\n{subtitle_text}",
    )


def translate(subtitle_text: str, target_lang: str) -> str:
    lang = "简体中文" if target_lang == "zh" else "English"
    return _chat(
        f"你是字幕翻译。把内容译成{lang}，保留段落，不要添加解说。",
        subtitle_text,
    )
