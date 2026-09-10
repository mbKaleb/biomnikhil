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
const traceEl = document.getElementById("trace");

// ---- terminal-style trace panel ----
// The live footer sits below the last step during a run: a one-line status
// ("running code…") plus the blinking cursor.
const traceLive = document.createElement("div");
traceLive.className = "trace-live";
const traceStatusEl = document.createElement("span");
traceStatusEl.className = "status";
const traceCursor = document.createElement("span");
traceCursor.className = "cursor";
traceLive.append(traceCursor, traceStatusEl);

function setTraceStatus(text) {
  traceStatusEl.textContent = text;
}

// Elapsed-run ticker in the trace header. runT0 starts at "now" but gets
// corrected backwards by the first replayed SSE step, so a re-attach after
// a page refresh still shows time since the run actually began.
const elapsedEl = document.getElementById("run-elapsed");
let runT0 = null;
let runTicker = null;

function renderElapsed() {
  if (!runT0) return;
  const s = Math.max(0, Math.floor((Date.now() - runT0) / 1000));
  elapsedEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function startElapsed() {
  runT0 = Date.now();
  clearInterval(runTicker);
  runTicker = setInterval(renderElapsed, 1000);
  renderElapsed();
}

function stopElapsed() {
  clearInterval(runTicker);
  runTicker = null;
  runT0 = null;
  elapsedEl.textContent = "";
}

// "New output below" pill: the panel deliberately stops auto-following
// when the user scrolls up — this is the way back down.
const jumpPill = document.createElement("button");
jumpPill.id = "jump-pill";
jumpPill.textContent = "↓ new output";
jumpPill.hidden = true;
jumpPill.addEventListener("click", () => {
  stepsEl.scrollTop = stepsEl.scrollHeight;
  jumpPill.hidden = true;
});

function nearBottomNow() {
  return stepsEl.scrollHeight - stepsEl.scrollTop - stepsEl.clientHeight < 60;
}
traceEl.appendChild(jumpPill);
stepsEl.addEventListener("scroll", () => {
  if (nearBottomNow()) jumpPill.hidden = true;
});

// Resizable trace panel: drag the left edge; width persists across reloads.
{
  const saved = localStorage.getItem("traceWidth");
  if (saved) traceEl.style.width = `min(${saved}px, 85vw)`;
  const resizer = document.getElementById("trace-resizer");
  resizer.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    resizer.setPointerCapture(e.pointerId);
    traceEl.classList.add("resizing");
    const onMove = (ev) => {
      const w = Math.min(Math.max(window.innerWidth - ev.clientX, 260), window.innerWidth * 0.85);
      traceEl.style.width = `${w}px`;
    };
    const onUp = () => {
      traceEl.classList.remove("resizing");
      localStorage.setItem("traceWidth", parseInt(traceEl.style.width, 10) || 400);
      resizer.removeEventListener("pointermove", onMove);
      resizer.removeEventListener("pointerup", onUp);
    };
    resizer.addEventListener("pointermove", onMove);
    resizer.addEventListener("pointerup", onUp);
  });
}

function traceOpen() {
  traceEl.classList.add("open");
}
function traceStart() {
  // Append to the chat's existing trace history (it persists per chat)
  // rather than wiping it — matches what a reload would show.
  traceLive.remove();
  setTraceStatus("waiting on model…");
  stepsEl.appendChild(traceLive);
  traceEl.classList.add("open", "running");
  startElapsed();
  stepsEl.scrollTop = stepsEl.scrollHeight;
}
// Strip biomni's log noise before showing a step in the terminal panel.
function cleanTrace(text) {
  // The retry nag biomni sends itself when the model misformats — noise.
  if (text.includes("Each response must include thinking process")) return null;
  const out = text
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      if (/^=+ (Ai|Human|System) Message =+$/.test(t)) return false; // banners
      if (t === "parsing error...") return false;
      return true;
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return out || null;
}

// Split a cleaned agent message on its <execute>/<observation>/<solution>
// tags into typed segments, so each renders as its own block.
function parseSegments(text) {
  const segs = [];
  const re = /<(execute|observation|solution)>([\s\S]*?)(?:<\/\1>|$)/g;
  let cursor = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    const before = text.slice(cursor, m.index).trim();
    if (before) segs.push({ type: "thought", text: before });
    const inner = m[2].trim();
    if (inner) segs.push({ type: m[1], text: inner });
    cursor = re.lastIndex;
  }
  const tail = text.slice(cursor).trim();
  if (tail) segs.push({ type: "thought", text: tail });
  return segs;
}

