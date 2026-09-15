const promptEl = document.getElementById("prompt");
const runBtn = document.getElementById("run");
const stepsEl = document.getElementById("steps");
const messagesEl = document.getElementById("messages");
const attachHint = document.getElementById("attach-hint");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const uploadStatus = document.getElementById("upload-status");
const fileList = document.getElementById("file-list");
const attachAllBtn = document.getElementById("attach-all");
const fileSearchEl = document.getElementById("file-search");
const fileSortEl = document.getElementById("file-sort");
const fileCountEl = document.getElementById("file-count");
const previewModal = document.getElementById("preview-modal");
const previewIcon = document.getElementById("preview-icon");
const previewName = document.getElementById("preview-name");
const previewBody = document.getElementById("preview-body");
const previewDownload = document.getElementById("preview-download");
const previewClose = document.getElementById("preview-close");
const confirmModalEl = document.getElementById("confirm-modal");
const confirmTitleEl = document.getElementById("confirm-title");
const confirmMessageEl = document.getElementById("confirm-message");
const confirmCancelBtn = document.getElementById("confirm-cancel");
const confirmOkBtn = document.getElementById("confirm-ok");
const outputListEl = document.getElementById("output-list");
const outputCountEl = document.getElementById("output-count");
const outputSearchEl = document.getElementById("output-search");
const outputBadgeEl = document.getElementById("output-count-badge");
const outputDownloadAllBtn = document.getElementById("output-download-all");
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
jumpPill.append(faIcon("arrow-down"), document.createTextNode(" new output"));
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
    // Run boundary divider: play icon at start, outcome icon at end.
    const icons = { done: "square", failed: "xmark", stopped: "ban" };
    const outcome = kind === "run_end" ? cleaned.split(" ", 1)[0] : null;
    if (outcome) ln.classList.add(outcome);
    const icon = document.createElement("span");
    icon.className = "run-icon";
    icon.appendChild(faIcon(kind === "run_start" ? "play" : icons[outcome] || "square"));
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
      // them behind an expander. Thinking/plan text is exempt: reasoning
      // models (gpt-5-mini etc.) routinely write long plans, and that's
      // exactly the content people open the trace panel to read — auto-
      // collapsing it defeats the point.
      if (seg.type !== "thought" && (seg.text.length > 1200 || seg.text.split("\n").length > 25)) {
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
document.querySelectorAll(".trace-tabs button[data-trace-filter]").forEach((btn) => {
  btn.addEventListener("click", () => {
    document
      .querySelectorAll(".trace-tabs button[data-trace-filter]")
      .forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    traceEl.classList.toggle("filter-errors", btn.dataset.traceFilter === "errors");
    stepsEl.scrollTop = stepsEl.scrollHeight;
  });
});

const copyTraceBtn = document.getElementById("copy-trace");
copyTraceBtn.addEventListener("click", async () => {
  // .innerText (not .textContent) so it naturally respects whatever the
  // active all/errors filter has hidden via CSS — hidden elements don't
  // contribute to innerText, no need to duplicate the filter logic here.
  await copyToClipboard(stepsEl.innerText);
  copyTraceBtn.classList.add("copied");
  copyTraceBtn.replaceChildren(faIcon("check"), document.createTextNode(" copied"));
  setTimeout(() => {
    copyTraceBtn.classList.remove("copied");
    copyTraceBtn.replaceChildren(faIcon("copy"), document.createTextNode(" copy"));
  }, 1200);
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
    if (!res.ok) throw new Error(`server returned HTTP ${res.status}`);
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
  } catch (err) {
    // A network-level failure (server unreachable) throws a generic
    // "Failed to fetch" here — distinguish that from a real HTTP error so
    // the message actually points at the right thing to check.
    const detail = err instanceof TypeError
      ? "can't reach the server — is it still running?"
      : err.message || String(err);
    chatListEl.innerHTML = `<li class="empty">Could not load chats: ${detail}</li>`;
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
  renameBtn.replaceChildren(faIcon("pen"));
  renameBtn.title = "Rename";
  renameBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    startRename(li, chat, meta);
  });

  const deleteBtn = document.createElement("button");
  deleteBtn.replaceChildren(faIcon("trash"));
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

// Copies text to the clipboard, falling back to the old select-and-copy
// trick when the Clipboard API is unavailable (non-secure context, no
// permission) rather than silently doing nothing.
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
    } catch {
      /* nothing more we can do */
    }
    ta.remove();
  }
}

