# 秒存

本机可跑的万能视频下载站：粘贴链接 → 解析清晰度 → 下载到电脑或手机，也可以不下载、直接生成学习笔记。下载能力来自 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，不自研各平台解析。

## 现在能做什么

- **解析**：封面、标题、时长、上传者、清晰度。抖音公开视频走分享页，不需要登录。播放列表只列出条目，点一条再解析。
- **下载**：选约 360 / 720 / 1080 / 最佳画面 / 仅音频，进度可见，浏览器保存文件。
- **AI 学习**（需 `LLM_API_KEY`）：概述、要点、带时间戳章节。笔记按 Markdown 排版显示，也可以复制或导出原文。字幕对照、追问同样在学习页。
- **思维导图**：总结成功后在学习页展开，用的是同一次笔记，不再额外请求模型。可以全屏阅读，也可以下载整张高清图。
- **字幕文件**：解析之后可以单独下载 `.srt`，不用先做 AI 总结。已经总结过的视频会直接用这次字幕，不必再转写。
- **无平台字幕**：下载音频，本机 Whisper 转写后再走同一套学习页。转写失败会明确报错。B 站弹幕不算字幕；能读到登录 Cookie 时优先用 CC。浏览器锁住 Cookie 时不中断总结，改为未登录继续，没有 CC 就转写。
- **字幕翻译**：页面按钮译成中文。接口也接受英文（`target_lang=en`），页面上还没有语言切换。
- **Pro**：定价卡和「升级」弹层只是视觉，不扣费、不锁功能。

## 先读文档（扩功能必读）

后续加功能或让 AI 改代码时，**必须先读这两份**，改了行为要同步改文档：

- [docs/requirements.md](docs/requirements.md) — 需求：范围、非范围、用户路径、验收
- [docs/design.md](docs/design.md) — 方案：模块、接口、yt-dlp 用法、扩展点
- [docs/phase1.md](docs/phase1.md) — 已落地能力、踩坑、抖音无 Cookie、B 站字幕与 Whisper、思维导图
- [AGENTS.md](AGENTS.md) — 给 Cursor / 后续 AI 的硬约束

## 环境

- Python 3.11+
- 系统已安装 [ffmpeg](https://ffmpeg.org/) 并在 `PATH` 中（合并音视频需要）
- 可选：OpenAI 兼容 API Key（AI 学习笔记 / 思维导图 / 字幕翻译 / 追问）。思维导图由同一次笔记在页面上展开，不再额外请求模型
- 没有平台字幕时会下载音频，用本机 Whisper 转写后再总结。首次使用会下载模型（默认 `base`）。可在 `.env` 设置 `WHISPER_MODEL`、`WHISPER_LANGUAGE`。连不上 huggingface.co 时设置 `HF_ENDPOINT=https://hf-mirror.com`
- 可选：B 站若能读到登录 Cookie，会优先用平台 CC，不再转写
- 可选：安装 [deno](https://deno.land/)，YouTube 部分清晰度列表会更完整（没有也能下）
- 抖音公开视频走分享页解析，不需要登录或浏览器 Cookie。若提示失败，请换一条能在浏览器打开的视频链接后重试

```powershell
cd d:\project\ai-code\free-video-download
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell 若提示「禁止运行脚本」，不要执行 `Activate.ps1`，直接用上面的 `.\.venv\Scripts\python.exe -m uvicorn ...` 即可。

浏览器打开 <http://127.0.0.1:8000>。

## 合规

仅用于保存你有权下载的内容（自己的作品、已获授权、许可协议允许保存的内容等）。本工具不针对 DRM，也不提供绕过版权保护的能力。

## 刻意不做

账号、数据库、真实支付、公开站点级限流队列、改 yt-dlp 源码、绕过 DRM。术语表和笔记历史还没做。详见需求文档。
