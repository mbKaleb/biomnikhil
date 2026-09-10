const promptEl = document.getElementById("prompt");
const runBtn = document.getElementById("run");
const stepsEl = document.getElementById("steps");
const messagesEl = document.getElementById("messages");
const attachHint = document.getElementById("attach-hint");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const uploadStatus = document.getElementById("upload-status");
const fileList = document.getElementById("file-list");
const sidebar = document.getElementById("sidebar");
const backdrop = document.getElementById("backdrop");
const menuBtn = document.getElementById("menu-btn");
const newChatBtn = document.getElementById("new-chat");
const chatListEl = document.getElementById("chat-list");

const chatNameEl = document.getElementById("chat-name");

let currentChatId = null;

function setChatName(name) {
  chatNameEl.textContent = name || "";
  chatNameEl.title = name || "";
}

// ---- sidebar open/close ----
function openSidebar() {
  sidebar.classList.add("open");
  backdrop.classList.add("show");
  refreshChats();
}
function closeSidebar() {
  sidebar.classList.remove("open");
  backdrop.classList.remove("show");
}
menuBtn.addEventListener("click", () =>
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar()
);
backdrop.addEventListener("click", closeSidebar);
document.getElementById("close-sidebar").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSidebar();
});

// ---- chat list ----
async function refreshChats() {
  try {
    const res = await fetch("/api/chats");
    const { chats } = await res.json();
    chatListEl.innerHTML = "";
    if (!chats || chats.length === 0) {
      chatListEl.innerHTML = '<li class="empty">No chats yet</li>';
      return;
    }
    for (const chat of chats) {
      chatListEl.appendChild(chatRow(chat));
      if (chat.id === currentChatId) setChatName(chat.title);
    }
  } catch {
    chatListEl.innerHTML = '<li class="empty">Could not load chats</li>';
  }
}

function relativeTime(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

function chatRow(chat) {
  const li = document.createElement("li");
  if (chat.id === currentChatId) li.classList.add("active");

  const bubble = document.createElement("span");
  bubble.className = "chat-bubble";
  bubble.textContent = "\u{1F4AC}";

  const meta = document.createElement("span");
  meta.className = "chat-meta";
  const title = document.createElement("span");
  title.className = "chat-title";
  title.textContent = chat.title;
  title.title = chat.title;
  const time = document.createElement("span");
  time.className = "chat-time";
  time.textContent = relativeTime(chat.updated);
  meta.append(title, time);

  const actions = document.createElement("span");
  actions.className = "chat-actions";

  const renameBtn = document.createElement("button");
  renameBtn.textContent = "✎";
  renameBtn.title = "Rename";
  renameBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    startRename(li, chat, meta);
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.textContent = "\u{1F5D1}️";
  deleteBtn.title = "Delete";
  deleteBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${chat.title}"?`)) return;
    await fetch(`/api/chats/${chat.id}`, { method: "DELETE" });
    if (chat.id === currentChatId) startNewChat();
    refreshChats();
  });

  actions.append(renameBtn, deleteBtn);
  li.append(bubble, meta, actions);
  li.addEventListener("click", () => selectChat(chat.id));
  return li;
}