// ---- messages ----
// Builds a Font Awesome <i> element — vendored locally (app/static/
// fontawesome/), not loaded from a CDN, so icons still render offline.
function faIcon(cls) {
  const i = document.createElement("i");
  i.className = `fa-solid fa-${cls}`;
  return i;
}

// Returns a fa-* class name (without the "fa-" prefix) for a file's icon,
// by extension.
function fileIconClass(name) {
  const ext = name.split(".").pop().toLowerCase();
  if (["csv", "tsv", "xlsx", "xls"].includes(ext)) return "file-csv";
  if (["png", "jpg", "jpeg", "gif", "svg", "tif", "tiff"].includes(ext)) return "image";
  if (["fa", "fasta", "fastq", "fq", "vcf", "bam", "sam", "bed", "gff"].includes(ext)) return "dna";
  if (["pdf"].includes(ext)) return "file-pdf";
  if (["html", "htm"].includes(ext)) return "globe";
  if (["zip", "gz", "tar", "bz2"].includes(ext)) return "file-zipper";
  return "file";
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// Inline preview vs download-only per extension — text-ish/image/pdf types
// render in the in-app preview modal; anything else (zips, binaries)
// should just download since the browser can't show it usefully anyway.
const VIEWABLE_EXT = new Set([
  "csv", "tsv", "txt", "json", "md", "log",
  "png", "jpg", "jpeg", "gif", "svg", "pdf", "html", "htm",
]);

// Minimal CSV/TSV split — good enough for the well-formed exports this
// app generates itself; doesn't attempt full RFC 4180 quoting edge cases.
function renderDelimited(text, delim) {
  const rows = text.replace(/\r\n?/g, "\n").split("\n").filter((l) => l.length);
  const table = document.createElement("table");
  rows.slice(0, 500).forEach((row, i) => {
    const tr = document.createElement("tr");
    for (const cell of row.split(delim)) {
      const el = document.createElement(i === 0 ? "th" : "td");
      el.textContent = cell;
      tr.appendChild(el);
    }
    table.appendChild(tr);
  });
  if (rows.length > 500) {
    const note = document.createElement("div");
    note.className = "preview-loading";
    note.textContent = `Showing first 500 of ${rows.length} rows — download for the full file.`;
    previewBody.appendChild(note);
  }
  return table;
}

function closePreview() {
  previewModal.classList.remove("show");
  previewBody.innerHTML = "";
}

// Generic confirm modal for destructive actions — resolves true/false.
// Only one can be open at a time, which is all this app ever needs.
let _confirmResolve = null;
function confirmModal(message, { title = "Delete file?", confirmLabel = "Delete" } = {}) {
  confirmTitleEl.textContent = title;
  confirmMessageEl.textContent = message;
  confirmOkBtn.textContent = confirmLabel;
  confirmModalEl.classList.add("show");
  return new Promise((resolve) => {
    _confirmResolve = resolve;
  });
}
function _settleConfirm(result) {
  confirmModalEl.classList.remove("show");
  if (_confirmResolve) {
    _confirmResolve(result);
    _confirmResolve = null;
  }
}
confirmCancelBtn.addEventListener("click", () => _settleConfirm(false));
confirmOkBtn.addEventListener("click", () => _settleConfirm(true));
confirmModalEl.addEventListener("click", (e) => {
  if (e.target === confirmModalEl) _settleConfirm(false); // backdrop click
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && confirmModalEl.classList.contains("show")) _settleConfirm(false);
});