const SEG_LABELS = { execute: "code", observation: "output", solution: "answer" };

// What the agent is doing next, inferred from the segment it just emitted.
const NEXT_STATUS = {
  execute: "running code…",
  observation: "waiting on model…",
  thought: "thinking…",
  solution: "finishing up…",
};

function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// Minimal Python tokenizer for the code segments: comments, strings,
// keywords, and numbers get a colored span; everything else is plain text.
// Built with DOM nodes, never innerHTML, so agent output can't inject markup.
const PY_TOKEN = new RegExp(
  [
    '#[^\\n]*', // comment
    '"""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'', // triple-quoted string
    '"(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'', // string
    '\\b(?:def|class|import|from|return|if|elif|else|for|while|in|is|not|and|or|try|except|finally|with|as|lambda|yield|pass|break|continue|raise|global|del|assert|None|True|False)\\b', // keyword
    '\\b\\d+(?:\\.\\d+)?(?:e[+-]?\\d+)?\\b', // number
  ].join("|"),
  "g"
);

function highlightCode(text, target) {
  let cursor = 0;
  let m;
  PY_TOKEN.lastIndex = 0;
  while ((m = PY_TOKEN.exec(text)) !== null) {
    if (m.index > cursor) target.appendChild(document.createTextNode(text.slice(cursor, m.index)));
    const tok = m[0];
    const span = document.createElement("span");
    span.className =
      tok[0] === "#" ? "tok-c"
      : tok[0] === '"' || tok[0] === "'" ? "tok-s"
      : /^\d/.test(tok) ? "tok-n"
      : "tok-k";
    span.textContent = tok;
    target.appendChild(span);
    cursor = PY_TOKEN.lastIndex;
  }
  if (cursor < text.length) target.appendChild(document.createTextNode(text.slice(cursor)));
}

// DataFrame/table-shaped output: several lines whose columns are separated
// by runs of 2+ spaces. Wrapping such lines destroys the alignment, so
// these render with preserved whitespace and a horizontal scrollbar.
function isTabular(text) {
  const lines = text.split("\n");
  if (lines.length < 3) return false;
  const columnish = lines.filter((l) => /\S {2,}\S/.test(l)).length;
  return columnish >= Math.min(3, lines.length - 1) && columnish >= lines.length / 2;
}

