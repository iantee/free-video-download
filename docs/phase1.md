# 秒存 — 第一期实现总结

本文记录第一期已经落地的能力、踩过的坑和当前边界。扩功能仍以 [requirements.md](requirements.md)、[design.md](design.md) 为准；本文是回顾，不是替代。

下载主路径（粘贴链接 → 解析 → 选清晰度 → 保存到本地）在 2026-09-10 已验收可用。学习页在此后补上，本文记录到 2026-10-24，以当前代码为准。

## 1. 做成了什么

本机可跑的单页工具「秒存」：FastAPI + Jinja + yt-dlp。用户打开 http://127.0.0.1:8000，粘贴视频链接，解析封面/标题/清晰度，再下载；也可以不下载，直接生成学习笔记。

| 能力 | 现状 |
| --- | --- |
| 解析 | `POST /api/parse`：封面、标题、时长、上传者、清晰度 chips、`has_subtitles` |
| 下载 | `POST /api/download` 在同一条响应里推 SSE 进度，`GET /api/file/{job_id}` 交给浏览器保存 |
| 播放列表 | 只列条目，点选一条再解析/下载。没有一键批量 |
| 封面 | `/api/thumb` 同源代理，避免 B 站 CDN 因 localhost Referer 返回 403 |
| 学习笔记 | 已配 `LLM_API_KEY` 时：概述、要点、带时间戳章节。页面渲染 Markdown（标题、列表、强调、章节时间），复制 / 导出仍是原文 |
| 思维导图 | 学习页 Tab，前端 SVG。用同一次笔记的概述、章节、要点绘制，无新接口、不再调模型。可全屏缩放，并可下载整张高清 PNG。三项都空时只提示，不画空图 |
| 字幕文件 | 结果区和学习页可单独下载 `.srt`，不需要 LLM。已总结过则复用本次完整字幕；否则现拉平台字幕，没有再转写 |
| 无平台字幕 | 下载音频，`whisper_service` 转写后再走同一套学习页。响应 `source` 为 `subtitle` 或 `whisper`。转写失败或未安装时明确报错 |
| 字幕翻译 | 页面按钮译成中文。`POST /api/translate` 另接受 `target_lang=en`，页面还没有语言切换 |
| 追问 | `POST /api/ask`，用内存里的 `learn_id`（约 30 分钟）。每问独立，不把多轮历史发给模型 |
| Pro | 仅视觉占位，不接支付、不锁功能 |
| 抖音公开视频 | **不需要浏览器 Cookie** |

刻意没做：账号、数据库、真实支付、公开站队列、改 yt-dlp 源码、绕过 DRM、术语表、笔记历史。

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
app/ai_service.py      字幕或转写 → 笔记 / 翻译 / 追问
app/whisper_service.py 无平台字幕时的本地转写（faster-whisper，CPU int8）
app/jobs.py            内存任务、学习会话 cues、downloads/ 临时文件
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

### 4.5 B 站字幕与 Cookie 锁

弹幕 `danmaku` 不算字幕。未登录时官方接口 `need_login_subtitle` 给出的 `subtitle_url` 为空，匿名签 WBI 也拿不到 CC 地址。硬字幕烧在画面里，不能当提取成功。

`COOKIES_FILE` 优先，否则才用 `COOKIES_FROM_BROWSER`。Windows 上 Chrome/Edge 开着会锁 Cookie 库。锁住时解析、拉字幕、总结都改走未登录，不能把 Cookie 锁当成整次失败；没有 CC 就转写。B 站响应带 `cookie_warning`。失败文案按站点区分，禁止对 B 站链接提示「打开一次抖音」。

### 4.6 无字幕时的 Whisper

只有 `fetch_subtitle_cues` 返回 `no_subtitles` 才下载 `bestaudio/best` 并调用 `whisper_service.transcribe()`。模型默认 `base`，可用 `WHISPER_MODEL`、`WHISPER_LANGUAGE` 覆盖。依赖 `faster-whisper` 和 `av>=14,<19`。连不上 huggingface.co 时用 `HF_ENDPOINT`；代码里会 `setdefault("HF_HUB_DISABLE_XET", "1")`，避免镜像走 Xet 返回 401。转写出空文本是 `whisper_failed`，不调用 LLM。

### 4.7 思维导图

不新开接口。`static/js/app.js` 的 `renderMind()` 读取总结 JSON，画成 SVG：根节点是视频标题，分支是概述、各章节（时间 + 标题，叶子是该段摘要）和要点。空白要点会被滤掉。「全屏阅读」铺满视口，可缩放和拖拽；拖动背景时禁止选中节点文字。「下载高清图」导出整张图，不是当前看见的那一块。优先 3 倍分辨率，单边超过 8192 像素时降低倍数。

### 4.8 字幕文件

`POST /api/subtitles` 不调用模型。cues 带 `end` 时 SRT 用真实结束时间。学习会话在内存里留完整 cues，下载时优先复用，避免无字幕视频再转写一次。页面返回的对照字幕仍是截断后的那份。

## 5. 接口一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 单页 |
| GET | `/api/health` | `ffmpeg` / `llm` / `whisper` 是否可用 |
| POST | `/api/parse` | 解析。单条含 `has_subtitles`；B 站 Cookie 被锁时多 `cookie_warning` |
| POST | `/api/download` | 同一条 `text/event-stream` 推送进度，直到 `done` 或 `error` |
| GET | `/api/file/{job_id}` | 取已下完的文件，送出后安排删除 |
| GET | `/api/thumb?url=` | 封面代理 |
| POST | `/api/summary` | 字幕或转写 → 笔记（`learn_id`、概述、要点、章节、截断后的 cues、markdown、`source`）。会话里保留完整 cues |
| POST | `/api/subtitles` | 单独下载用的 SRT。有 `learn_id` 则复用会话；否则现拉字幕或转写。不需要 LLM |
| POST | `/api/translate` | 字幕或转写后翻译。页面只传 `zh`，接口也接受 `en` |
| POST | `/api/ask` | 基于 `learn_id` 会话追问 |

错误形如 `{ "ok": false, "error": "<code>", "message": "<中文>" }`，禁止假成功。

## 6. 已知缺口

- YouTube 部分清晰度依赖本机 deno，没有也能下，列表可能不完整
- AI 笔记、翻译、追问依赖外挂 `LLM_API_KEY`。没有平台字幕时改走 Whisper，不再把「没有字幕」直接当成无法总结
- 页面字幕翻译只有中文。英文要自己调 `POST /api/translate` 并传 `target_lang=en`
- B 站 CC 在未登录时拿不到地址。浏览器开着时 Cookie 库通常读不出来，这时只能转写，不能假装抽到了原文 CC
- 任务在内存里，重启丢失；临时文件约 30 分钟清理
- 启动命令没有 `--reload`。改 Python 必须重启进程，否则会以为「代码改了还不生效」

## 7. 还没做

真实支付与是否锁 4K/批量/AI、账号、播放列表一键队列、术语表、笔记历史、公开上线所需的限流与磁盘配额。
