# 秒存

本机可跑的万能视频下载站：粘贴链接 → 解析清晰度 → 下载到电脑或手机。下载能力来自 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，不自研各平台解析。

## 先读文档（扩功能必读）

后续加功能或让 AI 改代码时，**必须先读这两份**，改了行为要同步改文档：

- [docs/requirements.md](docs/requirements.md) — 需求：范围、非范围、用户路径、验收
- [docs/design.md](docs/design.md) — 方案：模块、接口、yt-dlp 用法、扩展点
- [docs/phase1.md](docs/phase1.md) — 第一期实现总结：已验收能力、踩坑、抖音无 Cookie 方案
- [AGENTS.md](AGENTS.md) — 给 Cursor / 后续 AI 的硬约束

## 环境

- Python 3.11+
- 系统已安装 [ffmpeg](https://ffmpeg.org/) 并在 `PATH` 中（合并音视频需要）
- 可选：OpenAI 兼容 API Key（AI 总结 / 字幕翻译）
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

## 第一期刻意不做

账号、数据库、真实支付、Whisper、公开站点级限流队列。详见需求文档。