async function openPreview(f) {
  const viewUrl = `/api/tasks/${f.task_id}/outputs/${f.path
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`;
  const ext = f.name.split(".").pop().toLowerCase();

  previewIcon.replaceChildren(faIcon(fileIconClass(f.name)));
  previewName.textContent = f.path || f.name;
  previewDownload.href = `${viewUrl}?dl=1`;
  previewDownload.download = f.name;
  previewBody.innerHTML = '<div class="preview-loading">Loading…</div>';
  previewModal.classList.add("show");

  if (["png", "jpg", "jpeg", "gif", "svg"].includes(ext)) {
    previewBody.innerHTML = "";
    const img = document.createElement("img");
    img.src = viewUrl;
    previewBody.appendChild(img);
    return;
  }
  if (ext === "pdf") {
    previewBody.innerHTML = "";
    const frame = document.createElement("iframe");
    frame.src = viewUrl;
    previewBody.appendChild(frame);
    return;
  }

  try {
    const res = await fetch(viewUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    previewBody.innerHTML = "";
    if (ext === "csv") {
      previewBody.appendChild(renderDelimited(text, ","));
    } else if (ext === "tsv") {
      previewBody.appendChild(renderDelimited(text, "\t"));
    } else if (ext === "json") {
      const pre = document.createElement("pre");
      try {
        pre.textContent = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        pre.textContent = text;
      }
      previewBody.appendChild(pre);
    } else if (["html", "htm"].includes(ext)) {
      const frame = document.createElement("iframe");
      frame.srcdoc = text;
      previewBody.appendChild(frame);
    } else {
      const pre = document.createElement("pre");
      pre.textContent = text;
      previewBody.appendChild(pre);
    }
  } catch (err) {
    previewBody.innerHTML = `<div class="preview-error">Couldn't load preview: ${err.message || err}</div>`;
  }
}

previewClose.addEventListener("click", closePreview);
previewModal.addEventListener("click", (e) => {
  if (e.target === previewModal) closePreview(); // click on the backdrop
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && previewModal.classList.contains("show")) closePreview();
});

// ---- outputs tray: every file any run in this chat has produced, in one
// place, instead of hunting through individual messages for it. ----
let chatOutputFiles = []; // {name, path, size, task_id, prompt, ts}
let lastUserPrompt = "";

function resetOutputTray() {
  chatOutputFiles = [];
  renderOutputTray();
}

function registerOutputFiles(files, prompt, ts) {
  if (!files || !files.length) return;
  const seen = new Set(chatOutputFiles.map((f) => `${f.task_id}/${f.path}`));
  for (const f of files) {
    const key = `${f.task_id}/${f.path}`;
    if (seen.has(key)) continue;
    seen.add(key);
    chatOutputFiles.push({ ...f, prompt: prompt || "", ts: ts || Date.now() / 1000 });
  }
  renderOutputTray();
}

let visibleOutputFiles = []; // whatever renderOutputTray last showed — what "Download all" zips

function renderOutputTray() {
  outputBadgeEl.textContent = chatOutputFiles.length || "";
  const term = outputSearchEl.value.trim().toLowerCase();
  const visible = chatOutputFiles
    .filter((f) => !term || f.name.toLowerCase().includes(term) || f.prompt.toLowerCase().includes(term))
    .sort((a, b) => b.ts - a.ts);
  visibleOutputFiles = visible;

  outputDownloadAllBtn.disabled = visible.length === 0;
  outputDownloadAllBtn.textContent =
    visible.length === chatOutputFiles.length
      ? `Download all${visible.length ? ` (${visible.length})` : ""}`
      : `Download shown (${visible.length})`;

  outputCountEl.textContent = chatOutputFiles.length
    ? `${visible.length} of ${chatOutputFiles.length} files`
    : "";

  outputListEl.innerHTML = "";
  if (!chatOutputFiles.length) {
    outputListEl.innerHTML = '<li class="empty">No files generated in this chat yet</li>';
    return;
  }
  if (!visible.length) {
    outputListEl.innerHTML = '<li class="empty">No files match your search</li>';
    return;
  }
  for (const f of visible) {
    const li = document.createElement("li");
    li.className = "output-item";

    const icon = document.createElement("span");
    icon.className = "output-icon";
    icon.appendChild(faIcon(fileIconClass(f.name)));

    const meta = document.createElement("span");
    meta.className = "output-meta";
    const name = document.createElement("span");
    name.className = "output-name";
    name.textContent = f.path || f.name;
    name.title = f.path || f.name;
    const sub = document.createElement("span");
    sub.className = "output-sub";
    const when = new Date(f.ts * 1000).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    sub.textContent = `${formatFileSize(f.size)} · ${when}${f.prompt ? ` · "${f.prompt.slice(0, 40)}${f.prompt.length > 40 ? "…" : ""}"` : ""}`;
    meta.append(name, sub);
    li.append(icon, meta);

    const ext = f.name.split(".").pop().toLowerCase();
    if (VIEWABLE_EXT.has(ext)) {
      const view = document.createElement("button");
      view.className = "msg-file-btn";
      view.title = `View ${f.name}`;
      view.replaceChildren(faIcon("eye"));
      view.addEventListener("click", () => openPreview(f));
      li.appendChild(view);
    }
    const url = `/api/tasks/${f.task_id}/outputs/${f.path.split("/").map(encodeURIComponent).join("/")}`;
    const download = document.createElement("a");
    download.className = "msg-file-btn";
    download.href = `${url}?dl=1`;
    download.download = f.name;
    download.title = `Download ${f.name}`;
    download.replaceChildren(faIcon("download"));
    li.appendChild(download);

    outputListEl.appendChild(li);
  }
}
outputSearchEl.addEventListener("input", renderOutputTray);