function traceLine(kind, text, ts) {
  const cleaned = kind === "agent" ? cleanTrace(text) : text;
  if (cleaned === null) return;
  const ln = document.createElement("div");
  ln.className = `ln ${kind}`;
  let lastSegType = null;
  if (kind === "run_start" || kind === "run_end") {
    // Run boundary divider: ▶ at start, outcome glyph at end.
    const icons = { done: "■", failed: "✕", stopped: "⊘" };
    const outcome = kind === "run_end" ? cleaned.split(" ", 1)[0] : null;
    if (outcome) ln.classList.add(outcome);
    const icon = document.createElement("span");
    icon.className = "run-icon";
    icon.textContent = kind === "run_start" ? "▶" : icons[outcome] || "■";
    const label = document.createElement("span");
    label.className = "run-label";
    label.textContent =
      kind === "run_start"
        ? `run — "${cleaned.length > 80 ? cleaned.slice(0, 80) + "…" : cleaned}"`
        : `run ${cleaned}`;
    ln.append(icon, label);
    if (ts) {
      const time = document.createElement("span");
      time.className = "ts";
      time.textContent = fmtTime(ts);
      ln.appendChild(time);
    }
  } else if (kind !== "agent") {
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = (ts ? `${fmtTime(ts)} ` : "") + `[${kind}] `;
    ln.append(k, document.createTextNode(cleaned));
  } else {
    // Each agent message is one numbered step; the header carries the
    // step count and the time the step arrived.
    const head = document.createElement("div");
    head.className = "ln-head";
    const stepN = document.createElement("span");
    stepN.className = "step-n";
    stepN.textContent = `step ${stepsEl.querySelectorAll(".ln.agent").length + 1}`;
    head.appendChild(stepN);
    if (ts) {
      const time = document.createElement("span");
      time.className = "ts";
      time.textContent = fmtTime(ts);
      head.appendChild(time);
    }
    ln.appendChild(head);
    for (const seg of parseSegments(cleaned)) {
      lastSegType = seg.type;
      const el = document.createElement("div");
      el.className = `seg seg-${seg.type}`;
      if (seg.type === "observation" && isTabular(seg.text)) {
        el.classList.add("tabular");
      }
      if (
        seg.type === "observation" &&
        /Traceback \(most recent call last\)|(?:^|\n)\s*[\w.]*(?:Error|Exception):/.test(seg.text)
      ) {
        el.classList.add("err"); // failures should jump out mid-scroll
        ln.classList.add("has-err");
      }
      const isObs = seg.type === "observation";
      if (SEG_LABELS[seg.type]) {
        const label = document.createElement("span");
        label.className = "seg-label";
        label.textContent = SEG_LABELS[seg.type];
        el.appendChild(label);
      }
      const body = document.createElement("div");
      body.className = "seg-body";
      if (isObs && /\b(NaN|None|nan|<NA>)\b/.test(seg.text)) {
        // Dim missing-value tokens so real data stands out in tables.
        for (const part of seg.text.split(/(\bNaN\b|\bNone\b|\bnan\b|<NA>)/)) {
          if (/^(NaN|None|nan|<NA>)$/.test(part)) {
            const dim = document.createElement("span");
            dim.className = "nan";
            dim.textContent = part;
            body.appendChild(dim);
          } else if (part) {
            body.appendChild(document.createTextNode(part));
          }
        }
      } else if (seg.type === "execute") {
        highlightCode(seg.text, body);
      } else {
        body.appendChild(document.createTextNode(seg.text));
      }
      el.appendChild(body);
      // Giant outputs (raw API JSON, molfiles) drown the trace — clamp
      // them behind an expander.
      if (seg.text.length > 1200 || seg.text.split("\n").length > 25) {
        el.classList.add("clamped");
        const toggle = document.createElement("button");
        toggle.className = "seg-toggle";
        const size =
          seg.text.length > 1024
            ? `${(seg.text.length / 1024).toFixed(1)} KB`
            : `${seg.text.length} chars`;
        toggle.textContent = `▾ show all (${size})`;
        toggle.addEventListener("click", () => {
          const clamped = el.classList.toggle("clamped");
          toggle.textContent = clamped ? `▾ show all (${size})` : "▴ collapse";
        });
        el.appendChild(toggle);
      }
      ln.appendChild(el);
    }
    if (!ln.querySelector(".seg")) return; // header alone isn't worth a line
  }
  // Follow the tail only if the user is already at (or near) the bottom;
  // if they've scrolled up to read something, don't yank them back down —
  // offer the "new output" pill instead.
  const nearBottom = nearBottomNow();
  // The live footer is only in the DOM during a run; replayed history
  // (loaded from a chat) appends at the end.
  const live = traceLive.parentNode === stepsEl;
  stepsEl.insertBefore(ln, live ? traceLive : null);
  if (live && lastSegType) setTraceStatus(NEXT_STATUS[lastSegType] || "thinking…");
  if (nearBottom) stepsEl.scrollTop = stepsEl.scrollHeight;
  else jumpPill.hidden = false;
  if (ln.classList.contains("has-err")) updateErrCount();
}

