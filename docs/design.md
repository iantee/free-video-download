# 秒存 — 方案设计（第一期）

后续加功能前先读 [requirements.md](requirements.md) 和本文。改接口或模块边界时同步更新本文。

## 1. 总体架构

单进程 Python Web 应用：FastAPI 出页面和 JSON/SSE；Jinja 渲染一个单页；yt-dlp 官方 `YoutubeDL` 负责解析/下载/字幕；没有可提取字幕时由 Whisper 转写。AI 只吃字幕或转写文本。

```text
Browser
  → FastAPI (Jinja 单页 + /api/*)
      → ytdlp_service   YoutubeDL 薄封装（解析 / 下载 / 带时间戳字幕 cues）
      → whisper_service 无平台字幕时，把音频转成同一套 cues
      → ai_service      cues → 结构化笔记 / 翻译 / 追问
      → jobs            内存下载任务 + 学习会话（cues）+ 临时目录
```

不引入 Redis、数据库、消息队列。不 fork、不 vendoring 修改 yt-dlp 源码。

## 2. 目录与模块职责

```text
AGENTS.md                给后续 AI 的硬约束
README.md                先读文档 + 启动
docs/requirements.md
docs/design.md
docs/phase1.md           第一期实现总结（踩坑与验收边界）
app/main.py              路由、SSE、静态页
app/ytdlp_service.py     parse / download / 抽字幕 / 精简清晰度
app/douyin_share.py      抖音公开分享页回退（无 Cookie）
app/ai_service.py        summarize / translate；检查 Key
app/whisper_service.py   无平台字幕时的本地转写
app/jobs.py              job 字典、临时文件、延迟清理
templates/index.html
static/css/style.css
static/js/app.js
downloads/               运行时临时目录（gitignore）
.env.example
```

| 模块 | 允许做 | 不要做 |
| --- | --- | --- |
| `main.py` | HTTP 形状、校验 URL、把错误变成 JSON | 直接 new `YoutubeDL`、拼 LLM prompt |
| `ytdlp_service.py` | 官方 opts + `extract_info` / `download`；抖音公开视频可先走 `douyin_share` | 改 yt-dlp 内部、实现 a_bogus / DRM 绕过 |
| `douyin_share.py` | 读 iesdouyin 公开分享页 SSR JSON，直链仍交给 YoutubeDL 下载 | 签名算法、登录态爬取、改 yt-dlp 源码 |
| `ai_service.py` | 调 Chat Completions、限制字幕长度、解析笔记 JSON | 自己下载视频、跑 Whisper、编造无字幕总结 |
| `whisper_service.py` | 无平台字幕时，把音频转成带时间戳 cues | 塞进 `ytdlp_service`；转写为空仍当成功 |
| `jobs.py` | id、状态、路径、学习会话 cues、清理 | 持久化到 SQLite/磁盘索引（一期） |
| 前端 | 解析/下载/学习页 Tab（Markdown 笔记、思维导图全屏与高清图、字幕下载、追问）、Pro 弹层 | 直连各视频平台 CDN；思维导图不要另开接口 |

## 3. 如何调用 yt-dlp（禁止 fork）

官方形态：

```python
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.sanitize_info(ydl.extract_info(url, download=False))
```

### 3.1 解析