outputDownloadAllBtn.addEventListener("click", async () => {
  if (!visibleOutputFiles.length) return;
  const original = outputDownloadAllBtn.textContent;
  outputDownloadAllBtn.disabled = true;
  outputDownloadAllBtn.textContent = "Zipping…";
  try {
    const res = await fetch("/api/outputs/zip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: visibleOutputFiles.map((f) => ({ task_id: f.task_id, path: f.path })),
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "outputs.zip";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    outputCountEl.textContent = `Zip failed: ${err.message || err}`;
  } finally {
    outputDownloadAllBtn.disabled = visibleOutputFiles.length === 0;
    outputDownloadAllBtn.textContent = original;
  }
});

function addMessage(role, text, isError = false, files = [], ts = null, msgId = null) {
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  div.textContent = text;
  if (role === "user") {
    lastUserPrompt = text;
    const btns = document.createElement("div");
    btns.className = "msg-btns";

    const retry = document.createElement("button");
    retry.className = "retry-btn";
    retry.title = "Retry this prompt (adds a new message at the end)";
    retry.replaceChildren(faIcon("arrows-rotate"));
    retry.addEventListener("click", () => runTask(text));
    btns.appendChild(retry);

    // Rewind is only meaningful once this message is actually saved (has a
    // real id) and belongs to a real chat — a brand-new unsent chat has
    // neither, and there's nothing to delete yet.
    if (msgId != null && currentChatId) {
      const rewind = document.createElement("button");
      rewind.className = "retry-btn rewind-btn";
      rewind.title = "Rewind to here and retry (deletes everything after this message)";
      rewind.replaceChildren(faIcon("clock-rotate-left"));
      rewind.addEventListener("click", () => rewindAndRetry(msgId, text));
      btns.appendChild(rewind);
    }

    div.appendChild(btns);
  }
  if (files && files.length) {
    registerOutputFiles(files, lastUserPrompt, ts);
  }
  if (files && files.length) {
    const list = document.createElement("div");
    list.className = "msg-files";
    for (const f of files) {
      const chip = document.createElement("div");
      chip.className = "msg-file";

      const icon = document.createElement("span");
      icon.className = "msg-file-icon";
      icon.appendChild(faIcon(fileIconClass(f.name)));

      const info = document.createElement("span");
      info.className = "msg-file-info";
      const name = document.createElement("span");
      name.className = "msg-file-name";
      name.textContent = f.path || f.name;
      name.title = f.path || f.name;
      const size = document.createElement("span");
      size.className = "msg-file-size";
      size.textContent = formatFileSize(f.size);
      info.append(name, size);

      chip.append(icon, info);

      const url = `/api/tasks/${f.task_id}/outputs/${f.path
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`;
      const ext = f.name.split(".").pop().toLowerCase();

      if (VIEWABLE_EXT.has(ext)) {
        const view = document.createElement("button");
        view.className = "msg-file-btn";
        view.title = `View ${f.name}`;
        view.replaceChildren(faIcon("eye"));
        view.addEventListener("click", () => openPreview(f));
        chip.appendChild(view);
      }

      const download = document.createElement("a");
      download.className = "msg-file-btn";
      download.href = `${url}?dl=1`;
      download.download = f.name;
      download.title = `Download ${f.name}`;
      download.replaceChildren(faIcon("download"));
      chip.appendChild(download);

      list.appendChild(chip);
    }
    div.appendChild(list);
  }
  if (role === "assistant") {
    const copy = document.createElement("button");
    copy.className = "copy-btn";
    copy.title = "Copy response";
    copy.replaceChildren(faIcon("copy"));
    copy.addEventListener("click", async () => {
      await copyToClipboard(text);
      copy.classList.add("copied");
      copy.replaceChildren(faIcon("check"));
      setTimeout(() => {
        copy.classList.remove("copied");
        copy.replaceChildren(faIcon("copy"));
      }, 1200);
    });
    div.appendChild(copy);
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
    resetOutputTray();
    loadAttachedForChat(chatId);
    for (const m of chat.messages) {
      if (m.role === "trace") {
        traceLine("agent", m.content, m.created); // replay into the terminal panel
      } else if (m.role === "run_start" || m.role === "run_end") {
        traceLine(m.role, m.content, m.created); // run boundary markers
      } else {
        addMessage(m.role, m.content, m.content.startsWith("[error]"), m.files, m.created, m.id);
      }
    }
    // Re-attach to an in-flight run (e.g. after a page refresh) so the
    // running indicator, stop button, and live trace come back.
    try {
      const { task_id } = await fetch(`/api/chats/${chatId}/active`).then((r) => r.json());
      if (task_id !== currentTaskId) {
        // Whatever was streaming (if anything) belongs to a different
        // chat's task — stop it before this chat's own run (if any) takes
        // over. Without this, switching to a chat with no active run of
        // its own leaves the old stream running forever, silently
        // appending another chat's trace lines (and error count) here.
        if (activeEventSource) {
          activeEventSource.close();
          activeEventSource = null;
          currentTaskId = null;
          setRunning(false);
        }
        if (task_id) {
          // The SSE stream replays the run's steps from the start, and this
          // chat's persisted trace already contains them — reset the panel
          // so the run isn't shown twice.
          stepsEl.innerHTML = "";
          updateErrCount();
          traceStart();
          attachStream(task_id);
        }
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
  // Same reasoning as selectChat(): a brand-new chat has no run of its
  // own, so any stream left over from whatever chat you were just on
  // must be stopped rather than left leaking into this empty panel.
  if (activeEventSource) {
    activeEventSource.close();
    activeEventSource = null;
    currentTaskId = null;
    setRunning(false);
  }
  currentChatId = null;
  setChatName("");
  messagesEl.innerHTML = "";
  stepsEl.innerHTML = "";
  updateErrCount();
  resetOutputTray();
  loadAttachedForChat(null);
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
    if (btn.dataset.tab === "outputs") renderOutputTray();
  });
});

// ---- files ----
// Attachment selection survives page reloads and server restarts — nobody
// wants to re-check 16 files by hand every time. Scoped per chat: attaching
// files in one chat shouldn't silently apply them to every other chat too.
const ATTACHED_KEY_PREFIX = "attachedFiles:";
function attachedKeyFor(chatId) {
  return ATTACHED_KEY_PREFIX + (chatId || "new");
}
const attached = new Set(); // repopulated by loadAttachedForChat() below

function loadAttachedForChat(chatId) {
  attached.clear();
  try {
    for (const name of JSON.parse(localStorage.getItem(attachedKeyFor(chatId)) || "[]")) {
      attached.add(name);
    }
  } catch {
    /* corrupt localStorage entry — start clean */
  }
  updateAttachHint();
  if (allFiles.length) renderFileList();
}

function saveAttached() {
  localStorage.setItem(attachedKeyFor(currentChatId), JSON.stringify([...attached]));
}

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

// Full list from the server, cached so search/sort/attach-all don't need
// a round trip — they just re-filter and re-render this.
let allFiles = [];

function renderFileList() {
  const term = fileSearchEl.value.trim().toLowerCase();
  const visible = term ? allFiles.filter((f) => f.name.toLowerCase().includes(term)) : allFiles.slice();

  const [key, dir] = fileSortEl.value.split("-");
  visible.sort((a, b) => {
    const cmp = key === "size" ? a.size - b.size : a.name.localeCompare(b.name);
    return dir === "desc" ? -cmp : cmp;
  });

  // "Attach All" acts on whatever's currently visible (filtered), and
  // flips to "Detach All" once every visible file is already attached.
  const allVisibleAttached = visible.length > 0 && visible.every((f) => attached.has(f.name));
  attachAllBtn.textContent = allVisibleAttached ? "Detach all" : "Attach all";
  attachAllBtn.classList.toggle("all-on", allVisibleAttached);
  attachAllBtn.disabled = visible.length === 0;

  fileCountEl.textContent = allFiles.length
    ? `${visible.length} of ${allFiles.length} shown · ${attached.size} attached`
    : "";

  fileList.innerHTML = "";
  for (const f of visible) {
    const li = document.createElement("li");

    const icon = document.createElement("span");
    icon.className = "file-icon";
    icon.appendChild(faIcon(fileIconClass(f.name)));

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
    const renderToggle = () => {
      const on = attached.has(f.name);
      toggle.classList.toggle("on", on);
      toggle.replaceChildren(
        ...(on ? [faIcon("check"), document.createTextNode(" Attached")] : [document.createTextNode("Attach")])
      );
    };
    toggle.addEventListener("click", () => {
      if (attached.has(f.name)) attached.delete(f.name);
      else attached.add(f.name);
      renderToggle();
      updateAttachHint();
      saveAttached();
      renderFileList(); // refresh the attach-all label/count too
    });
    renderToggle();

    const remove = document.createElement("button");
    remove.className = "file-remove";
    remove.replaceChildren(faIcon("xmark"));
    remove.title = `Remove ${f.name}`;
    remove.addEventListener("click", async () => {
      const ok = await confirmModal(
        `This permanently deletes "${f.name}" from disk — it'll disappear from ` +
          `the file list and from any chat that has it attached. This can't be undone.`
      );
      if (!ok) return;
      remove.disabled = true;
      try {
        const res = await fetch(`/api/files/${encodeURIComponent(f.name)}`, { method: "DELETE" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `server returned HTTP ${res.status}`);
        attached.delete(f.name);
        updateAttachHint();
        saveAttached();
        await refreshFiles();
      } catch (err) {
        remove.disabled = false;
        uploadStatus.textContent = `Couldn't delete ${f.name}: ${err.message || err}`;
      }
    });

    li.append(icon, meta, toggle, remove);
    fileList.appendChild(li);
  }
  if (allFiles.length === 0) {
    fileList.innerHTML = '<li class="empty">No files uploaded yet</li>';
  } else if (visible.length === 0) {
    fileList.innerHTML = '<li class="empty">No files match your search</li>';
  }
}

attachAllBtn.addEventListener("click", () => {
  const term = fileSearchEl.value.trim().toLowerCase();
  const visible = term ? allFiles.filter((f) => f.name.toLowerCase().includes(term)) : allFiles;
  const allVisibleAttached = visible.length > 0 && visible.every((f) => attached.has(f.name));
  for (const f of visible) {
    if (allVisibleAttached) attached.delete(f.name);
    else attached.add(f.name);
  }
  updateAttachHint();
  saveAttached();
  renderFileList();
});
fileSearchEl.addEventListener("input", renderFileList);
fileSortEl.addEventListener("change", renderFileList);

async function refreshFiles() {
  try {
    const res = await fetch("/api/files");
    if (!res.ok) throw new Error(`server returned HTTP ${res.status}`);
    const { files } = await res.json();
    allFiles = files || [];

    // Drop any restored attachment whose file no longer exists on disk.
    const onDisk = new Set(allFiles.map((f) => f.name));
    let pruned = false;
    for (const name of attached) {
      if (!onDisk.has(name)) {
        attached.delete(name);
        pruned = true;
      }
    }
    if (pruned) saveAttached();
    updateAttachHint();
    renderFileList();
  } catch (err) {
    const detail = err instanceof TypeError
      ? "can't reach the server — is it still running?"
      : err.message || String(err);
    fileList.innerHTML = `<li class="empty">Could not load file list: ${detail}</li>`;
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
    saveAttached();
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
let activeEventSource = null; // the one live SSE connection this tab should have open

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

async function rewindAndRetry(msgId, promptText) {
  const ok = await confirmModal(
    "This permanently deletes this message and everything after it in this " +
      "chat — replies, trace, and any generated files — then re-sends the " +
      "same prompt as a fresh run. This can't be undone.",
    { title: "Rewind and retry?", confirmLabel: "Rewind & retry" }
  );
  if (!ok) return;
  if (!currentChatId) return;

  try {
    const res = await fetch(`/api/chats/${currentChatId}/rewind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: msgId }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  } catch (err) {
    addMessage("assistant", `Rewind failed: ${err.message || err}`, true);
    return;
  }

  await selectChat(currentChatId); // reload the now-truncated history
  runTask(promptText);
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
    saveAttached(); // persist under the now-real chat id, not the "new" bucket
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
  // Only one run's events should ever be able to touch the DOM at a time —
  // switching chats (or starting a new run) while a previous stream is
  // still open otherwise leaves it firing in the background, appending
  // that other run's trace lines (and error count) into whatever chat is
  // currently on screen.
  if (activeEventSource) activeEventSource.close();

  currentTaskId = taskId;
  setRunning(true);
  const source = new EventSource(`/api/tasks/${taskId}/stream`);
  activeEventSource = source;
  const isStale = () => source !== activeEventSource;

  source.onmessage = (event) => {
    if (isStale()) return; // a newer stream has since taken over
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
      if (activeEventSource === source) activeEventSource = null;
      setRunning(false);
      traceEnd();
      if (msg.status === "done") {
        addMessage("assistant", msg.result, false, msg.files);
        traceEl.classList.remove("open");
      } else {
        addMessage("assistant", msg.error || "task failed", true, msg.files);
      }
    }
    if (msg.error && !msg.status) {
      source.close();
      if (activeEventSource === source) activeEventSource = null;
      setRunning(false);
      traceEnd();
    }
  };
  source.onerror = () => {
    if (isStale()) return;
    source.close();
    if (activeEventSource === source) activeEventSource = null;
    // A long-lived SSE connection can drop on a blip (dev-server quirk,
    // network hiccup) with the backend task still very much alive — a
    // multi-minute pyopenms run is exactly the kind that outlives a flaky
    // connection. Don't assume "error" means "over"; check real status
    // before tearing the UI down and silently abandoning a live run.
    fetch(`/api/tasks/${taskId}`)
      .then((r) => r.json())
      .then((snap) => {
        if (snap && (snap.status === "queued" || snap.status === "running")) {
          // Still going — reconnect cleanly after a short delay (avoids a
          // tight retry loop if the connection is persistently broken
          // rather than just a one-off blip). The fresh stream replays
          // full history from the start, so clear the panel first to
          // avoid duplicating everything already shown (same reasoning as
          // the re-attach-after-refresh path in selectChat()).
          setTimeout(() => {
            if (currentTaskId !== taskId) return; // superseded meanwhile
            stepsEl.innerHTML = "";
            updateErrCount();
            attachStream(taskId);
          }, 1500);
        } else {
          setRunning(false);
          traceEnd();
        }
      })
      .catch(() => {
        setRunning(false);
        traceEnd();
      });
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
      if (status.state === "ready" && status.validated) {
        // Byte count matched, and the background integrity check (parquet
        // footers, json/pkl parse, non-empty text files) came back clean.
        stopDlPolling();
        dlFill.style.width = "100%";
        dlPercent.textContent = "100%";
        dlNote.textContent = "Data lake ready — you're all set.";
        setTimeout(() => dlModal.classList.remove("show", "downloading"), 1600);
      } else if (status.state === "ready" && !status.validated) {
        dlNote.textContent = "Verifying downloaded files…";
      } else if (status.state === "invalid") {
        stopDlPolling();
        dlModal.classList.remove("downloading");
        dlNote.textContent =
          `Download finished but failed an integrity check ` +
          `(${status.validation_error || "unknown error"}). Click "Download" to retry.`;
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

// Quietly poll (no modal) while a "ready-by-size" data lake finishes its
// background integrity check, in case it turns out corrupted/truncated.
function pollValidationQuietly() {
  const timer = setInterval(async () => {
    try {
      const status = await fetch("/api/datalake").then((r) => r.json());
      if (status.state === "invalid") {
        clearInterval(timer);
        dlNote.textContent =
          `Data lake failed an integrity check ` +
          `(${status.validation_error || "unknown error"}). Click "Download" to re-fetch it.`;
        dlModal.classList.add("show");
      } else if (status.validated) {
        clearInterval(timer); // valid — nothing to show the user
      }
    } catch {
      /* transient poll failure — keep trying */
    }
  }, 1500);
}

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
    } else if (status.state === "invalid") {
      dlNote.textContent =
        `Data lake failed an integrity check ` +
        `(${status.validation_error || "unknown error"}). Click "Download" to re-fetch it.`;
      dlModal.classList.add("show");
    } else if (status.state === "ready" && !status.validated) {
      // Size looks right — verify in the background before trusting it,
      // without blocking the UI or popping the modal for the common case.
      pollValidationQuietly();
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
