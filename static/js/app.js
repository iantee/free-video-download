const $ = (id) => document.getElementById(id);

const parseForm = $("parseForm");
const urlInput = $("urlInput");
const parseBtn = $("parseBtn");
const heroLearnBtn = $("heroLearnBtn");
const parseHint = $("parseHint");
const workbench = $("workbench");
const playlistPanel = $("playlistPanel");
const resultPanel = $("resultPanel");
const formatChips = $("formatChips");
const downloadBtn = $("downloadBtn");
const summaryBtn = $("summaryBtn");
const translateBtn = $("translateBtn");
const progressWrap = $("progressWrap");
const progressFill = $("progressFill");
const progressText = $("progressText");
const aiBox = $("aiBox");
const learnPanel = $("learnPanel");
const learnStatus = $("learnStatus");
const notesPane = $("notesPane");
const mindPane = $("mindPane");
const cuesPane = $("cuesPane");
const askPane = $("askPane");
const askThread = $("askThread");
const askForm = $("askForm");
const askInput = $("askInput");
const askBtn = $("askBtn");
const copyMdBtn = $("copyMdBtn");
const exportMdBtn = $("exportMdBtn");
const subtitleBtn = $("subtitleBtn");
const downloadCueBtn = $("downloadCueBtn");
const mindScreen = $("mindScreen");
const mindScreenTitle = $("mindScreenTitle");
const mindViewport = $("mindViewport");
const menuBtn = $("menuBtn");
const nav = $("nav");
const proModal = $("proModal");

let current = {
  url: "",
  format: "",
  title: "",
  learnId: "",
  markdown: "",
  cues: [],
  srt: "",
  mindSvg: null,
};

const mindView = { scale: 1, x: 0, y: 0 };
let mindDrag = null;

function showHint(message, isError = true) {
  parseHint.hidden = !message;
  parseHint.textContent = message || "";
  parseHint.style.color = isError ? "#dc2626" : "#64748b";
}

function formatDuration(seconds) {
  if (!seconds && seconds !== 0) return "";
  const total = Number(seconds) || 0;
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null && text !== "") node.textContent = text;
  return node;
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.message || data.error || "请求失败";
  } catch {
    return "请求失败";
  }
}

function resetLearn(hidePanel) {
  current.learnId = "";
  current.markdown = "";
  current.cues = [];
  current.srt = "";
  current.mindSvg = null;
  closeMindScreen();
  askThread.replaceChildren();
  notesPane.replaceChildren();
  mindPane.replaceChildren();
  cuesPane.replaceChildren();
  learnStatus.hidden = true;
  learnStatus.textContent = "";
  learnStatus.classList.remove("is-error");
  aiBox.hidden = true;
  if (hidePanel) learnPanel.hidden = true;
  copyMdBtn.disabled = true;
  exportMdBtn.disabled = true;
  syncCueDownload();
}

function switchTab(name) {
  document.querySelectorAll("#learnTabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  notesPane.hidden = name !== "notes";
  mindPane.hidden = name !== "mind";
  cuesPane.hidden = name !== "cues";
  askPane.hidden = name !== "ask";
  if (name !== "mind") closeMindScreen();
}

function setLearnStatus(message, isError = false) {
  learnStatus.hidden = !message;
  learnStatus.textContent = message || "";
  learnStatus.classList.toggle("is-error", Boolean(isError));
}

function syncCueDownload() {
  downloadCueBtn.disabled = !(current.learnId || current.srt || (current.cues && current.cues.length));
}

function safeFileName(title, ext) {
  const name = String(title || "视频").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80);
  return `${name || "视频"}.${ext}`;
}

