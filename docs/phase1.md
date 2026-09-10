# 秒存 — 第一期实现总结

本文记录第一期已经落地的能力、踩过的坑和当前边界。扩功能仍以 [requirements.md](requirements.md)、[design.md](design.md) 为准；本文是回顾，不是替代。

完成时间：2026-09-10。下载主路径（粘贴链接 → 解析 → 选清晰度 → 保存到本地）已验收可用。

## 1. 做成了什么

本机可跑的单页工具「秒存」：FastAPI + Jinja + yt-dlp。用户打开 http://127.0.0.1:8000，粘贴视频链接，解析封面/标题/清晰度，再下载。

| 能力 | 现状 |
| --- | --- |
| 解析 | `POST /api/parse`：封面、标题、时长、上传者、清晰度 chips |
| 下载 | `POST /api/download` + SSE 进度，文件经浏览器保存 |
| 播放列表 | 只列条目，点选一条再解析/下载 |
| 封面 | `/api/thumb` 同源代理，避免 B 站 CDN 因 localhost Referer 返回 403 |
| AI | 有字幕且配置了 `LLM_API_KEY` 才能总结/翻译；否则明确失败 |
| Pro | 仅视觉占位，不接支付、不锁功能 |
| 抖音公开视频 | **不需要浏览器 Cookie** |

刻意没做：账号、数据库、真实支付、Whisper、公开站队列、改 yt-dlp 源码、绕过 DRM。

## 2. 怎么跑

Windows 上不要执行 `Activate.ps1`（执行策略会拦）。直接：

```powershell
cd d:\project\ai-code\free-video-download
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

依赖：Python 3.11+、本机 ffmpeg（合并音视频）。改代码后必须重启 uvicorn（当前启动命令没有 `--reload`）。

## 3. 代码地图

```text
app/main.py            路由、SSE、静态页
app/ytdlp_service.py   YoutubeDL 封装：解析 / 下载 / 字幕 / 清晰度桶
app/douyin_share.py    抖音公开分享页回退（无 Cookie）
app/thumbs.py          封面代理 + SSRF 限制
app/ai_service.py      字幕 → LLM
app/jobs.py            内存任务 + downloads/ 临时文件
templates/index.html
static/css/style.css
static/js/app.js
tests/test_unit.py
```

下载不把平台直链交给前端跨域拉取，一律服务端下完再给文件。

## 4. 第一期踩过的坑（后续不要退回去）

### 4.1 抖音搜索页 `Unsupported URL`

用户从搜索/发现页复制的地址常常是 `/search/...?modal_id=数字`，yt-dlp 只认 `/video/{id}`。

`canonicalize_url()` 会抽出 `modal_id` / `aweme_id` / `item_id`，改写成 `https://www.douyin.com/video/{id}`。没有视频 ID 的纯搜索页直接报错，不把 Unsupported URL 甩给用户。

### 4.2 抖音 Cookie 锁死（已放弃这条路）

yt-dlp 官方 Douyin 提取器要「新鲜 Cookie」。Windows 上 Chrome/Edge 开着会锁住 Cookie 库，`cookiesfrombrowser` 失败。要求用户关浏览器才能解析，体验不可接受。

### 4.3 抖音无 Cookie 方案（当前主路径）

参考开源分享页解析（如 [qgeng1465/douyin-watermark-free-downloader](https://github.com/qgeng1465/douyin-watermark-free-downloader)）：

1. 用 iPhone UA 请求 `https://www.iesdouyin.com/share/video/{id}/?note_aweme_type=0`（没有这个 query 时，页面经常只有壳、没有 `videoInfoRes`）
2. 解析 `window._ROUTER_DATA` 里的 `item_list[0].video.play_addr`
3. `playwm` 换成 `play` 后，把直链交给 `YoutubeDL` 下载

分享页有时第一次返回空壳，代码会重试最多 4 次。失败才回退 yt-dlp 官方提取器。

限制：分享页通常只给一条渐进式 MP4（实测可到 2160p），清晰度 chips 不再假装有 360/720/1080 多档。图文笔记、私密视频不支持。分享页结构若再改，`app/douyin_share.py` 会失效。

### 4.4 B 站 412 与封面 403

解析 B 站要带 `Origin` / `Referer` / 浏览器 UA，412 再试最多 3 次。封面走 `/api/thumb`，代理请求用平台 Referer，并拒绝内网地址。

## 5. 接口一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 单页 |
| GET | `/api/health` | ffmpeg / LLM 是否可用 |
| POST | `/api/parse` | 解析 |
| POST | `/api/download` | 创建下载任务 |
| GET | `/api/download/{id}/events` | SSE 进度 |
| GET | `/api/download/{id}/file` | 取文件 |
| GET | `/api/thumb?url=` | 封面代理 |
| POST | `/api/ai/summarize`、`/api/ai/translate` | 字幕 AI |

错误形如 `{ "ok": false, "error": "<code>", "message": "<中文>" }`，禁止假成功。

## 6. 已知缺口

- YouTube 部分清晰度依赖本机 deno，没有也能下，列表可能不完整
- AI 完全依赖外挂 Key 和平台字幕
- 任务在内存里，重启丢失；临时文件约 30 分钟清理
- 没有 `--reload` 时，改 Python 必须重启进程，否则会以为「代码改了还不生效」

## 7. 下一期候选（未做）

真实支付与是否锁 4K/批量/AI、账号、播放列表一键队列、Whisper、公开上线所需的限流与磁盘配额。
