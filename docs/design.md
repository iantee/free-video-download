# 秒存 — 方案设计（第一期）

后续加功能前先读 [requirements.md](requirements.md) 和本文。改接口或模块边界时同步更新本文。

## 1. 总体架构

单进程 Python Web 应用：FastAPI 出页面和 JSON/SSE；Jinja 渲染一个单页；yt-dlp 官方 `YoutubeDL` 负责解析/下载/字幕；AI 只吃字幕文本。

```text
Browser
  → FastAPI (Jinja 单页 + /api/*)
      → ytdlp_service   YoutubeDL 薄封装
      → ai_service      字幕文本 → OpenAI 兼容 Chat Completions
      → jobs            内存任务 + 临时目录
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
| `ai_service.py` | 调 Chat Completions、限制字幕长度 | 自己下载视频、跑 Whisper |
| `jobs.py` | id、状态、路径、清理 | 持久化到 SQLite/磁盘索引（一期） |
| 前端 | 解析/下载/AI 交互、Pro 弹层 | 直连各视频平台 CDN |

## 3. 如何调用 yt-dlp（禁止 fork）

官方形态：

```python
with yt_dlp.YoutubeDL(opts) as ydl:
    info = ydl.sanitize_info(ydl.extract_info(url, download=False))
```

### 3.1 解析

- 粘贴后先 `canonicalize_url`：抖音搜索/发现页若带 `modal_id`（或 `aweme_id`/`item_id`），改写成 yt-dlp 支持的 `https://www.douyin.com/video/{id}`。纯搜索页没有视频 ID 则明确报错，不把 Unsupported URL 原样甩给用户
- 抖音公开视频优先走移动端分享页 `iesdouyin.com/share/video/{id}?note_aweme_type=0`（无需浏览器 Cookie）。从 `_ROUTER_DATA.videoInfoRes` 取封面/标题/直链，下载仍交给 `YoutubeDL`。分享页失败时才回退 yt-dlp 官方 Douyin 提取器
- Cookie：`COOKIES_FILE` 优先；否则仅在设置了 `COOKIES_FROM_BROWSER` 时传给 yt-dlp。不再为抖音自动读取 Chrome/Edge（Windows 上浏览器开着会锁文件）
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
- `subtitleslangs` 优先 `zh-Hans,zh-CN,zh,en,*`
- `subtitlesformat=vtt`，`skip_download=True`
- 读生成的 `.vtt`，去掉时间轴，拼成纯文本
- 找不到任何字幕文件 → 业务错误 `no_subtitles`，不调用 LLM

## 4. HTTP 接口

统一错误 JSON：`{"ok": false, "error": "<code>", "message": "<人类可读>"}`。

| code | 何时 |
| --- | --- |
| `invalid_url` | 空、非 http(s) |
| `unsupported` | yt-dlp `UnsupportedError` |
| `parse_failed` | 其它提取失败 |
| `download_failed` | 下载/合并失败 |
| `ffmpeg_missing` | 需要合并但系统没有 ffmpeg |
| `no_subtitles` | 无字幕且无自动字幕 |
| `llm_not_configured` | 未设置 `LLM_API_KEY` |
| `llm_failed` | 上游 LLM 错误 |
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

请求：`{"url": "..."}` → 抽字幕 → LLM 输出中文总结（要点列表 + 一两段概述）。

### 4.5 `POST /api/translate`

请求：`{"url": "...", "target_lang": "zh"|"en"}` → 抽字幕 → 翻译后的纯文本（可保留大致段落）。

### 4.6 `GET /api/health`

返回 ffmpeg 是否在 PATH、LLM 是否已配置。用于自检，不展示密钥。

### 4.7 `GET /api/thumb?url=`

代理封面图。`url` 须为公网 http(s) 图片地址。B 站封面会附带 `Referer: https://www.bilibili.com/`。

## 5. 任务与磁盘

- `jobs`：`{job_id: {status, percent, filepath, filename, error, created_at}}`
- 线程锁保护字典
- 临时根目录：项目下 `downloads/`
- 清理：下载接口送完文件后删；启动时可扫掉过期目录（例如 >30 分钟）
- 第一期不做跨进程共享；上线多 worker 时必须换掉这个模块（见第 8 节）

## 6. AI

环境变量（见 `.env.example`）：

- `LLM_API_KEY` 必填才启用
- `LLM_BASE_URL` 默认 `https://api.openai.com/v1`（DeepSeek 等改成其兼容根路径）
- `LLM_MODEL` 默认 `gpt-4o-mini`

协议：`POST {LLM_BASE_URL}/chat/completions`，`httpx`，超时要足够（建议 60s+）。

字幕过长则截断（例如 1.2 万字符）并在 prompt 里说明「内容已截断」。

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
| 胶囊搜索 + 「搜索」 | 胶囊链接框 + 「免费解析」 |
| 瀑布模板卡 | 平台能力卡 + Pro 能力卡 |
| 模板详情左图右操作 | 解析结果：左封面，右清晰度 / 下载 / AI |

产品名：**秒存**。顶栏右侧「升级 Pro」打开说明弹层，不跳支付。

窄屏：顶栏链接收进菜单；解析条与结果区单列。

## 8. 扩展点（给下一期 AI）

改功能时优先扩这些缝，而不是新起一套下载内核。

| 能力 | 改哪 | 不要动 |
| --- | --- | --- |
| 真实支付 | 新 `billing` 模块 + 才把 Pro 锁接到 `main.py` | yt-dlp 封装形态 |
| 账号 | 新 auth；jobs 开始按 user 隔离 | 先不要为了账号引入复杂 ORM |
| 完整批量 | `jobs` 改成队列（可仍内存）；前端一次提交多个 entry | 不要在浏览器里循环打下载直到把机器打满还不限并发 |
| Whisper | 仅当 `no_subtitles` 时的可选后路，新模块 | 不要塞进 `ytdlp_service` |
| 数据库 | 只替换 `jobs.py` 的存储实现，保持 job_id 与文件路径契约 | 不要让 `YoutubeDL` 直接写 DB |
| 公开上线 | 限流中间件、体积上限、并发信号量、下载时长上限 | 不要去掉免责声明 |

## 9. 运行依赖

- Python 3.11+
- pip：`fastapi`、`uvicorn[standard]`、`yt-dlp`、`httpx`、`jinja2`、`python-multipart`
- 系统：`ffmpeg`（合并与部分后处理）
- 可选：`LLM_API_KEY`

启动：`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