function startRename(li, chat, metaEl) {
  const input = document.createElement("input");
  input.value = chat.title;
  li.replaceChild(input, metaEl);
  input.focus();
  input.select();
  const done = async (save) => {
    const title = input.value.trim();
    if (save && title && title !== chat.title) {
      await fetch(`/api/chats/${chat.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
    }
    refreshChats();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") done(true);
    if (e.key === "Escape") done(false);
  });
  input.addEventListener("blur", () => done(true));
  input.addEventListener("click", (e) => e.stopPropagation());
}

// ---- messages ----
function addMessage(role, text, isError = false) {
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  div.textContent = text;
  messagesEl.appendChild(div);
  div.scrollIntoView({ block: "end" });
  return div;
}

async function selectChat(chatId) {
  try {
    const res = await fetch(`/api/chats/${chatId}`);
    if (!res.ok) throw new Error("unknown chat");
    const chat = await res.json();
    currentChatId = chatId;
    setChatName(chat.title);
    messagesEl.innerHTML = "";
    stepsEl.style.display = "none";
    for (const m of chat.messages) {
      addMessage(m.role, m.content, m.content.startsWith("[error]"));
    }
    document.querySelector('.tabs button[data-tab="task"]').click();
    closeSidebar();
    refreshChats();
  } catch {
    refreshChats();
  }
}

function startNewChat() {
  currentChatId = null;
  setChatName("");
  messagesEl.innerHTML = "";
  stepsEl.style.display = "none";
  stepsEl.textContent = "";
  promptEl.focus();
}
newChatBtn.addEventListener("click", () => {
  startNewChat();
  closeSidebar();
  document.querySelector('.tabs button[data-tab="task"]').click();
});

// ---- tabs ----
document.querySelectorAll(".tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "files") refreshFiles();
  });
});

// ---- files ----
const attached = new Set();

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function updateAttachHint() {
  if (attached.size === 0) {
    attachHint.hidden = true;
  } else {
    attachHint.hidden = false;
    attachHint.textContent = `Attached: ${[...attached].join(", ")}`;
  }
}

function fileIcon(name) {
  const ext = name.split(".").pop().toLowerCase();
  if (["csv", "tsv", "xlsx", "xls"].includes(ext)) return "\u{1F4CA}";
  if (["png", "jpg", "jpeg", "gif", "svg", "tif", "tiff"].includes(ext)) return "\u{1F5BC}️";
  if (["fa", "fasta", "fastq", "fq", "vcf", "bam", "sam", "bed", "gff"].includes(ext)) return "\u{1F9EC}";
  if (["pdf"].includes(ext)) return "\u{1F4D5}";
  if (["zip", "gz", "tar", "bz2"].includes(ext)) return "\u{1F5DC}️";
  return "\u{1F4C4}";
}

async function refreshFiles() {
  try {
    const res = await fetch("/api/files");
    const { files } = await res.json();
    fileList.innerHTML = "";
    for (const f of files || []) {
      const li = document.createElement("li");

      const icon = document.createElement("span");
      icon.className = "file-icon";
      icon.textContent = fileIcon(f.name);

      const meta = document.createElement("span");
      meta.className = "meta";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = f.name;
      name.title = f.name;
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = formatSize(f.size);
      meta.append(name, size);

      const toggle = document.createElement("button");
      toggle.className = "attach-toggle";
      const render = () => {
        const on = attached.has(f.name);
        toggle.classList.toggle("on", on);
        toggle.textContent = on ? "Attached ✓" : "Attach";
      };
      toggle.addEventListener("click", () => {
        if (attached.has(f.name)) attached.delete(f.name);
        else attached.add(f.name);
        render();
        updateAttachHint();
      });
      render();

      li.append(icon, meta, toggle);
      fileList.appendChild(li);
    }
    if (!files || files.length === 0) {
      fileList.innerHTML = '<li class="empty">No files uploaded yet</li>';
    }
  } catch {
    fileList.innerHTML = '<li class="empty">Could not load file list</li>';
  }
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  uploadStatus.textContent = "Uploading…";
  try {
    const res = await fetch("/api/files", { method: "POST", body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    uploadStatus.textContent = `Uploaded: ${data.saved.join(", ")}`;
    for (const name of data.saved) attached.add(name);
    updateAttachHint();
    await refreshFiles();
  } catch (err) {
    uploadStatus.textContent = `Upload failed: ${err.message || err}`;
  }
}

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  uploadFiles([...fileInput.files]);
  fileInput.value = "";
});
["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));

// ---- task run ----
runBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) return;

  runBtn.disabled = true;
  addMessage("user", prompt);
  promptEl.value = "";
  stepsEl.style.display = "block";
  stepsEl.textContent = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, files: [...attached], chat_id: currentChatId }),
    });
    const { task_id, chat_id, error } = await res.json();
    if (error) throw new Error(error);
    currentChatId = chat_id;
    refreshChats();

    const source = new EventSource(`/api/tasks/${task_id}/stream`);
    source.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.step) {
        stepsEl.textContent += `[${msg.step.kind}] ${msg.step.text}\n`;
        stepsEl.scrollTop = stepsEl.scrollHeight;
      }
      if (msg.status) {
        source.close();
        runBtn.disabled = false;
        stepsEl.style.display = "none";
        if (msg.status === "done") {
          addMessage("assistant", msg.result);
        } else {
          addMessage("assistant", msg.error || "task failed", true);
        }
      }
      if (msg.error && !msg.status) {
        source.close();
        runBtn.disabled = false;
      }
    };
    source.onerror = () => {
      source.close();
      runBtn.disabled = false;
    };
  } catch (err) {
    runBtn.disabled = false;
    addMessage("assistant", String(err), true);
  }
});

// ---- data lake first-run modal ----
const dlModal = document.getElementById("dl-modal");
const dlFill = document.getElementById("dl-fill");
const dlBytes = document.getElementById("dl-bytes");
const dlPercent = document.getElementById("dl-percent");
const dlFiles = document.getElementById("dl-files");
const dlEta = document.getElementById("dl-eta");
const dlNote = document.getElementById("dl-note");
let dlTimer = null;
let dlLastSample = null; // {t, bytes} for the transfer-rate estimate

function formatBytes(n) {
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function renderDl(status) {
  const pct = status.percent;
  if (status.bytes > 0) {
    dlFill.classList.remove("indeterminate");
    dlFill.style.width = `${Math.max(pct, 1.5)}%`;
  } else {
    dlFill.classList.add("indeterminate");
  }
  dlBytes.textContent = `${formatBytes(status.bytes)} / ${formatBytes(status.expected_bytes)}`;
  dlPercent.textContent = `${pct}%`;
  dlFiles.textContent = `${status.files} files`;

  const now = Date.now();
  if (dlLastSample && status.bytes > dlLastSample.bytes) {
    const rate = ((status.bytes - dlLastSample.bytes) / (now - dlLastSample.t)) * 1000;
    const remaining = (status.expected_bytes - status.bytes) / rate;
    if (rate > 0 && remaining > 0 && remaining < 86400) {
      const m = Math.floor(remaining / 60);
      dlEta.textContent = m >= 1 ? `~${m}m left` : "almost done";
    }
  }
  dlLastSample = { t: now, bytes: status.bytes };
}

function stopDlPolling() {
  if (dlTimer) clearInterval(dlTimer);
  dlTimer = null;
}

function pollDl() {
  stopDlPolling();
  dlModal.classList.add("show", "downloading");
  dlTimer = setInterval(async () => {
    try {
      const status = await fetch("/api/datalake").then((r) => r.json());
      renderDl(status);
      if (status.state === "ready") {
        stopDlPolling();
        dlFill.style.width = "100%";
        dlPercent.textContent = "100%";
        dlNote.textContent = "Data lake ready — you're all set.";
        setTimeout(() => dlModal.classList.remove("show", "downloading"), 1600);
      } else if (status.state === "error") {
        stopDlPolling();
        dlModal.classList.remove("downloading");
        dlNote.textContent = `Download failed: ${status.error}`;
      }
    } catch {
      /* transient poll failure — keep trying */
    }
  }, 1500);
}

document.getElementById("dl-start").addEventListener("click", async () => {
  dlNote.textContent = "";
  await fetch("/api/datalake/download", { method: "POST" });
  pollDl();
});
document.getElementById("dl-later").addEventListener("click", () => {
  dlModal.classList.remove("show");
});

(async function checkDatalake() {
  try {
    const status = await fetch("/api/datalake").then((r) => r.json());
    if (status.state === "downloading") {
      renderDl(status);
      pollDl();
    } else if (status.state === "missing" || status.state === "partial") {
      if (status.state === "partial") {
        dlNote.textContent = `Found a partial download (${formatBytes(status.bytes)}) — it will resume.`;
      }
      dlModal.classList.add("show");
    }
  } catch {
    /* health endpoint down — nothing to do */
  }
})();

refreshFiles();
refreshChats();