// ---- all/errors tabs under the terminal ----
const errCountEl = document.getElementById("err-count");
function updateErrCount() {
  errCountEl.textContent = stepsEl.querySelectorAll(".ln.has-err").length;
}
document.querySelectorAll(".trace-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".trace-tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    traceEl.classList.toggle("filter-errors", btn.dataset.traceFilter === "errors");
    stepsEl.scrollTop = stepsEl.scrollHeight;
  });
});
function traceEnd() {
  traceEl.classList.remove("running");
  traceLive.remove();
  stopElapsed();
}
document.getElementById("trace-btn").addEventListener("click", () =>
  traceEl.classList.toggle("open")
);
document.getElementById("close-trace").addEventListener("click", () =>
  traceEl.classList.remove("open")
);

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
  li.append(meta, actions);
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
  if (role === "user") {
    const retry = document.createElement("button");
    retry.className = "retry-btn";
    retry.title = "Retry this prompt";
    retry.textContent = "↻";
    retry.addEventListener("click", () => runTask(text));
    div.appendChild(retry);
  }
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
    localStorage.setItem("lastChatId", chatId);
    setChatName(chat.title);
    messagesEl.innerHTML = "";
    stepsEl.innerHTML = "";
    updateErrCount();
    for (const m of chat.messages) {
      if (m.role === "trace") {
        traceLine("agent", m.content, m.created); // replay into the terminal panel
      } else if (m.role === "run_start" || m.role === "run_end") {
        traceLine(m.role, m.content, m.created); // run boundary markers
      } else {
        addMessage(m.role, m.content, m.content.startsWith("[error]"));
      }
    }
    // Re-attach to an in-flight run (e.g. after a page refresh) so the
    // running indicator, stop button, and live trace come back.
    try {
      const { task_id } = await fetch(`/api/chats/${chatId}/active`).then((r) => r.json());
      if (task_id && task_id !== currentTaskId) {
        // The SSE stream replays the run's steps from the start, and this
        // chat's persisted trace already contains them — reset the panel
        // so the run isn't shown twice.
        stepsEl.innerHTML = "";
    updateErrCount();
        traceStart();
        attachStream(task_id);
      }
    } catch {
      /* active-task lookup is best-effort */
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
  stepsEl.innerHTML = "";
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

      const remove = document.createElement("button");
      remove.className = "file-remove";
      remove.textContent = "✕";
      remove.title = `Remove ${f.name}`;
      remove.addEventListener("click", async () => {
        remove.disabled = true;
        try {
          const res = await fetch(`/api/files/${encodeURIComponent(f.name)}`, { method: "DELETE" });
          if (!res.ok) throw new Error();
          attached.delete(f.name);
          updateAttachHint();
          await refreshFiles();
        } catch {
          remove.disabled = false;
        }
      });

      li.append(icon, meta, toggle, remove);
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
runBtn.addEventListener("click", () => {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  promptEl.value = "";
  runTask(prompt);
});

const stopBtn = document.getElementById("stop");
let currentTaskId = null;

stopBtn.addEventListener("click", async () => {
  if (!currentTaskId) return;
  stopBtn.disabled = true;
  try {
    await fetch(`/api/tasks/${currentTaskId}/cancel`, { method: "POST" });
  } catch {
    /* the SSE stream will surface the outcome either way */
  }
});

function setRunning(running) {
  runBtn.disabled = running;
  stopBtn.hidden = !running;
  stopBtn.disabled = false;
  if (!running) currentTaskId = null;
}

async function runTask(prompt) {
  if (runBtn.disabled) return; // one task at a time

  setRunning(true);
  addMessage("user", prompt);
  traceStart();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, files: [...attached], chat_id: currentChatId }),
    });
    const { task_id, chat_id, error } = await res.json();
    if (error) throw new Error(error);
    currentChatId = chat_id;
    localStorage.setItem("lastChatId", chat_id);
    refreshChats();
    attachStream(task_id);
  } catch (err) {
    setRunning(false);
    traceEnd();
    addMessage("assistant", String(err), true);
  }
}

// Subscribe to a task's SSE stream and drive the running UI (trace lines,
// stop button, live indicator). Used for fresh runs and for re-attaching
// to an in-flight task after a page refresh or chat switch.
function attachStream(taskId) {
  currentTaskId = taskId;
  setRunning(true);
  const source = new EventSource(`/api/tasks/${taskId}/stream`);
  source.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.step) {
      // SSE replays a run's steps from the start, so the earliest step's
      // timestamp is when the run truly began — sync the elapsed ticker.
      const ms = msg.step.t * 1000;
      if (runT0 && ms < runT0) runT0 = ms;
      traceLine(msg.step.kind, msg.step.text, msg.step.t);
    }
    if (msg.status) {
      source.close();
      setRunning(false);
      traceEnd();
      if (msg.status === "done") {
        addMessage("assistant", msg.result);
        traceEl.classList.remove("open");
      } else {
        addMessage("assistant", msg.error || "task failed", true);
      }
    }
    if (msg.error && !msg.status) {
      source.close();
      setRunning(false);
      traceEnd();
    }
  };
  source.onerror = () => {
    source.close();
    setRunning(false);
    traceEnd();
  };
}

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

// Restore the chat that was open before the last refresh (which also
// re-attaches to its in-flight run, if one is still going).
{
  const last = localStorage.getItem("lastChatId");
  if (last) selectChat(last);
}