- 粘贴后先 `canonicalize_url`：抖音搜索/发现页若带 `modal_id`（或 `aweme_id`/`item_id`），改写成 yt-dlp 支持的 `https://www.douyin.com/video/{id}`。纯搜索页没有视频 ID 则明确报错，不把 Unsupported URL 原样甩给用户
- 抖音公开视频优先走移动端分享页 `iesdouyin.com/share/video/{id}?note_aweme_type=0`（无需浏览器 Cookie）。从 `_ROUTER_DATA.videoInfoRes` 取封面/标题/直链，下载仍交给 `YoutubeDL`。分享页失败时才回退 yt-dlp 官方 Douyin 提取器
- Cookie：`COOKIES_FILE` 优先；否则仅在设置了 `COOKIES_FROM_BROWSER` 时传给 yt-dlp。不再为抖音自动读取 Chrome/Edge（Windows 上浏览器开着会锁文件）。B 站 CC / AI 字幕同样依赖这组 Cookie：未登录时官方接口 `need_login_subtitle` 为空 URL，yt-dlp 只能拿到弹幕
- Windows 上 Chrome 开着会锁 Cookie 库。先尝试用共享读把库拷出来再交给 yt-dlp；失败则改未登录继续，不能用更早的 Cookie 锁错误盖住这次未登录结果。解析、字幕和总结都不因此停住。没有 CC 时照旧转写。B 站失败文案不能写成抖音。需要登录态 CC 时才提示退出浏览器或改用 `COOKIES_FILE`
- 所有 `YoutubeDL` 调用走 `base_opts(url, extra)`。B 站 / b23.tv 会自动加上 `Origin`、`Referer`、浏览器 UA；遇到 HTTP 412 再重试最多 3 次（B 站风控经常第一次拦、第二次放行）
- `noplaylist=False`：若是 playlist，返回 `_type=playlist` 与 `entries` 摘要，不在解析阶段下载
- 单条视频：取出 `id/title/thumbnail/duration/uploader/webpage_url/extractor`
- `thumbnail` 改写成同源 `/api/thumb?url=`。浏览器直接加载 B 站 `hdslb` 封面会带上本机 Referer，CDN 返回 403；代理请求会改用 B 站 Referer，并把 `http://` 升为 `https://`
- `GET /api/thumb` 只允许公网 http(s) 图片（拒绝内网/回环，限制 2MB），避免开放代理滥用
- 清晰度：扫描 `formats`，按高度桶去重，对外只暴露约 **360 / 720 / 1080 / best / audio**
- 实际下载时用 yt-dlp format 表达式，而不是手拼流地址：
  - 360：`bestvideo[height<=360]+bestaudio/best[height<=360]`
  - 720：`bestvideo[height<=720]+bestaudio/best[height<=720]`
  - 1080：`bestvideo[height<=1080]+bestaudio/best[height<=1080]`
  - best：`bv*+ba/b`
  - audio：`bestaudio/best`

音视频合并依赖本机 **ffmpeg**。没有 ffmpeg 时，下载接口返回明确错误。

### 3.2 下载

- `outtmpl` 指到 `downloads/{job_id}/media.%(ext)s`
- `progress_hooks` 把 percent 写进内存 job，供 SSE 读
- 完成后记录最终文件路径和用于 `Content-Disposition` 的标题
- 不要把未过期的直链交给前端去跨域拉（多数平台会失败）；一律服务端下完再给文件

### 3.3 字幕

- `writesubtitles=True`，`writeautomaticsub=True`
- `subtitleslangs` 优先 `zh-Hans,zh-CN,zh,en,all`（yt-dlp 不接受单独的 `*`）
- `subtitlesformat=vtt`，`skip_download=True`
- B 站解析时加 `listsubtitles=True`，但 **弹幕 `danmaku` 不算字幕**；硬字幕（烧进画面）无法提取，禁止当成功
- 找不到字幕文件时不直接失败：`download_url(..., "bestaudio/best")` 下音频，交给 `whisper_service.transcribe()`
- 模型默认 `base`（CPU、int8），可用 `WHISPER_MODEL`、`WHISPER_LANGUAGE` 覆盖。依赖 `faster-whisper`、`av>=14,<19`（PyAV 19 与当前 faster-whisper 不兼容）和本机 ffmpeg。权重来自 Hugging Face；连不上官方站时用 `HF_ENDPOINT`（例如 `https://hf-mirror.com`）。下载关闭 Xet，避免镜像返回 401
- 转写出的 cues 与平台字幕走同一套截断、笔记、追问、翻译。响应带 `source: "subtitle" | "whisper"`
- 未安装、模型加载失败、转写为空 → `whisper_missing` / `whisper_failed`，不调用 LLM
- 平台字幕优先。B 站弹幕仍不算字幕；Cookie 能拉到 CC 时不必转写
- 读生成的 `.vtt`：`vtt_to_cues()` 保留开始时间（`start` 秒数 + `label` 如 `00:12`）、结束时间 `end`（有则写入）和文本；连续重复行去重，并尽量把后一条的结束时间并进前一条
- `cues_to_srt()` 把 cues 写成 `.srt`。有 `end` 就用真实结束时间；没有则接到下一条开头，间隔超过 12 秒时只保留约 4 秒，避免一条字幕盖住长静音
- 翻译仍可把 cues 拼成纯文本（不含时间轴）
- 过长则截断到约 1.2 万字符，学习页字幕对照与 LLM 使用同一截断结果
- 平台字幕和转写都没有可用文本时，不调用 LLM

