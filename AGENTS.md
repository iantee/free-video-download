# 秒存 — 给 AI 的约束

改任何功能之前：

1. 先读 [docs/requirements.md](docs/requirements.md) 和 [docs/design.md](docs/design.md)
2. 行为或接口变了，必须同步改这两份文档
3. 不要 fork、不要改 yt-dlp 源码；只通过 `yt_dlp.YoutubeDL` 封装
4. 第一期不要擅自加数据库、账号、真实支付、Whisper、公开站队列
5. 不要绕过 DRM；不要把失败做成假成功
6. UI 只借 [鱼皮绘图页](https://ai.codefather.cn/painting) 的设计语言（亮蓝 `#1777FF`、胶囊条、轻阴影卡片），不要抄其品牌