function saveBlob(blob, filename) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function saveTextFile(text, filename, mime) {
  saveBlob(new Blob([text], { type: mime }), filename);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderInline(text) {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return html;
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const parts = [];
  let list = [];
  const flushList = () => {
    if (!list.length) return;
    parts.push(`<ul>${list.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
    list = [];
  };
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      return;
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushList();
      const level = heading[1].length;
      let body = heading[2];
      let stamp = "";
      const time = /^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.*)$/.exec(body);
      if (level === 3 && time) {
        stamp = `<time>${escapeHtml(time[1])}</time>`;
        body = time[2];
      }
      parts.push(`<h${level}>${stamp}${renderInline(body)}</h${level}>`);
      return;
    }
    const item = /^[-*]\s+(.*)$/.exec(trimmed);
    if (item) {
      list.push(item[1]);
      return;
    }
    flushList();
    parts.push(`<p>${renderInline(trimmed)}</p>`);
  });
  flushList();
  const article = document.createElement("article");
  article.className = "md-doc";
  article.innerHTML = parts.join("");
  return article;
}

function renderNotes(data) {
  notesPane.replaceChildren();
  const markdown = (data && data.markdown) || current.markdown || "";
  if (String(markdown).trim()) {
    notesPane.append(renderMarkdown(markdown));
    return;
  }
  const overview = el("section", "learn-overview");
  overview.append(el("h3", "", "概述"), el("p", "", (data && data.overview) || "暂无概述"));
  notesPane.append(overview);
}

const SVG_NS = "http://www.w3.org/2000/svg";
const MIND_FONT = '"Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif';
const MIND_LINE = 22;
const MIND_PAD_X = 14;
const MIND_PAD_Y = 12;
const MIND_GAP_X = 56;
const MIND_GAP_Y = 14;
const MIND_MARGIN = 28;

const MIND_KIND = {
  root: { fontSize: 16, fontWeight: 800, maxText: 240, fill: "#1777FF", color: "#ffffff", stroke: "none" },
  branch: { fontSize: 15, fontWeight: 700, maxText: 280, fill: "#ffffff", color: "#0f172a", stroke: "#b9d4ff" },
  leaf: { fontSize: 14, fontWeight: 400, maxText: 420, fill: "#f1f5f9", color: "#334155", stroke: "none" },
};

let mindMeasureCtx = null;

function svgEl(name, attrs) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function mindFont(node) {
  return `${node.fontWeight} ${node.fontSize}px ${MIND_FONT}`;
}

function textWidth(text, font) {
  if (!mindMeasureCtx) mindMeasureCtx = document.createElement("canvas").getContext("2d");
  mindMeasureCtx.font = font;
  return mindMeasureCtx.measureText(text || "").width;
}

function takeLine(text, font, maxWidth) {
  let line = "";
  for (let index = 0; index < text.length; index += 1) {
    const next = line + text[index];
    if (line && textWidth(next, font) > maxWidth) return [line, text.slice(index)];
    line = next;
  }
  return [line, ""];
}

function wrapBody(text, font, maxWidth, firstMax) {
  const lines = [];
  let rest = String(text || "").trim();
  let limit = firstMax;
  while (rest) {
    const [line, next] = takeLine(rest, font, Math.max(48, limit));
    if (!line) break;
    lines.push(line);
    rest = next.trim();
    limit = maxWidth;
    if (lines.length >= 48) {
      if (rest) lines[lines.length - 1] += "…";
      break;
    }
  }
  return lines.length ? lines : [""];
}

function mindItem(text, kind, children, prefix) {
  const style = MIND_KIND[kind];
  return {
    text: String(text || "").trim(),
    kind,
    prefix: prefix || "",
    children: children || [],
    ...style,
  };
}

function measureMind(node) {
  const font = mindFont(node);
  const prefixWidth = node.prefix ? textWidth(node.prefix, font) : 0;
  node.lines = wrapBody(node.text, font, node.maxText, node.maxText - prefixWidth);
  const widths = node.lines.map((line, index) => textWidth(line, font) + (index === 0 ? prefixWidth : 0));
  node.cardW = Math.ceil(Math.max(...widths, 24) + MIND_PAD_X * 2 + 6);
  node.cardH = Math.ceil(MIND_PAD_Y * 2 + node.lines.length * MIND_LINE);
  node.children.forEach(measureMind);
  const kidsH = node.children.reduce((sum, child) => sum + child.blockH, 0)
    + MIND_GAP_Y * Math.max(0, node.children.length - 1);
  node.kidsH = kidsH;
  node.blockH = Math.max(node.cardH, kidsH || 0);
  const kidsW = node.children.reduce((max, child) => Math.max(max, child.blockW), 0);
  node.blockW = node.cardW + (node.children.length ? MIND_GAP_X + kidsW : 0);
}

function placeMind(node, x, top) {
  node.x = x;
  node.y = top + (node.blockH - node.cardH) / 2;
  if (!node.children.length) return;
  let cursor = top + (node.blockH - node.kidsH) / 2;
  node.children.forEach((child) => {
    placeMind(child, x + node.cardW + MIND_GAP_X, cursor);
    cursor += child.blockH + MIND_GAP_Y;
  });
}

function drawMindNode(parent, node) {
  const radius = node.kind === "root" ? node.cardH / 2 : 12;
  const shape = svgEl("rect", {
    x: node.x,
    y: node.y,
    width: node.cardW,
    height: node.cardH,
    rx: radius,
    fill: node.fill,
    stroke: node.stroke,
    "stroke-width": node.stroke === "none" ? 0 : 1.5,
  });
  const text = svgEl("text", {
    fill: node.color,
    "font-family": MIND_FONT,
    "font-size": node.fontSize,
    "font-weight": node.fontWeight,
  });
  node.lines.forEach((line, index) => {
    const span = svgEl("tspan", {
      x: node.x + MIND_PAD_X,
      y: node.y + MIND_PAD_Y + node.fontSize * 0.85 + index * MIND_LINE,
    });
    if (index === 0 && node.prefix) {
      const prefix = svgEl("tspan", { fill: "#1777FF", "font-weight": 800 });
      prefix.textContent = node.prefix;
      span.append(prefix);
    }
    span.append(document.createTextNode(line));
    text.append(span);
  });
  parent.append(shape, text);
  node.children.forEach((child) => {
    const x1 = node.x + node.cardW;
    const y1 = node.y + node.cardH / 2;
    const x2 = child.x;
    const y2 = child.y + child.cardH / 2;
    const mid = (x1 + x2) / 2;
    parent.append(svgEl("path", {
      d: `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`,
      fill: "none",
      stroke: "#c5d4e8",
      "stroke-width": 2,
    }));
    drawMindNode(parent, child);
  });
}

function buildMindSvg(root) {
  measureMind(root);
  placeMind(root, MIND_MARGIN, MIND_MARGIN);
  const width = Math.ceil(root.blockW + MIND_MARGIN * 2);
  const height = Math.ceil(root.blockH + MIND_MARGIN * 2);
  const svg = svgEl("svg", {
    xmlns: SVG_NS,
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
  });
  drawMindNode(svg, root);
  svg.setAttribute("aria-label", root.text || "思维导图");
  return svg;
}

function renderMind(data) {
  mindPane.replaceChildren();
  current.mindSvg = null;
  const overview = String((data && data.overview) || "").trim();
  const chapters = ((data && data.chapters) || []).filter(
    (chapter) => chapter && (chapter.title || chapter.summary || chapter.start),
  );
  const highlights = ((data && data.highlights) || [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  if (!overview && !chapters.length && !highlights.length) {
    mindPane.append(el("p", "learn-status", "笔记里没有可以展开的结构"));
    return;
  }

  const branches = [];
  if (overview) branches.push(mindItem("概述", "branch", [mindItem(overview, "leaf")]));
  chapters.forEach((chapter) => {
    const leaves = chapter.summary ? [mindItem(chapter.summary, "leaf")] : [];
    branches.push(mindItem(chapter.title || "章节", "branch", leaves, `${chapter.start || "00:00"} `));
  });
  if (highlights.length) {
    branches.push(mindItem("要点", "branch", highlights.map((item) => mindItem(item, "leaf"))));
  }
  const svg = buildMindSvg(mindItem(current.title || "本视频", "root", branches));
  current.mindSvg = svg;

  const fullBtn = el("button", "btn-ghost", "全屏阅读");
  fullBtn.type = "button";
  fullBtn.addEventListener("click", openMindScreen);
  const saveBtn = el("button", "btn-ghost", "下载高清图");
  saveBtn.type = "button";
  saveBtn.addEventListener("click", () => {
    exportMindPng().catch(() => setLearnStatus("思维导图导出失败", true));
  });
  const bar = el("div", "mind-toolbar");
  bar.append(fullBtn, saveBtn);
  const scroller = el("div", "mindmap");
  scroller.append(svg.cloneNode(true));
  mindPane.append(bar, scroller);
}

function applyMindView() {
  const canvas = $("mindCanvas");
  if (!canvas) return;
  canvas.style.transform = `translate(${mindView.x}px, ${mindView.y}px) scale(${mindView.scale})`;
}

function fitMind() {
  const canvas = $("mindCanvas");
  const svg = canvas && canvas.querySelector("svg");
  if (!svg) return;
  const width = Number(svg.getAttribute("width")) || 1;
  const height = Number(svg.getAttribute("height")) || 1;
  const box = mindViewport.getBoundingClientRect();
  const scale = Math.min((box.width - 48) / width, (box.height - 48) / height, 2);
  mindView.scale = Math.max(0.2, scale);
  mindView.x = (box.width - width * mindView.scale) / 2;
  mindView.y = (box.height - height * mindView.scale) / 2;
  applyMindView();
}

function zoomMind(factor) {
  const box = mindViewport.getBoundingClientRect();
  const px = box.width / 2;
  const py = box.height / 2;
  const next = Math.min(3, Math.max(0.2, mindView.scale * factor));
  const ratio = next / mindView.scale;
  mindView.x = px - (px - mindView.x) * ratio;
  mindView.y = py - (py - mindView.y) * ratio;
  mindView.scale = next;
  applyMindView();
}

function openMindScreen() {
  if (!current.mindSvg) return;
  mindScreen.hidden = false;
  document.body.classList.add("mind-open");
  mindScreenTitle.textContent = current.title || "思维导图";
  mindViewport.replaceChildren();
  const canvas = el("div", "mind-canvas");
  canvas.id = "mindCanvas";
  canvas.append(current.mindSvg.cloneNode(true));
  mindViewport.append(canvas);
  requestAnimationFrame(fitMind);
}

function closeMindScreen() {
  if (!mindScreen || mindScreen.hidden) return;
  mindScreen.hidden = true;
  document.body.classList.remove("mind-open");
  mindViewport.replaceChildren();
  mindDrag = null;
}

function exportScale(width, height) {
  const maxDim = 8192;
  let scale = 3;
  while (scale > 1 && (width * scale > maxDim || height * scale > maxDim)) scale -= 0.5;
  return scale;
}

function exportMindPng() {
  const svg = current.mindSvg;
  if (!svg) return Promise.resolve();
  const width = Number(svg.getAttribute("width"));
  const height = Number(svg.getAttribute("height"));
  if (!width || !height) return Promise.reject(new Error("empty"));
  const scale = exportScale(width, height);
  const xml = new XMLSerializer().serializeToString(svg);
  const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((png) => {
        URL.revokeObjectURL(url);
        if (!png) {
          reject(new Error("empty"));
          return;
        }
        saveBlob(png, safeFileName(`${current.title || "视频"}-思维导图`, "png"));
        setLearnStatus("已开始下载思维导图");
        resolve(png);
      }, "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("image"));
    };
    image.src = url;
  });
}

function renderCues(cues, translatedText) {
  cuesPane.replaceChildren();
  if (translatedText) {
    const box = el("section", "learn-translate");
    box.append(el("h3", "", "译文"), el("p", "", translatedText));
    cuesPane.append(box);
  }
  (cues || []).forEach((cue) => {
    const row = el("div", "cue-row");
    row.append(el("time", "", cue.label || "00:00"), el("div", "", cue.text || ""));
    cuesPane.append(row);
  });
  if (!(cues || []).length && !translatedText) {
    cuesPane.append(el("p", "learn-status", "还没有字幕文本"));
  }
}

function renderVideo(data, fallbackUrl) {
  current.url = data.webpage_url || fallbackUrl;
  current.title = data.title;
  current.format = (data.formats && data.formats[0] && data.formats[0].format) || "bv*+ba/b";
  resetLearn(true);
  current.hasSubtitles = data.has_subtitles !== false;

  playlistPanel.hidden = true;
  resultPanel.hidden = false;
  workbench.hidden = false;
  progressWrap.hidden = true;

  $("thumb").src = data.thumbnail || "";
  $("thumb").alt = data.title || "视频封面";
  $("thumb").hidden = !data.thumbnail;
  $("thumb").onerror = () => {
    $("thumb").hidden = true;
  };
  $("resultTitle").textContent = data.title || "未命名视频";
  const bits = [data.uploader, formatDuration(data.duration), data.extractor]
    .filter(Boolean);
  if (data.has_subtitles === false) bits.push("无平台字幕，总结时将语音转写");
  $("resultMeta").textContent = bits.join(" · ");

  formatChips.innerHTML = "";
  (data.formats || []).forEach((item, index) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = item.label;
    btn.dataset.format = item.format;
    if (index === 0) btn.classList.add("active");
    btn.addEventListener("click", () => {
      current.format = item.format;
      formatChips.querySelectorAll("button").forEach((node) => node.classList.remove("active"));
      btn.classList.add("active");
    });
    formatChips.appendChild(btn);
  });
}

function renderPlaylist(data) {
  resultPanel.hidden = true;
  learnPanel.hidden = true;
  playlistPanel.hidden = false;
  workbench.hidden = false;
  playlistPanel.innerHTML = "";
  const heading = document.createElement("p");
  heading.textContent = `${data.title || "播放列表"} · 点选一条后解析`;
  heading.style.gridColumn = "1 / -1";
  heading.style.color = "#64748b";
  playlistPanel.appendChild(heading);

  (data.entries || []).forEach((entry) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.innerHTML = `<strong>${entry.title || "未命名"}</strong><br><span style="color:#64748b;font-size:12px">${formatDuration(entry.duration)}</span>`;
    btn.addEventListener("click", () => {
      urlInput.value = entry.url;
      parseUrl(entry.url);
    });
    playlistPanel.appendChild(btn);
  });
}

function setBusy(busy) {
  parseBtn.disabled = busy;
  heroLearnBtn.disabled = busy;
  summaryBtn.disabled = busy;
  translateBtn.disabled = busy;
  subtitleBtn.disabled = busy;
}

async function parseUrl(url, { autoLearn } = {}) {
  showHint("");
  setBusy(true);
  parseBtn.textContent = "解析中";
  if (autoLearn) heroLearnBtn.textContent = "总结中";
  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "解析失败");
    }
    if (data.type === "playlist") {
      renderPlaylist(data);
      if (autoLearn) showHint("这是播放列表，请先点选一条视频再总结");
    } else {
      renderVideo(data, url);
      if (data.cookie_warning) showHint(data.cookie_warning);
      if (autoLearn) await runSummary();
    }
    workbench.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showHint(error.message || "解析失败");
  } finally {
    setBusy(false);
    parseBtn.textContent = "免费解析";
    heroLearnBtn.textContent = "AI 总结";
  }
}

async function readSse(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .filter((part) => part.startsWith("data:"))
        .map((part) => part.replace(/^data:\s?/, ""))
        .join("");
      if (!line) continue;
      onEvent(JSON.parse(line));
    }
  }
}

async function runSummary() {
  if (!current.url) return;
  learnPanel.hidden = false;
  switchTab("notes");
  setLearnStatus(
    current.hasSubtitles === false
      ? "没有平台字幕，正在下载音频并用 Whisper 转写，可能需要几分钟…"
      : "正在抽取字幕并生成笔记…",
  );
  notesPane.replaceChildren(el("p", "", "处理中，请稍候…"));
  mindPane.replaceChildren();
  cuesPane.replaceChildren();
  copyMdBtn.disabled = true;
  exportMdBtn.disabled = true;
  downloadCueBtn.disabled = true;
  setBusy(true);
  learnPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const response = await fetch("/api/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: current.url, title: current.title }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "处理失败");
    }
    current.learnId = data.learn_id || "";
    current.markdown = data.markdown || "";
    current.cues = data.cues || [];
    renderNotes(data);
    renderMind(data);
    renderCues(data.cues);
    setLearnStatus(data.source === "whisper" ? "笔记来自语音转写" : "");
    copyMdBtn.disabled = !current.markdown;
    exportMdBtn.disabled = !current.markdown;
    syncCueDownload();
  } catch (error) {
    notesPane.replaceChildren();
    setLearnStatus(error.message || "处理失败", true);
  } finally {
    setBusy(false);
    syncCueDownload();
  }
}

parseForm.addEventListener("submit", (event) => {
  event.preventDefault();
  parseUrl(urlInput.value.trim());
});

heroLearnBtn.addEventListener("click", () => {
  const url = urlInput.value.trim();
  if (!url) {
    urlInput.focus();
    return;
  }
  parseUrl(url, { autoLearn: true });
});

downloadBtn.addEventListener("click", async () => {
  if (!current.url) return;
  downloadBtn.disabled = true;
  progressWrap.hidden = false;
  progressFill.style.width = "4%";
  progressText.textContent = "开始下载…";
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: current.url, format: current.format }),
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    await readSse(response, (event) => {
      if (event.status === "error") {
        throw new Error(event.message || "下载失败");
      }
      if (typeof event.percent === "number") {
        progressFill.style.width = `${event.percent}%`;
      }
      if (event.status === "processing") {
        progressText.textContent = "正在合并音视频…";
      } else if (event.status === "downloading") {
        progressText.textContent = `下载中 ${event.percent || 0}%`;
      } else if (event.status === "done" && event.job_id) {
        progressText.textContent = "完成，开始保存文件";
        const link = document.createElement("a");
        link.href = `/api/file/${event.job_id}`;
        link.download = event.filename || "video";
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
    });
  } catch (error) {
    progressText.textContent = error.message || "下载失败";
  } finally {
    downloadBtn.disabled = false;
  }
});

summaryBtn.addEventListener("click", () => {
  runSummary();
});

translateBtn.addEventListener("click", async () => {
  if (!current.url) return;
  learnPanel.hidden = false;
  switchTab("cues");
  setLearnStatus(
    current.hasSubtitles === false ? "没有平台字幕，正在转写后再翻译…" : "正在翻译字幕…",
  );
  setBusy(true);
  try {
    const response = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: current.url, target_lang: "zh" }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "处理失败");
    }
    renderCues(current.cues || [], data.text);
    setLearnStatus("");
    aiBox.hidden = true;
  } catch (error) {
    setLearnStatus(error.message || "处理失败", true);
  } finally {
    setBusy(false);
  }
});

document.querySelectorAll("#learnTabs button").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

copyMdBtn.addEventListener("click", async () => {
  if (!current.markdown) return;
  try {
    await navigator.clipboard.writeText(current.markdown);
    setLearnStatus("已复制 Markdown");
  } catch {
    setLearnStatus("复制失败，请手动全选导出文件", true);
  }
});

exportMdBtn.addEventListener("click", () => {
  if (!current.markdown) return;
  saveTextFile(
    current.markdown,
    safeFileName(current.title || "视频学习笔记", "md"),
    "text/markdown;charset=utf-8",
  );
});

async function downloadSubtitles() {
  if (!current.url && !current.learnId && !current.srt) return;
  if (current.srt) {
    saveTextFile(current.srt, safeFileName(current.title || "字幕", "srt"), "application/x-subrip;charset=utf-8");
    setLearnStatus("已开始下载字幕");
    return;
  }
  const reuse = Boolean(current.learnId);
  learnPanel.hidden = false;
  setLearnStatus(
    !reuse && current.hasSubtitles === false
      ? "没有平台字幕，正在下载音频并用 Whisper 转写，可能需要几分钟…"
      : "正在准备字幕文件…",
  );
  subtitleBtn.disabled = true;
  downloadCueBtn.disabled = true;
  try {
    const response = await fetch("/api/subtitles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: current.url, learn_id: current.learnId }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "字幕下载失败");
    }
    current.srt = data.srt || "";
    if (!current.srt.trim()) throw new Error("没有可用的字幕文本");
    if ((!current.cues || !current.cues.length) && Array.isArray(data.cues) && data.cues.length) {
      current.cues = data.cues;
      renderCues(data.cues);
      switchTab("cues");
    }
    saveTextFile(current.srt, safeFileName(current.title || "字幕", "srt"), "application/x-subrip;charset=utf-8");
    setLearnStatus(data.source === "whisper" ? "字幕来自语音转写，已开始下载" : "已开始下载字幕");
  } catch (error) {
    setLearnStatus(error.message || "字幕下载失败", true);
  } finally {
    subtitleBtn.disabled = false;
    syncCueDownload();
  }
}

subtitleBtn.addEventListener("click", () => {
  downloadSubtitles();
});

downloadCueBtn.addEventListener("click", () => {
  downloadSubtitles();
});

askForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = askInput.value.trim();
  if (!question) return;
  if (!current.learnId) {
    setLearnStatus("请先生成学习笔记，再追问", true);
    switchTab("notes");
    return;
  }
  askThread.append(el("div", "ask-msg user", question));
  askInput.value = "";
  askBtn.disabled = true;
  const waiting = el("div", "ask-msg assistant", "思考中…");
  askThread.append(waiting);
  askThread.scrollTop = askThread.scrollHeight;
  try {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ learn_id: current.learnId, question }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "追问失败");
    }
    waiting.textContent = data.text;
    setLearnStatus("");
  } catch (error) {
    waiting.textContent = error.message || "追问失败";
  } finally {
    askBtn.disabled = false;
    askThread.scrollTop = askThread.scrollHeight;
  }
});

menuBtn.addEventListener("click", () => {
  nav.classList.toggle("open");
});

document.querySelectorAll("[data-open-pro]").forEach((node) => {
  node.addEventListener("click", () => proModal.showModal());
});

$("mindFitBtn").addEventListener("click", fitMind);
$("mindZoomOutBtn").addEventListener("click", () => zoomMind(1 / 1.15));
$("mindZoomInBtn").addEventListener("click", () => zoomMind(1.15));
$("mindExportBtn").addEventListener("click", () => {
  exportMindPng().catch(() => setLearnStatus("思维导图导出失败", true));
});
$("mindCloseBtn").addEventListener("click", closeMindScreen);

mindViewport.addEventListener("wheel", (event) => {
  if (mindScreen.hidden) return;
  event.preventDefault();
  const box = mindViewport.getBoundingClientRect();
  const px = event.clientX - box.left;
  const py = event.clientY - box.top;
  const next = Math.min(3, Math.max(0.2, mindView.scale * (event.deltaY < 0 ? 1.1 : 0.9)));
  const ratio = next / mindView.scale;
  mindView.x = px - (px - mindView.x) * ratio;
  mindView.y = py - (py - mindView.y) * ratio;
  mindView.scale = next;
  applyMindView();
}, { passive: false });

function clearMindSelection() {
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed) selection.removeAllRanges();
}

mindPane.addEventListener("selectstart", (event) => {
  event.preventDefault();
});

mindViewport.addEventListener("selectstart", (event) => {
  event.preventDefault();
});

mindViewport.addEventListener("pointerdown", (event) => {
  if (mindScreen.hidden || event.button) return;
  event.preventDefault();
  clearMindSelection();
  mindDrag = { id: event.pointerId, x: event.clientX, y: event.clientY, ox: mindView.x, oy: mindView.y };
  mindViewport.setPointerCapture(event.pointerId);
});

mindViewport.addEventListener("pointermove", (event) => {
  if (!mindDrag || mindDrag.id !== event.pointerId) return;
  event.preventDefault();
  clearMindSelection();
  mindView.x = mindDrag.ox + event.clientX - mindDrag.x;
  mindView.y = mindDrag.oy + event.clientY - mindDrag.y;
  applyMindView();
});

function endMindDrag(event) {
  if (mindDrag && event.pointerId === mindDrag.id) mindDrag = null;
}

mindViewport.addEventListener("pointerup", endMindDrag);
mindViewport.addEventListener("pointercancel", endMindDrag);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMindScreen();
});

copyMdBtn.disabled = true;
exportMdBtn.disabled = true;
syncCueDownload();