## 4. HTTP 接口

统一错误 JSON：`{"ok": false, "error": "<code>", "message": "<人类可读>"}`。

| code | 何时 |
| --- | --- |
| `invalid_url` | 空、非 http(s) |
| `unsupported` | yt-dlp `UnsupportedError` |
| `parse_failed` | 其它提取失败 |
| `download_failed` | 下载/合并失败 |
| `ffmpeg_missing` | 需要合并但系统没有 ffmpeg |
| `no_subtitles` | 抽字幕阶段确认没有平台字幕（随后会尝试转写；转写本身不再用这个码） |
| `whisper_missing` | 未安装 `faster-whisper` |
| `whisper_failed` | 模型加载失败、转写失败，或没有可用文本 |
| `llm_not_configured` | 未设置 `LLM_API_KEY` |
| `llm_failed` | 上游 LLM 错误或笔记 JSON 无法解析 |
| `learn_expired` | 追问时内存学习会话不存在或已清理 |
| `not_found` | job 不存在或文件已清 |

### 4.1 `POST /api/parse`

请求：`{"url": "https://..."}`

单条成功：

```json
{
  "ok": true,
  "type": "video",
  "title": "...",
  "thumbnail": "/api/thumb?url=https%3A%2F%2Fi0.hdslb.com%2Fbfs%2Farchive%2F....jpg",
  "duration": 123,
  "uploader": "...",
  "webpage_url": "...",
  "extractor": "youtube",
  "has_subtitles": true,
  "formats": [
    {"id": "720", "label": "720p", "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"}
  ]
}
```

播放列表成功：`type=playlist`，带 `entries: [{id,title,url,thumbnail,duration}]`，不带完整 formats。前端点选某条后再对 `url` 调 parse。

B 站若因浏览器锁住 Cookie 而改走未登录解析，响应多一个 `cookie_warning` 字符串。其它站点不加这个字段。`has_subtitles` 为 false 时，前端提示总结将走语音转写。

### 4.2 `POST /api/download`

请求：`{"url": "...", "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"}`

响应：`text/event-stream`

```text
data: {"status":"downloading","percent":42}

data: {"status":"done","job_id":"...","filename":"标题.mp4"}

data: {"status":"error","error":"download_failed","message":"..."}
```

### 4.3 `GET /api/file/{job_id}`

`FileResponse`，`Content-Disposition: attachment`。成功送出后安排删除该 job 目录（可短延迟，避免浏览器还在读）。

### 4.4 `POST /api/summary`

请求：`{"url": "..."}` → 抽 cues → LLM 输出结构化中文笔记。禁止返回占位假总结。JSON 解析失败用 `llm_failed`。

```json
{
  "ok": true,
  "learn_id": "…",
  "overview": "200–400 字概述",
  "highlights": ["要点1", "要点2"],
  "chapters": [
    {"start": "00:00", "title": "…", "summary": "…"}
  ],
  "cues": [{"start": 0, "label": "00:00", "text": "…"}],
  "markdown": "# 标题\n\n## 概述\n…",
  "source": "subtitle"
}
```

`learn_id` 对应内存 job。返回给页面的 `cues` 仍是截断后的那一份，和学习页对照、发给模型的文本一致。内存里另存**完整** cues 和 `source`，供追问（追问时再截断）和字幕下载复用，约 30 分钟由 `jobs.sweep_old` 清理。不落库。前端「思维导图」只用这份 JSON 里的概述、章节和要点绘制，响应里不另加导图字段。

