const $ = (id) => document.getElementById(id);

const parseForm = $("parseForm");
const urlInput = $("urlInput");
const parseBtn = $("parseBtn");
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
const menuBtn = $("menuBtn");
const nav = $("nav");
const proModal = $("proModal");

let current = {
  url: "",
  format: "",
  title: "",
};

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

async function readError(response) {
  try {
    const data = await response.json();
    return data.message || data.error || "请求失败";
  } catch {
    return "请求失败";
  }
}

function renderVideo(data, fallbackUrl) {
  current.url = data.webpage_url || fallbackUrl;
  current.title = data.title;
  current.format = (data.formats && data.formats[0] && data.formats[0].format) || "bv*+ba/b";

  playlistPanel.hidden = true;
  resultPanel.hidden = false;
  workbench.hidden = false;
  aiBox.hidden = true;
  progressWrap.hidden = true;

  $("thumb").src = data.thumbnail || "";
  $("thumb").alt = data.title || "视频封面";
  $("thumb").hidden = !data.thumbnail;
  $("thumb").onerror = () => {
    $("thumb").hidden = true;
  };
  $("resultTitle").textContent = data.title || "未命名视频";
  const bits = [data.uploader, formatDuration(data.duration), data.extractor]
    .filter(Boolean)
    .join(" · ");
  $("resultMeta").textContent = bits;

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

async function parseUrl(url) {
  showHint("");
  parseBtn.disabled = true;
  parseBtn.textContent = "解析中";
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
    } else {
      renderVideo(data, url);
    }
    workbench.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) {
    showHint(error.message || "解析失败");
  } finally {
    parseBtn.disabled = false;
    parseBtn.textContent = "免费解析";
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

parseForm.addEventListener("submit", (event) => {
  event.preventDefault();
  parseUrl(urlInput.value.trim());
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

async function runAi(path, body) {
  aiBox.hidden = false;
  aiBox.textContent = "处理中，请稍候…";
  summaryBtn.disabled = true;
  translateBtn.disabled = true;
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.message || "处理失败");
    }
    aiBox.textContent = data.text;
  } catch (error) {
    aiBox.textContent = error.message || "处理失败";
  } finally {
    summaryBtn.disabled = false;
    translateBtn.disabled = false;
  }
}

summaryBtn.addEventListener("click", () => {
  if (!current.url) return;
  runAi("/api/summary", { url: current.url });
});

translateBtn.addEventListener("click", () => {
  if (!current.url) return;
  runAi("/api/translate", { url: current.url, target_lang: "zh" });
});

menuBtn.addEventListener("click", () => {
  nav.classList.toggle("open");
});

document.querySelectorAll("[data-open-pro]").forEach((node) => {
  node.addEventListener("click", () => proModal.showModal());
});