### 4.5 `POST /api/subtitles`

请求：`{"url": "...", "learn_id": "可选"}`。不调用 LLM，未配置 Key 也能用。

已有未过期的 `learn_id` 时，直接用该会话里的完整 cues 生成 SRT，不再下载、不再转写。没有会话时按总结的同一路径取字幕：平台字幕优先，没有则 Whisper。转写失败仍是 `whisper_missing` / `whisper_failed`，不返回空文件。

```json
{
  "ok": true,
  "source": "subtitle",
  "srt": "1\n00:00:00,000 --> 00:00:01,500\n你好\n",
  "cues": [{"start": 0, "end": 1.5, "label": "00:00", "text": "你好"}]
}
```

这条请求自己的临时目录用完即删，不把下载任务写成学习会话。

### 4.6 `POST /api/translate`

请求：`{"url": "...", "target_lang": "zh"|"en"}` → 抽字幕或转写 → 翻译后的纯文本（可保留大致段落）。页面「字幕翻译」按钮固定传 `zh`。

### 4.7 `POST /api/ask`

请求：`{"learn_id": "...", "question": "..."}` → 用该会话的 cues 回答。成功 `{"ok": true, "text": "…"}`。没有会话 → `learn_expired`，提示重新点「AI 总结」。每问独立，不把多轮历史发给模型；前端可在当前页展示对话气泡。

### 4.8 `GET /api/health`

返回 `{"ok": true, "ffmpeg": true, "llm": true, "whisper": true}`。`ffmpeg` 看是否在 PATH，`llm` 看是否配置了 Key，`whisper` 看能否导入 `faster-whisper`。不展示密钥。

### 4.9 `GET /api/thumb?url=`

代理封面图。`url` 须为公网 http(s) 图片地址。B 站封面会附带 `Referer: https://www.bilibili.com/`。

## 5. 任务与磁盘

- `jobs`：`{job_id: {status, percent, filepath, filename, error, created_at, cues?}}`
- 学习会话：总结成功后把完整 cues 和 `source` 写进同一套内存字典，`learn_id` 即 `job_id`。页面上的对照文本仍用截断后的 cues
- 线程锁保护字典
- 临时根目录：项目下 `downloads/`
- 清理：下载接口送完文件后删；学习会话和过期目录约 30 分钟扫掉
- 第一期不做跨进程共享；上线多 worker 时必须换掉这个模块（见第 8 节）

## 6. AI

环境变量（见 `.env.example`）：

- `LLM_API_KEY` 必填才启用
- `LLM_BASE_URL` 默认 `https://api.openai.com/v1`（DeepSeek 等改成其兼容根路径）
- `LLM_MODEL` 默认 `gpt-4o-mini`

协议：`POST {LLM_BASE_URL}/chat/completions`，`httpx`，超时要足够（建议 60s+）。

字幕过长则截断（例如 1.2 万字符）并在 prompt 里说明「内容已截断」。

总结：把带时间戳的 cues 交给模型，要求只输出 JSON（概述、要点、章节）。章节 `start` 必须接近真实字幕时间，禁止编造字幕没有的信息。服务端解析 JSON 后生成 Markdown，供复制/导出。解析失败 → `llm_failed`。

追问：只根据该会话 cues 回答，不知道就说字幕里没有。

未配置 Key → `llm_not_configured`。不要返回占位假总结。

## 7. UI 设计语言

参考站实测 token（只复制气质，不复制「鱼皮AI导航」品牌）：

| Token | 值 |
| --- | --- |
| 顶栏 | 高 56px，背景 `rgba(255,255,255,0.9)`，阴影 `rgba(0,0,0,0.05) 0 1px 3px` |
| 主色 | `#1777FF` |
| 标题 | `#0F172A`，`font-weight: 900`，约 36px |
| 卡片 | 圆角 12px，阴影 `rgba(0,0,0,0.05) 0 4px 20px -4px`，hover 淡蓝光 |
| 主按钮 / 解析条 | `border-radius: 9999px` |
| 卡片内底 | `#F1F5F9`，细 ring slate |

结构映射：

| 参考站 | 秒存 |
| --- | --- |
| 一键出图标题 | 「全平台视频，一键保存到本地」 |
| 胶囊搜索 + 「搜索」 | 胶囊链接框 + 「免费解析」+ 「AI 总结」 |
| 瀑布模板卡 | 平台能力卡 + Pro 能力卡 |
| 模板详情左图右操作 | 解析结果：左封面，右清晰度 / 下载；下方学习卡（笔记 / 思维导图 / 字幕 / 追问） |

产品名：**秒存**。顶栏右侧「升级 Pro」打开说明弹层，不跳支付。

窄屏：顶栏链接收进菜单；解析条两按钮、结果区、学习 Tab 单列。

学习卡：「免费解析」后默认不跑 AI；点结果区「AI 总结」或首页「AI 总结」再生成。不内嵌原站播放器。

「学习笔记」渲染总结返回的 Markdown：标题、列表、行内强调和章节时间。先转义再插入标签，不执行原文里的 HTML。复制和导出仍是 Markdown 原文。

「思维导图」Tab 只用这次总结返回的概述、章节、要点在前端画 SVG：根节点是视频标题，分支是概述、各章节（时间 + 标题，叶子是该段摘要）和要点。不新开接口，不另调模型，不落库。笔记三项都空时显示说明，不画空导图。卡片里可以横向滚动；「全屏阅读」铺满视口，可滚轮缩放、拖拽和平移按钮。「下载高清图」把整张 SVG（不是当前视口）画成 PNG，优先 3 倍分辨率，单边超过 8192 像素时再降倍数。

结果区和学习页都有「下载字幕」。浏览器保存 `.srt`。已有 `learn_id` 时走 `POST /api/subtitles` 复用会话；否则现拉字幕或转写。不要求配置 LLM Key。

## 8. 扩展点（给下一期 AI）

改功能时优先扩这些缝，而不是新起一套下载内核。

| 能力 | 改哪 | 不要动 |
| --- | --- | --- |
| 真实支付 | 新 `billing` 模块 + 才把 Pro 锁接到 `main.py` | yt-dlp 封装形态 |
| 账号 | 新 auth；jobs 开始按 user 隔离 | 先不要为了账号引入复杂 ORM |
| 完整批量 | `jobs` 改成队列（可仍内存）；前端一次提交多个 entry | 不要在浏览器里循环打下载直到把机器打满还不限并发 |
| Whisper | 已落地：`app/whisper_service.py`，仅无平台字幕时调用 | 不要塞进 `ytdlp_service`，也不要把空转写做成假成功 |
| 思维导图 | 已落地：学习页 SVG。全屏和 PNG 都在前端完成 | 不要另开接口、另调模型，或为此引入账号或数据库 |
| 字幕文件 | 已落地：`POST /api/subtitles` 返回 SRT，页面触发浏览器保存 | 不要为下载字幕调用 LLM，也不要在转写失败时给空文件 |
| 术语表 | 新前端模块 + 现有 notes JSON | 不要为此引入账号或数据库 |
| 数据库 | 只替换 `jobs.py` 的存储实现，保持 job_id 与文件路径契约 | 不要让 `YoutubeDL` 直接写 DB |
| 公开上线 | 限流中间件、体积上限、并发信号量、下载时长上限 | 不要去掉免责声明 |

## 9. 运行依赖

- Python 3.11+
- pip：`fastapi`、`uvicorn[standard]`、`yt-dlp`、`httpx`、`jinja2`、`python-multipart`
- 系统：`ffmpeg`（合并与部分后处理）
- 可选：`LLM_API_KEY`
- 无字幕转写：`faster-whisper`（`WHISPER_MODEL` 默认 `base`，`WHISPER_LANGUAGE` 默认识别）

启动（与 README 一致，不带 `--reload`；改 Python 后要重启进程）：

`.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
