// Every state-changing API call must carry the per-process token the server
// embedded in the page (see _guard_request in app/__init__.py). Wrapping
// fetch once covers every call site; only same-origin requests get it.
{
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.origin === location.origin) {
      const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
      headers.set("X-Biomnikhil-Token", csrfToken);
      init = { ...init, headers };
      // 401 = the Cognito sign-in ended (server restart, revoked user):
      // go sign in again rather than surfacing it as a broken call.
      return nativeFetch(input, init).then((res) => {
        if (res.status === 401) location.assign("/login");
        return res;
      });
    }
    return nativeFetch(input, init);
  };
}

// The desktop app window has no developer console, so uncaught errors go to
// the server log (data/server.log) instead of disappearing.
{
  const report = (message, where) =>
    fetch("/api/client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: String(message).slice(0, 2000), where }),
    }).catch(() => {});
  window.addEventListener("error", (e) =>
    report(e.error?.stack || e.message, `${e.filename}:${e.lineno}:${e.colno}`));
  window.addEventListener("unhandledrejection", (e) =>
    report(e.reason?.stack || e.reason, "unhandled promise rejection"));
}

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

// The current run's progress, for the live status line. The trace panel keeps
// every earlier run of the chat, so its step labels can't supply this count.
let runStepCount = 0;
let runMaxSteps = null; // sent by the server; absent for a run with no cap

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
  runStepCount = 0;
  runMaxSteps = null;
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
const PHASE_LABELS = {
  execute: "running code",
  observation: "reading output",
  thought: "thinking",
  solution: "writing answer",
};

// The first module a code segment imports, shown as the tool in use.
function findImportedTool(code) {
  const importMatch = code.match(/^\s*(?:from|import)\s+(\w+)/m);
  if (!importMatch) return null;
  return importMatch[1];
}

// For example: "step 7/150 · running code · using pyopenms".
function buildProgressStatus(phase, tool) {
  const parts = [];
  if (runMaxSteps) {
    parts.push(`step ${runStepCount}/${runMaxSteps}`);
  } else {
    parts.push(`step ${runStepCount}`);
  }
  parts.push(PHASE_LABELS[phase] || "thinking");
  if (tool) parts.push(`using ${tool}`);
  return parts.join(" · ");
}

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
  // Counted before filtering: the server's step cap counts hidden steps too.
  if (kind === "agent" && traceLive.parentNode === stepsEl) runStepCount += 1;
  const cleaned = kind === "agent" ? cleanTrace(text) : text;
  if (cleaned === null) return;
  const ln = document.createElement("div");
  ln.className = `ln ${kind}`;
  let lastSegType = null;
  let lastSegTool = null;
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
      lastSegTool = null;
      if (seg.type === "execute") lastSegTool = findImportedTool(seg.text);
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
  if (live && lastSegType) setTraceStatus(buildProgressStatus(lastSegType, lastSegTool));
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
// Esc closes whatever is on top, so the trace waits if a dialog or the chat
// list is open. Runs before their own Esc handlers, which are added later.
// Text inputs like chat rename use Esc to cancel, so they keep it too.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (e.target.tagName === "INPUT") return;
  if (confirmModalEl.classList.contains("show")) return;
  if (previewModal.classList.contains("show")) return;
  if (sidebar.classList.contains("open")) return;
  traceEl.classList.remove("open");
});

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
      // Agent-written HTML is untrusted (it can embed fetched or uploaded
      // data). Scripts may run, for interactive plots, but without
      // allow-same-origin the frame gets an opaque origin: it can't read
      // this page, its token, or call /api/* as the app.
      frame.sandbox = "allow-scripts";
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
  chatCitations = [];
  renderCitations();
  chatOutputFiles = [];
  viewerChoice = null; // a new chat starts back on its newest index.html
  renderOutputTray();
  renderViewer();
}

// Chats saved by a Windows server before paths were normalized store
// "figures\\a.png"; every URL and label here assumes "/".
function normalizeOutputFile(f) {
  return f.path && f.path.includes("\\") ? { ...f, path: f.path.replaceAll("\\", "/") } : f;
}

function registerOutputFiles(files, prompt, ts) {
  if (!files || !files.length) return;
  const seen = new Set(chatOutputFiles.map((f) => `${f.task_id}/${f.path}`));
  for (const f of files.map(normalizeOutputFile)) {
    const key = `${f.task_id}/${f.path}`;
    if (seen.has(key)) continue;
    seen.add(key);
    chatOutputFiles.push({ ...f, prompt: prompt || "", ts: ts || Date.now() / 1000 });
  }
  renderOutputTray();
  renderViewer();
}

// ---- viewer: renders a webpage the agent generated, by URL (not srcdoc)
// so its relative CSS/JS/images resolve against the run's output folder.
// Defaults to the newest index.html in the chat (else the newest .html);
// once the user picks a page it sticks until that chat is left. ----
const viewerPageEl = document.getElementById("viewer-page");
const viewerFrame = document.getElementById("viewer-frame");
const viewerEmpty = document.getElementById("viewer-empty");
const viewerOpen = document.getElementById("viewer-open");
const viewerReload = document.getElementById("viewer-reload");
let viewerChoice = null; // `${task_id}/${path}` the user picked, or null = auto

function outputFileUrl(f) {
  return `/api/tasks/${encodeURIComponent(f.task_id)}/outputs/${f.path
    .split("/").map(encodeURIComponent).join("/")}`;
}

function renderViewer() {
  const pages = chatOutputFiles
    .filter((f) => /\.html?$/i.test(f.name))
    .sort((a, b) => b.ts - a.ts);
  const hasPages = pages.length > 0;
  viewerEmpty.hidden = hasPages;
  viewerFrame.hidden = !hasPages;
  viewerPageEl.disabled = viewerReload.disabled = !hasPages;
  viewerOpen.setAttribute("aria-disabled", String(!hasPages));
  if (!hasPages) {
    viewerPageEl.replaceChildren(option("", "No pages yet"));
    viewerOpen.removeAttribute("href");
    if (viewerFrame.getAttribute("src")) viewerFrame.removeAttribute("src");
    return;
  }

  const key = (f) => `${f.task_id}/${f.path}`;
  const fallback = pages.find((f) => /(^|\/)index\.html?$/i.test(f.path)) || pages[0];
  const selected = pages.find((f) => key(f) === viewerChoice) || fallback;

  viewerPageEl.replaceChildren(
    ...pages.map((f) => {
      const when = new Date(f.ts * 1000).toLocaleString([], {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
      const opt = option(key(f), `${f.path} · ${when}`);
      opt.title = f.prompt || f.path;
      return opt;
    }),
  );
  viewerPageEl.value = key(selected);

  const url = outputFileUrl(selected);
  viewerOpen.href = url;
  // Only navigate when the page changed, so new outputs arriving mid-run
  // don't reload (and reset) the page being looked at.
  if (viewerFrame.getAttribute("src") !== url) viewerFrame.src = url;
}

viewerPageEl.addEventListener("change", () => {
  viewerChoice = viewerPageEl.value;
  renderViewer();
});
viewerReload.addEventListener("click", () => {
  const url = viewerFrame.getAttribute("src");
  if (url) viewerFrame.src = url; // reassigning the same URL reloads it
});
renderViewer(); // empty state until a chat's outputs load

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
  outputDownloadAllBtn.textContent = "Saving…";
  let savedMessage = null;
  try {
    const res = await fetch("/api/outputs/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        files: visibleOutputFiles.map((f) => ({ task_id: f.task_id, path: f.path })),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    savedMessage = `Saved ${data.count} file${data.count === 1 ? "" : "s"} to ${data.folder}`;
  } catch (err) {
    savedMessage = `Save failed: ${err.message || err}`;
  } finally {
    outputDownloadAllBtn.disabled = visibleOutputFiles.length === 0;
    outputDownloadAllBtn.textContent = original;
  }
  outputCountEl.textContent = savedMessage;
});

// ---- Markdown answers ----
// Agent answers can repeat text from fetched pages or uploaded files, so only
// structural tags survive. Images and embeds are left out on purpose: a remote
// image would load by itself and could carry data out in its URL.
const MARKDOWN_SANITIZE_CONFIG = {
  ALLOWED_TAGS: [
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "del", "code", "pre", "blockquote", "sup", "sub",
    "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "a",
  ],
  ALLOWED_ATTR: ["href", "title", "align", "start"],
};

// Raw HTML tags the sanitizer would keep, written with no attributes. Agents
// use these on purpose, as in "10<sup>-5</sup>".
const KEPT_RAW_HTML_TAG = /^<\/?(sup|sub|br|strong|em|del|code)\s*\/?>$/i;

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function createMarkdownParser() {
  if (!window.marked || !window.DOMPurify) return null;
  const parser = new window.marked.Marked({ gfm: true, breaks: true });
  parser.use({
    renderer: {
      // Images aren't allowed through, so keep their description in the text.
      image(token) {
        return token.text;
      },
      // Anything else shaped like a tag is text: "<NA>" is a pandas value and
      // "x<y and z>w" is a comparison. Left as HTML, the sanitizer would
      // delete it and silently change what the answer says.
      html(token) {
        const rawHtml = token.text;
        if (KEPT_RAW_HTML_TAG.test(rawHtml.trim())) return rawHtml;
        const escapedHtml = escapeHtml(rawHtml);
        if (token.block) return `<p>${escapedHtml}</p>`;
        return escapedHtml;
      },
      // Inside a raw tag like <code>, marked leaves text unescaped, so
      // "<code>a<b</code>" would lose its end the same way.
      text(token) {
        if (token.escaped && !token.tokens) return escapeHtml(token.text);
        return false; // false = the default renderer
      },
      // The sanitizer removes <input>, which would make done and not-done
      // steps look the same.
      checkbox(token) {
        if (token.checked) return "☑ ";
        return "☐ ";
      },
    },
    tokenizer: {
      // Only ~~double~~ tildes strike through. Answers often say "~5 kDa to
      // ~10 kDa", which single-tilde strikethrough would mangle.
      del(source) {
        if (source.startsWith("~~")) return false; // false = the default tokenizer
        return undefined; // not a strikethrough
      },
    },
  });
  return parser;
}
const markdownParser = createMarkdownParser();

function outputFileUrl(file) {
  const encodedParts = [];
  for (const part of file.path.split("/")) {
    encodedParts.push(encodeURIComponent(part));
  }
  return `/api/tasks/${file.task_id}/outputs/${encodedParts.join("/")}`;
}

function createFileLink(file, label) {
  const extension = file.name.split(".").pop().toLowerCase();
  if (VIEWABLE_EXT.has(extension)) {
    const previewButton = document.createElement("button");
    previewButton.className = "msg-file-link";
    previewButton.title = `View ${file.name}`;
    previewButton.textContent = label;
    previewButton.addEventListener("click", () => openPreview(file));
    return previewButton;
  }
  const downloadLink = document.createElement("a");
  downloadLink.className = "msg-file-link";
  downloadLink.href = `${outputFileUrl(file)}?dl=1`;
  downloadLink.download = file.name;
  downloadLink.title = `Download ${file.name}`;
  downloadLink.textContent = label;
  return downloadLink;
}

// Turns output file names mentioned in the answer's prose into links.
function linkFileNames(container, files) {
  const fileByLabel = new Map();
  for (const rawFile of files) {
    const file = normalizeOutputFile(rawFile);
    if (file.path) fileByLabel.set(file.path, file);
    if (!fileByLabel.has(file.name)) fileByLabel.set(file.name, file);
  }
  if (fileByLabel.size === 0) return;

  // Longest first, so "figures/a.png" wins over "a.png".
  const labels = [...fileByLabel.keys()];
  labels.sort((first, second) => second.length - first.length);
  const escapedLabels = [];
  for (const label of labels) {
    escapedLabels.push(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  }
  const labelPattern = new RegExp(
    `(?<![\\w./-])(?:${escapedLabels.join("|")})(?![\\w-]|\\.\\w)`,
    "g"
  );

  // Collected first: replacing nodes during the walk would derail it.
  const textNodes = [];
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const textNode = walker.currentNode;
    if (!textNode.parentElement.closest("code, pre, a")) textNodes.push(textNode);
  }

  for (const textNode of textNodes) {
    const nodeText = textNode.nodeValue;
    const matches = [...nodeText.matchAll(labelPattern)];
    if (matches.length === 0) continue;
    const fragment = document.createDocumentFragment();
    let cursor = 0;
    for (const match of matches) {
      const label = match[0];
      fragment.append(nodeText.slice(cursor, match.index));
      fragment.append(createFileLink(fileByLabel.get(label), label));
      cursor = match.index + label.length;
    }
    fragment.append(nodeText.slice(cursor));
    textNode.replaceWith(fragment);
  }
}

// The rendered answer, or null when it has to stay plain text (a library
// didn't load, or parsing failed).
function renderMarkdown(text, files) {
  if (!markdownParser) return null;
  try {
    const parsedHtml = markdownParser.parse(text);
    const body = document.createElement("div");
    body.className = "msg-markdown";
    body.innerHTML = window.DOMPurify.sanitize(parsedHtml, MARKDOWN_SANITIZE_CONFIG);
    for (const link of body.querySelectorAll("a")) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    linkFileNames(body, files || []);
    return body;
  } catch (err) {
    console.error("Markdown rendering failed", err);
    return null;
  }
}

// ---- smart scrolling ----
// The page itself scrolls, so this measures against the window.
function messagesNearBottom() {
  const distanceBelowWindow = messagesEl.getBoundingClientRect().bottom - window.innerHeight;
  return distanceBelowWindow < 80;
}

// ---- example and follow-up prompts ----
// A click only fills the prompt; it never starts a run.
function createPromptChips(prompts) {
  const chips = document.createElement("div");
  chips.className = "prompt-chips";
  for (const promptText of prompts) {
    const chip = document.createElement("button");
    chip.className = "prompt-chip";
    chip.textContent = promptText;
    chip.addEventListener("click", () => fillPrompt(promptText));
    chips.appendChild(chip);
  }
  return chips;
}

function fileExtension(fileName) {
  return fileName.split(".").pop().toLowerCase();
}

function examplePromptsForEmptyChat() {
  const prompts = [];
  for (const fileName of attached) {
    const extension = fileExtension(fileName);
    if (extension === "mzml") {
      prompts.push(`Identify peptides in ${fileName}`);
    } else if (extension === "csv" || extension === "tsv") {
      prompts.push(`Summarize ${fileName}`);
    }
  }
  prompts.push("Find recent papers on a protein and summarize their findings");
  prompts.push("Design PCR primers for a gene");
  prompts.push("Look up known drug targets for a disease");
  return prompts.slice(0, 4);
}

function renderEmptyState() {
  removeEmptyState();
  const emptyState = document.createElement("div");
  emptyState.className = "empty-state";
  emptyState.textContent = "biomnikhil";
  emptyState.appendChild(createPromptChips(examplePromptsForEmptyChat()));
  messagesEl.appendChild(emptyState);
}

function removeEmptyState() {
  const emptyState = messagesEl.querySelector(".empty-state");
  if (emptyState) emptyState.remove();
}

function followupPromptsForFiles(files) {
  const prompts = [];
  let hasTable = false;
  let hasFigure = false;
  for (const file of files) {
    const extension = fileExtension(file.name);
    if (!hasTable && (extension === "csv" || extension === "tsv")) {
      prompts.push(`Plot the key columns of ${file.name}`);
      hasTable = true;
    }
    if (!hasFigure && extension === "png") {
      prompts.push(`Explain this figure: ${file.name}`);
      hasFigure = true;
    }
  }
  if (files.length === 0) prompts.push("Save the result as a CSV");
  prompts.push("Explain the method used");
  return prompts;
}

function renderFollowups(messageDiv, files) {
  const wasNearBottom = messagesNearBottom();
  const followups = createPromptChips(followupPromptsForFiles(files || []));
  followups.classList.add("followups");
  messageDiv.appendChild(followups);
  if (wasNearBottom) messageDiv.scrollIntoView({ block: "end" });
}

const STOPPED_ERROR_TEXT = "Stopped by user.";

// options.noScroll: replaying history, which scrolls once at the end.
// options.stopped: the user stopped the run, which isn't a failure.
// ---- citations: sources that appeared in this chat's answers and traces,
// worked out by the server from the stored messages (services/citations.py).
// ----
const citationListEl = document.getElementById("citation-list");
const citationSearchEl = document.getElementById("citation-search");
const citationCountEl = document.getElementById("citation-count");
const citationBadgeEl = document.getElementById("citation-badge");
let chatCitations = [];
let citationTimer = null;

// Debounced: replaying a chat calls this once per answer.
function refreshCitations() {
  clearTimeout(citationTimer);
  citationTimer = setTimeout(async () => {
    const chatId = currentChatId;
    let found = [];
    if (chatId) {
      try {
        const res = await fetch(`/api/chats/${encodeURIComponent(chatId)}/citations`);
        if (res.ok) found = (await res.json()).citations;
      } catch { /* keep the tab empty rather than break the chat */ }
    }
    if (chatId !== currentChatId) return; // the user moved on meanwhile
    chatCitations = found;
    renderCitations();
  }, 300);
}

function renderCitations() {
  citationBadgeEl.textContent = chatCitations.length || "";
  const term = citationSearchEl.value.trim().toLowerCase();
  const visible = chatCitations.filter((c) => !term || JSON.stringify(c).toLowerCase().includes(term));
  citationCountEl.textContent = chatCitations.length ? `${visible.length} of ${chatCitations.length} citations` : "";
  citationListEl.replaceChildren();
  if (!visible.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = chatCitations.length ? "No citations match your search" : "No citations in this chat yet";
    citationListEl.appendChild(li);
    return;
  }
  for (const c of visible) {
    const li = document.createElement("li");
    li.className = "citation-item";
    const head = document.createElement("div");
    head.className = "citation-head";
    const link = document.createElement("a");
    link.className = "citation-link";
    link.href = c.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = c.label;
    const kind = document.createElement("span");
    kind.className = "citation-kind";
    kind.textContent = c.kind;
    head.append(link, kind);
    if (c.in_answer) {
      const cited = document.createElement("span");
      cited.className = "citation-cited";
      cited.textContent = "In an answer";
      head.appendChild(cited);
    }
    li.appendChild(head);
    for (const u of c.uses) {
      const use = document.createElement("div");
      use.className = "citation-use";
      const where = document.createElement("b");
      where.textContent = u.where;
      use.appendChild(where);
      if (u.prompt) use.append(` · asked: "${u.prompt.slice(0, 80)}${u.prompt.length > 80 ? "…" : ""}"`);
      const excerpt = document.createElement("q");
      excerpt.textContent = u.excerpt;
      use.appendChild(excerpt);
      li.appendChild(use);
    }
    citationListEl.appendChild(li);
  }
}
citationSearchEl.addEventListener("input", renderCitations);

function addMessage(role, text, isError = false, files = [], ts = null, msgId = null, options = {}) {
  if (role === "assistant") refreshCitations();
  removeEmptyState();
  const wasNearBottom = messagesNearBottom();
  const div = document.createElement("div");
  div.className = `msg ${role}` + (isError ? " error" : "");
  if (options.stopped) div.classList.add("stopped");
  let markdownBody = null;
  if (role === "assistant" && !isError && !options.stopped) {
    markdownBody = renderMarkdown(text, files);
  }
  if (markdownBody) {
    div.appendChild(markdownBody);
  } else {
    div.textContent = text;
  }
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
    for (const f of files.map(normalizeOutputFile)) {
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
  // Someone reading further up shouldn't be pulled away by a new answer.
  if (!options.noScroll && (role === "user" || wasNearBottom)) {
    div.scrollIntoView({ block: "end" });
  }
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
    lastUserPrompt = "";
    messagesEl.innerHTML = "";
    stepsEl.innerHTML = "";
    updateErrCount();
    resetOutputTray();
    loadAttachedForChat(chatId);
    refreshFiles(); // a file may have gone since the list was last fetched
    for (const m of chat.messages) {
      if (m.role === "trace") {
        traceLine("agent", m.content, m.created); // replay into the terminal panel
      } else if (m.role === "run_start" || m.role === "run_end") {
        traceLine(m.role, m.content, m.created); // run boundary markers
      } else if (m.content === `[error] ${STOPPED_ERROR_TEXT}`) {
        addMessage(m.role, "Stopped by user", false, m.files, m.created, m.id, { noScroll: true, stopped: true });
      } else {
        addMessage(m.role, m.content, m.content.startsWith("[error]"), m.files, m.created, m.id, { noScroll: true });
      }
    }
    if (messagesEl.children.length === 0) renderEmptyState();
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
      } else if (task_id) {
        // Same run, stream still open: the panel was rebuilt from history
        // above, so put the live status line back under it.
        stepsEl.appendChild(traceLive);
      }
    } catch {
      /* active-task lookup is best-effort */
    }
    document.querySelector('.tabs button[data-tab="task"]').click();
    // After the click: nothing scrolls while the Task tab is hidden.
    const lastMessage = messagesEl.lastElementChild;
    if (lastMessage) lastMessage.scrollIntoView({ block: "end" });
    closeSidebar();
    refreshChats();
  } catch {
    if (messagesEl.children.length === 0) renderEmptyState();
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
  lastUserPrompt = "";
  messagesEl.innerHTML = "";
  stepsEl.innerHTML = "";
  updateErrCount();
  resetOutputTray();
  loadAttachedForChat(null);
  refreshFiles(); // a file may have gone since the list was last fetched
  renderEmptyState();
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
    if (btn.dataset.tab === "citations") refreshCitations();
    if (btn.dataset.tab === "integrity") startIntegrity();
    else stopIntegrity();
    if (btn.dataset.tab === "spending") refreshSpending();
    if (btn.dataset.tab === "settings") refreshSettings();
  });
});

// Cmd+1..n (Ctrl on Windows) picks a tab by its position in the bar.
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
  const tabButtons = document.querySelectorAll(".tabs button");
  const tabButton = tabButtons[Number(e.key) - 1];
  if (!tabButton) return;
  e.preventDefault();
  tabButton.click();
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
const MAX_CHIPS_BEFORE_COLLAPSE = 3;
let attachChipsExpanded = false;
let filesLoaded = false; // until the list arrives, no file can be called missing

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

function detachFile(fileName) {
  attached.delete(fileName);
  saveAttached();
  updateAttachHint();
  renderFileList();
}

// Attached names the server would silently skip, as of the last file list.
function missingAttachments() {
  const missingNames = [];
  if (!filesLoaded) return missingNames;
  const namesOnDisk = new Set();
  for (const file of allFiles) {
    namesOnDisk.add(file.name);
  }
  for (const fileName of attached) {
    if (!namesOnDisk.has(fileName)) missingNames.push(fileName);
  }
  return missingNames;
}

// Deleting a file detaches it everywhere, so it doesn't show as missing in
// other chats. A chat's saved list is only read when that chat opens.
function removeFromSavedAttachments(fileName) {
  try {
    const savedKeys = [];
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (key.startsWith(ATTACHED_KEY_PREFIX)) savedKeys.push(key);
    }
    for (const key of savedKeys) {
      const savedNames = JSON.parse(localStorage.getItem(key) || "[]");
      const keptNames = [];
      for (const savedName of savedNames) {
        if (savedName !== fileName) keptNames.push(savedName);
      }
      localStorage.setItem(key, JSON.stringify(keptNames));
    }
  } catch {
    /* corrupt or unavailable storage: its chip will show as missing instead */
  }
}

function createAttachChip(fileName) {
  let fileInfo = null;
  for (const file of allFiles) {
    if (file.name === fileName) fileInfo = file;
  }
  const isMissing = filesLoaded && fileInfo === null;

  const chip = document.createElement("span");
  chip.className = "attach-chip";
  if (isMissing) chip.classList.add("missing");

  const nameEl = document.createElement("span");
  nameEl.className = "attach-chip-name";
  nameEl.textContent = fileName;
  nameEl.title = fileName;
  chip.appendChild(nameEl);

  const sizeEl = document.createElement("span");
  sizeEl.className = "attach-chip-size";
  if (isMissing) {
    sizeEl.textContent = "(missing)";
    chip.appendChild(sizeEl);
  } else if (fileInfo) {
    sizeEl.textContent = formatSize(fileInfo.size);
    chip.appendChild(sizeEl);
  }

  const removeBtn = document.createElement("button");
  removeBtn.className = "attach-chip-remove";
  removeBtn.title = `Detach ${fileName}`;
  removeBtn.replaceChildren(faIcon("xmark"));
  removeBtn.addEventListener("click", () => detachFile(fileName));
  chip.appendChild(removeBtn);
  return chip;
}

function updateAttachHint() {
  attachHint.replaceChildren();
  attachHint.hidden = attached.size === 0;

  // Suggestions in an empty chat depend on what's attached.
  if (messagesEl.querySelector(".empty-state")) renderEmptyState();
  if (attached.size === 0) return;

  const isCollapsible = attached.size > MAX_CHIPS_BEFORE_COLLAPSE;
  if (isCollapsible) {
    const summary = document.createElement("button");
    summary.className = "attach-summary";
    const arrow = attachChipsExpanded ? "▴" : "▾";
    summary.textContent = `${attached.size} files attached ${arrow}`;
    summary.addEventListener("click", () => {
      attachChipsExpanded = !attachChipsExpanded;
      updateAttachHint();
    });
    attachHint.appendChild(summary);
  }
  if (!isCollapsible || attachChipsExpanded) {
    for (const fileName of attached) {
      attachHint.appendChild(createAttachChip(fileName));
    }
  }
  const clearAll = document.createElement("button");
  clearAll.className = "attach-clear";
  clearAll.textContent = "clear all";
  clearAll.addEventListener("click", () => {
    attached.clear();
    saveAttached();
    updateAttachHint();
    renderFileList();
  });
  attachHint.appendChild(clearAll);
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
        removeFromSavedAttachments(f.name);
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
    filesLoaded = true;
    // An attachment whose file is gone stays listed, so its chip can say so.
    updateAttachHint();
    renderFileList();
  } catch (err) {
    const detail = err instanceof TypeError
      ? "can't reach the server — is it still running?"
      : err.message || String(err);
    fileList.innerHTML = `<li class="empty">Could not load file list: ${detail}</li>`;
  }
}

// The Files tab's status line isn't visible from the Task tab, so uploads
// started there report here too. A success shows as chips instead.
const taskUploadStatus = document.getElementById("task-upload-status");

function setTaskUploadStatus(text) {
  taskUploadStatus.textContent = text;
  taskUploadStatus.hidden = !text;
}

async function uploadFiles(files) {
  if (!files.length) return;
  const form = new FormData();
  for (const f of files) form.append("files", f);
  uploadStatus.textContent = "Uploading…";
  setTaskUploadStatus("Uploading…");
  try {
    const res = await fetch("/api/files", { method: "POST", body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    uploadStatus.textContent = `Uploaded: ${data.saved.join(", ")}`;
    setTaskUploadStatus("");
    for (const name of data.saved) attached.add(name);
    updateAttachHint();
    saveAttached();
    await refreshFiles();
  } catch (err) {
    uploadStatus.textContent = `Upload failed: ${err.message || err}`;
    setTaskUploadStatus(`Upload failed: ${err.message || err}`);
  }
}

dropzone.addEventListener("click", () => fileInput.click());
document.getElementById("composer-attach").addEventListener("click", () => fileInput.click());
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

// Drag and paste anywhere on the Task tab. Dragged or pasted text is left
// alone, so it still lands in the prompt as usual.
const taskPanel = document.getElementById("tab-task");

function dragHasFiles(event) {
  return [...event.dataTransfer.types].includes("Files");
}

taskPanel.addEventListener("dragover", (event) => {
  if (!dragHasFiles(event)) return;
  event.preventDefault();
  taskPanel.classList.add("dragover");
});
taskPanel.addEventListener("dragleave", (event) => {
  // Moving over a child element also fires dragleave on the panel.
  if (taskPanel.contains(event.relatedTarget)) return;
  taskPanel.classList.remove("dragover");
});
taskPanel.addEventListener("drop", (event) => {
  if (!dragHasFiles(event)) return;
  event.preventDefault();
  taskPanel.classList.remove("dragover");
  uploadFiles([...event.dataTransfer.files]);
});

// The panel is narrower than the window. A file dropped beside it would make
// the browser open the file in place of the app, losing any draft.
document.addEventListener("dragover", (event) => {
  if (event.defaultPrevented || !dragHasFiles(event)) return;
  event.preventDefault();
  if (!taskPanel.classList.contains("active")) event.dataTransfer.dropEffect = "none";
});
document.addEventListener("drop", (event) => {
  if (event.defaultPrevented || !dragHasFiles(event)) return;
  event.preventDefault();
  if (taskPanel.classList.contains("active")) uploadFiles([...event.dataTransfer.files]);
});

// Browsers call every pasted screenshot "image.png", and an upload replaces
// any file with the same name, so each one gets its own.
function namePastedFile(file, pasteTime, position) {
  if (!/^image\.\w+$/.test(file.name)) return file;
  const extension = file.name.split(".").pop();
  return new File([file], `pasted-${pasteTime}-${position}.${extension}`, { type: file.type });
}

taskPanel.addEventListener("paste", (event) => {
  const pastedFiles = [...event.clipboardData.files];
  if (pastedFiles.length === 0) return;
  // Spreadsheets copy their cells as text plus a picture of them. The text is
  // what the user wants.
  const pastedText = event.clipboardData.getData("text/plain");
  if (pastedText.trim() !== "") return;
  event.preventDefault();
  const pasteTime = Date.now();
  const namedFiles = [];
  for (const [position, file] of pastedFiles.entries()) {
    namedFiles.push(namePastedFile(file, pasteTime, position));
  }
  uploadFiles(namedFiles);
});

// ---- task run ----
// Whether a run is going, including while its POST is still in flight (when
// currentTaskId isn't known yet). runBtn.disabled can't answer this: it's also
// disabled for an empty prompt, and retry buttons run with an empty prompt.
let runInProgress = false;

function updateRunEnabled() {
  const hasPrompt = promptEl.value.trim() !== "";
  runBtn.disabled = runInProgress || !hasPrompt;
  runBtn.classList.toggle("nothing-to-send", !runInProgress && !hasPrompt);
}

function growPrompt() {
  promptEl.style.height = "auto";
  const borderHeight = promptEl.offsetHeight - promptEl.clientHeight;
  promptEl.style.height = `${promptEl.scrollHeight + borderHeight}px`;
}

function fillPrompt(text) {
  promptEl.value = text;
  growPrompt();
  updateRunEnabled();
  promptEl.focus();
  promptEl.setSelectionRange(text.length, text.length);
}

function clearPrompt() {
  promptEl.value = "";
  promptEl.style.height = "";
  updateRunEnabled();
}

function submitPrompt() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  runTask(prompt, { fromComposer: true });
}

runBtn.addEventListener("click", submitPrompt);
promptEl.addEventListener("input", () => {
  growPrompt();
  updateRunEnabled();
});
promptEl.addEventListener("keydown", (event) => {
  if (event.key === "ArrowUp" && promptEl.value === "" && lastUserPrompt) {
    event.preventDefault();
    fillPrompt(lastUserPrompt);
  }
});
updateRunEnabled();

const stopBtn = document.getElementById("stop");
let currentTaskId = null;
let activeEventSource = null; // the one live SSE connection this tab should have open

let stopRequestedTaskId = null; // the run the server has been asked to stop
let stopQueued = false; // Stop was clicked before the server gave a task id

function showStopping() {
  stopBtn.disabled = true;
  stopBtn.textContent = "Stopping…";
}

function resetStopButton() {
  stopBtn.disabled = false;
  stopBtn.replaceChildren(faIcon("stop"));
}

async function requestStop(taskId) {
  stopRequestedTaskId = taskId;
  showStopping();
  try {
    const res = await fetch(`/api/tasks/${taskId}/cancel`, { method: "POST" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch {
    // The stop didn't reach the run: let the user try again. If the run has
    // already ended, setRunning() has hidden this button anyway.
    if (stopRequestedTaskId !== taskId) return;
    stopRequestedTaskId = null;
    resetStopButton();
  }
}

stopBtn.addEventListener("click", () => {
  if (!runInProgress) return;
  if (currentTaskId) {
    requestStop(currentTaskId);
    return;
  }
  // The run's POST is still in flight. attachStream() sends the stop.
  stopQueued = true;
  showStopping();
});

// After a reload or chat switch only the server knows a stop is pending.
async function restoreStoppingState(taskId) {
  try {
    const res = await fetch(`/api/tasks/${taskId}`);
    if (!res.ok) return;
    const snapshot = await res.json();
    if (snapshot.cancelled && currentTaskId === taskId) {
      stopRequestedTaskId = taskId;
      showStopping();
    }
  } catch {
    /* best effort: Stop stays available */
  }
}

function setRunning(running) {
  runInProgress = running;
  updateRunEnabled();
  stopBtn.hidden = !running;
  // A reconnect calls this mid-run, and must not undo a pending stop.
  const stopPending = stopQueued || (currentTaskId && stopRequestedTaskId === currentTaskId);
  if (running && stopPending) {
    showStopping();
  } else {
    resetStopButton();
  }
  if (!running) {
    currentTaskId = null;
    stopRequestedTaskId = null;
    stopQueued = false;
  }
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

// options.fromComposer: the text came from the prompt box, so it's cleared
// once the server has it. A retry button leaves any draft in the box alone.
async function runTask(prompt, options = {}) {
  if (runInProgress) return; // one task at a time

  setRunning(true);
  const previousUserPrompt = lastUserPrompt;
  const userDiv = addMessage("user", prompt);
  traceStart();

  try {
    // The server skips files it can't find, so say so before the run starts.
    setTaskUploadStatus("");
    if (attached.size > 0) {
      await refreshFiles();
      const missingNames = missingAttachments();
      if (missingNames.length > 0) {
        setTaskUploadStatus(`Sent without ${missingNames.join(", ")}: no longer on disk.`);
      }
    }
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, files: [...attached], chat_id: currentChatId }),
    });
    if (!res.ok) {
      const errorBody = await res.json().catch(() => ({}));
      if (errorBody.needs_data_lake) {
        setDlHidden(false); // shows the download's progress if one is running
        dlModal.classList.add("show");
      }
      throw new Error(errorBody.error || `HTTP ${res.status}`);
    }
    const { task_id, chat_id, error } = await res.json();
    if (error) throw new Error(error);
    // Suggestions for the previous answer are out of date once a prompt is
    // sent. A failed send keeps them.
    for (const oldFollowups of messagesEl.querySelectorAll(".followups")) {
      oldFollowups.remove();
    }
    // Unless it was edited while the request was in flight.
    if (options.fromComposer && promptEl.value.trim() === prompt) clearPrompt();
    // These attachments now belong to this chat; left in the "new" bucket,
    // they'd be attached to every chat started after it.
    if (!currentChatId) localStorage.removeItem(attachedKeyFor(null));
    currentChatId = chat_id;
    localStorage.setItem("lastChatId", chat_id);
    saveAttached(); // persist under the now-real chat id, not the "new" bucket
    refreshChats();
    attachStream(task_id);
  } catch (err) {
    setRunning(false);
    traceEnd();
    // The message was never sent, so it goes back to the prompt box instead
    // of staying in the chat. A draft typed since then is kept.
    userDiv.remove();
    lastUserPrompt = previousUserPrompt;
    if (!promptEl.value.trim()) fillPrompt(prompt);
    let errorText = err.message || String(err);
    if (err instanceof TypeError) {
      errorText = "Can't reach the server. Is it still running?";
    }
    addMessage("assistant", `Your message wasn't sent: ${errorText}`, true);
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
  if (stopQueued) {
    stopQueued = false;
    requestStop(taskId);
  } else {
    restoreStoppingState(taskId);
  }
  const source = new EventSource(`/api/tasks/${taskId}/stream`);
  activeEventSource = source;
  const isStale = () => source !== activeEventSource;

  source.onmessage = (event) => {
    if (isStale()) return; // a newer stream has since taken over
    const msg = JSON.parse(event.data);
    if (msg.max_steps) runMaxSteps = msg.max_steps;
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
        const answerDiv = addMessage("assistant", msg.result, false, msg.files);
        renderFollowups(answerDiv, msg.files);
        traceEl.classList.remove("open");
      } else if (msg.cancelled && msg.error === STOPPED_ERROR_TEXT) {
        // Checked by text too: a real failure that lands just after the user
        // pressed Stop should still be shown as that failure.
        addMessage("assistant", "Stopped by user", false, msg.files, null, null, { stopped: true });
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
            traceStart(); // clearing the panel took the live status line with it
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
const dlMini = document.getElementById("dl-mini");
const dlMiniRing = document.getElementById("dl-mini-ring");
const dlMiniPct = document.getElementById("dl-mini-pct");
const DL_RING = 2 * Math.PI * 19; // the ring's circumference (r=19 in the SVG)
let dlTimer = null;
let dlLastSample = null; // {t, bytes} for the transfer-rate estimate

// Hide moves a running download from the modal to the corner ring; the
// choice survives a reload while that download is still going.
let dlHidden = false;
try { dlHidden = localStorage.getItem("dl-hidden") === "1"; } catch {}

function dlStartedBefore() {
  try { return localStorage.getItem("dl-started") === "1"; } catch { return false; }
}

function setDlHidden(hidden) {
  dlHidden = hidden;
  try { localStorage.setItem("dl-hidden", hidden ? "1" : "0"); } catch {}
  dlModal.classList.toggle("show", !hidden);
  dlMini.classList.remove("fade");
  dlMini.classList.toggle("show", hidden);
}

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
  dlMini.classList.toggle("indeterminate", !(status.bytes > 0));
  dlMiniRing.style.strokeDashoffset = DL_RING * (1 - Math.min(pct, 100) / 100);
  dlMiniPct.textContent = `${Math.floor(pct)}%`;
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

// hidden: start in the corner ring rather than the modal (a reload while a
// hidden download is still going); a download the user just started shows
// the modal.
function pollDl(hidden = false) {
  stopDlPolling();
  dlModal.classList.add("downloading");
  setDlHidden(hidden);
  try { localStorage.setItem("dl-started", "1"); } catch {}
  const tick = async () => {
    try {
      const status = await fetch("/api/datalake").then((r) => r.json());
      renderDl(status);
      if (status.state === "ready" && status.validated) {
        // Byte count matched, and the background integrity check (parquet
        // footers, json/pkl parse, non-empty text files) came back clean.
        stopDlPolling();
        if (status.merge_problems.length) {
          dlModal.classList.remove("downloading");
          setDlHidden(false);
          dlNote.textContent =
            `Data lake ready, but ${status.merge_problems.length} file(s) couldn't be merged and stayed in place: ` +
            status.merge_problems.slice(0, 5).map((problem) => `${problem.path} (${problem.reason})`).join("; ") +
            ". See the Integrity tab.";
          return;
        }
        dlFill.style.width = "100%";
        dlPercent.textContent = "100%";
        dlNote.textContent = "Data lake ready — you're all set.";
        dlMiniRing.style.strokeDashoffset = 0;
        dlMiniPct.innerHTML = '<i class="fa-solid fa-check"></i>';
        dlMini.title = "Data lake ready";
        try {
          localStorage.removeItem("dl-hidden");
          localStorage.removeItem("dl-started");
        } catch {}
        setTimeout(() => {
          dlModal.classList.remove("show", "downloading");
          dlMini.classList.add("fade");
          setTimeout(() => dlMini.classList.remove("show", "fade"), 400);
        }, 1600);
      } else if (status.state === "ready" && !status.validated) {
        dlNote.textContent = "Verifying downloaded files…";
      } else if (status.state === "invalid") {
        stopDlPolling();
        dlModal.classList.remove("downloading");
        setDlHidden(false); // needs the user: bring the modal back
        dlNote.textContent =
          `Download finished but failed an integrity check ` +
          `(${status.validation_error || "unknown error"}). Click "Download" to retry.`;
      } else if (status.state === "error") {
        stopDlPolling();
        dlModal.classList.remove("downloading");
        setDlHidden(false);
        dlNote.textContent = `Download failed: ${status.error}`;
      }
    } catch {
      /* transient poll failure — keep trying */
    }
  };
  tick(); // show where it stands now, not after the first interval
  dlTimer = setInterval(tick, 1500);
}

document.getElementById("dl-start").addEventListener("click", async () => {
  dlNote.textContent = "";
  await fetch("/api/datalake/download", { method: "POST" });
  pollDl();
});
document.getElementById("dl-later").addEventListener("click", () => {
  dlModal.classList.remove("show");
});
document.getElementById("dl-hide").addEventListener("click", () => setDlHidden(true));
dlMini.addEventListener("click", () => setDlHidden(false));

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
      pollDl(dlHidden);
    } else if (status.state === "partial" && dlStartedBefore()) {
      // Closing the app stopped a download the user had started: pick it
      // back up where it was (finished files are kept), modal or ring as before.
      renderDl(status);
      await fetch("/api/datalake/download", { method: "POST" });
      pollDl(dlHidden);
    } else if (status.state === "missing" || status.state === "partial") {
      if (status.state === "partial") {
        dlNote.textContent = `Found a partial download (${formatBytes(status.bytes)}) — downloading resumes it.`;
      }
      if (status.duplicates.length) {
        const paths = status.duplicates.map((duplicate) => duplicate.path).join(", ");
        dlNote.textContent += ` Found another copy at ${paths}: its files will be used rather than downloaded again.`;
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
  else renderEmptyState();
}

// ---- model selection ----
// Populated from GET /api/models on load (Bedrock's model list, or the
// built-in one in app/providers.py); changing either select POSTs the resolved id to
// /api/model, which rebuilds the agent for the next run. Two selects: family
// (opus, haiku, …) and that family's versions, newest first.
const familySelect = document.getElementById("model-family");
const versionSelect = document.getElementById("model-version");
const effortSelect = document.getElementById("model-effort");
let modelFamilies = [];
let currentEffort = null; // the level in effect on the server; null = default

function option(value, text) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = text;
  return opt;
}

// Always visible: while loading, or when no models are found, both selects
// show a disabled placeholder instead of disappearing from the header.
function showModelPlaceholder(text, title) {
  familySelect.replaceChildren(option("", text));
  versionSelect.replaceChildren(option("", "—"));
  familySelect.disabled = versionSelect.disabled = true;
  familySelect.title = title;
}

// Versions the endpoint lists as unavailable (e.g. not offered under the
// account's data retention mode) stay visible but can't be picked; hovering
// shows the endpoint's status_reason.
function versionOption(v) {
  const opt = option(v.id, v.available ? v.label : `${v.label} (unavailable)`);
  opt.disabled = !v.available;
  opt.title = v.status_reason || v.id;
  return opt;
}

function fillVersions(familyName, selectedId) {
  const family = modelFamilies.find((f) => f.name === familyName);
  versionSelect.replaceChildren(...family.versions.map(versionOption));
  const usable = family.versions.filter((v) => v.available);
  // Default to the newest usable version (versions arrive newest first).
  versionSelect.value =
    usable.find((v) => v.id === selectedId)?.id ?? usable[0]?.id ?? "";
  // Nothing to choose between: keep it visible, just inert.
  versionSelect.disabled = usable.length < 2;
  versionSelect.title = `Version — ${versionSelect.value || "none available"}`;
  fillEfforts();
}

// Only the levels the selected model accepts. A saved level it doesn't take
// shows as the default, which is what the run will actually use.
function fillEfforts() {
  const version = modelFamilies
    .flatMap((f) => f.versions)
    .find((v) => v.id === versionSelect.value);
  const levels = version?.efforts || [];
  effortSelect.replaceChildren(
    option("", "Default effort"),
    ...levels.map((e) => option(e, `${e} effort`)),
  );
  effortSelect.value = levels.includes(currentEffort) ? currentEffort : "";
  effortSelect.disabled = !levels.length;
  effortSelect.title = levels.length
    ? "How hard the model works on each step; higher costs more and takes longer. Applies to the next task."
    : "This model doesn't support effort levels.";
}

function renderModels(info) {
  modelFamilies = info.families || [];
  currentEffort = info.effort ?? null;
  if (!modelFamilies.length) {
    showModelPlaceholder(
      "No models found",
      "No models are configured in app/providers.py.",
    );
    return;
  }
  const current =
    modelFamilies.find((f) => f.versions.some((v) => v.id === info.current)) ||
    modelFamilies.find((f) => f.versions.some((v) => v.available)) ||
    modelFamilies[0];
  familySelect.replaceChildren(
    ...modelFamilies.map((f) => {
      const usable = f.versions.some((v) => v.available);
      const opt = option(f.name, usable ? f.name : `${f.name} (unavailable)`);
      opt.disabled = !usable;
      if (!usable) opt.title = f.versions[0].status_reason || "";
      return opt;
    }),
  );
  familySelect.value = current.name;
  familySelect.disabled = false;
  fillVersions(current.name, info.current);
  currentModel = versionSelect.value || info.current;
  familySelect.title = "Model for new runs, on Bedrock. Switching applies to the next task.";
}

// Render instantly from the list embedded in the page, then re-check the
// API once in the background: the backend may have finished re-discovering
// since render. Only re-render when the list actually changed.
async function initModels() {
  let embedded = null;
  try {
    embedded = JSON.parse(document.getElementById("models-data").textContent);
    renderModels(embedded);
  } catch {
    showModelPlaceholder("Models unavailable", "Couldn't load the model list.");
  }
  // The page may have rendered the built-in list while the backend was still
  // asking Bedrock for its models; check back a few times for the real list.
  let shown = embedded;
  for (const wait of [1500, 4000, 10000]) {
    if (shown?.live) break;
    await new Promise((r) => setTimeout(r, wait));
    try {
      const info = await fetch("/api/models").then((r) => r.json());
      const same =
        shown &&
        JSON.stringify(info.families) === JSON.stringify(shown.families) &&
        info.current === shown.current;
      if (!same) renderModels(info);
      shown = info;
    } catch {
      /* keep whatever rendered from the embedded list */
    }
  }
}

// The model in effect on the server; the selects fall back to it when a
// switch fails, so they never show a model the next run won't use.
let currentModel = null;

function showModel(modelId) {
  const family = modelFamilies.find((f) => f.versions.some((v) => v.id === modelId));
  if (family) familySelect.value = family.name;
  fillVersions(familySelect.value, modelId);
}

async function applyModel() {
  const model = versionSelect.value;
  familySelect.disabled = versionSelect.disabled = true;
  familySelect.classList.add("saving");
  versionSelect.classList.add("saving");
  try {
    const res = await fetch("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const body = await res.json();
    currentModel = body.current ?? model;
  } catch (err) {
    console.warn("model switch failed; keeping", currentModel, err);
  } finally {
    familySelect.classList.remove("saving");
    versionSelect.classList.remove("saving");
    familySelect.disabled = false;
    showModel(currentModel); // what the server has, success or not
  }
}

// Switching family picks its newest version (first in the list).
familySelect.addEventListener("change", () => {
  fillVersions(familySelect.value);
  applyModel();
});
versionSelect.addEventListener("change", applyModel);

effortSelect.addEventListener("change", async () => {
  try {
    const res = await fetch("/api/effort", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ effort: effortSelect.value }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    currentEffort = (await res.json()).effort;
  } catch (err) {
    console.warn("effort switch failed; keeping", currentEffort, err);
  }
  fillEfforts(); // what the server has, success or not
});

initModels();


// ---- integrity tab: pass/fail checks for the data lake, the agent, and
// the agent harness, from GET /api/integrity (checks are computed on the
// server). Polls while the tab is open — fast while anything is pending
// (download, verification, model-list fetch), slow otherwise. ----
const integritySummaryEl = document.getElementById("integrity-summary");
const integrityCardsEl = document.getElementById("integrity-cards");
const integrityBadgeEl = document.getElementById("integrity-badge");
let integrityTimer = null;
const harnessOpen = new Set(); // "<kind>/<name>" rows the user expanded
// File text by "<kind>/<name>", tagged with the mtime it was read at, so a
// re-render fills an open row instantly instead of flashing "Loading…"
// (which collapsed the row, shrank the page and jumped the scroll).
const harnessContent = new Map();
let lastIntegritySig = null; // skip re-rendering when nothing changed

const STATUS_ICON = {
  pass: "fa-circle-check",
  warn: "fa-triangle-exclamation",
  fail: "fa-circle-xmark",
  pending: "fa-circle-notch fa-spin", // only for work actually in progress
  info: "fa-circle-info", // a normal resting state, nothing to wait for
};

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function statusIcon(status) {
  const i = el("i", `fa-solid ${STATUS_ICON[status] || "fa-circle-info"} st-${status}`);
  i.setAttribute("aria-label", status);
  return i;
}

function worstStatus(checks) {
  for (const s of ["fail", "warn", "pending"]) if (checks.some((c) => c.status === s)) return s;
  return "pass";
}

function integrityCard(icon, title, sub, checks) {
  const card = el("section", "integrity-card");
  const head = el("header");
  head.append(statusIcon(worstStatus(checks)), el("i", `fa-solid ${icon}`), el("h2", null, title));
  if (sub) { const s = el("span", "sub", sub); s.title = sub; head.append(s); }
  card.append(head);
  for (const c of checks) {
    const row = el("div", "check-row");
    row.append(statusIcon(c.status), el("span", "label", c.label), el("span", "detail", c.detail));
    card.append(row);
  }
  return card;
}

function datalakeExtra(d) {
  const extra = el("div", "card-extra");
  if (d.invalid_files?.length) {
    extra.append(el("div", null, "Files that failed verification:"));
    const ul = el("ul");
    ul.dataset.scrollKey = "datalake:invalid";
    for (const f of d.invalid_files) ul.append(el("li", null, typeof f === "string" ? f : JSON.stringify(f)));
    extra.append(ul);
  }
  if (d.can_merge) {
    const mergeBtn = el("button", null, "Merge duplicates");
    mergeBtn.addEventListener("click", async () => {
      mergeBtn.disabled = true;
      dlNote.textContent = "";
      await fetch("/api/datalake/download", { method: "POST" });
      pollDl();
      refreshIntegrity();
    });
    extra.append(mergeBtn);
  }
  if (d.can_download) {
    const btn = el("button", null, d.state === "invalid" ? "Repair data lake" : "Download data lake");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      dlNote.textContent = "";
      await fetch("/api/datalake/download", { method: "POST" });
      pollDl(); // the same progress modal the first-run download uses
      refreshIntegrity();
    });
    extra.append(btn);
  }
  return extra.childElementCount ? extra : null;
}

function agentExtra(a) {
  if (!a.active.length) return null;
  const extra = el("div", "card-extra");
  extra.append(el("div", null, "Active runs (oldest first):"));
  const ul = el("ul");
  ul.dataset.scrollKey = "agent:active";
  for (const t of a.active) {
    const started = new Date(t.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    ul.append(el("li", null, `${t.status} · ${started} · ${t.prompt || t.id}`));
  }
  extra.append(ul);
  return extra;
}

function harnessItem(item) {
  const key = `${item.kind}/${item.name}`;
  const details = el("details", "harness-item");
  const summary = el("summary");
  const saved = new Date(item.modified * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
  summary.append(
    statusIcon(item.error ? "fail" : "pass"),
    el("span", "kind", item.kind === "recipes" ? "recipe" : "capability"),
    el("span", "name", item.name),
    el("span", "meta", `${formatBytes(item.size)} · ${saved}`),
  );
  if (item.error) summary.title = item.error;
  details.append(summary);
  const cached = harnessContent.get(key);
  const fresh = cached && cached.modified === item.modified;
  const pre = el("pre", null, fresh ? cached.text : "Loading…");
  pre.dataset.scrollKey = `harness:${key}`;
  details.append(pre);
  const load = async () => {
    const hit = harnessContent.get(key);
    if (hit && hit.modified === item.modified) {
      pre.textContent = hit.text; // already have this version — no refetch
      return;
    }
    try {
      const res = await fetch(`/api/harness/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.name)}`);
      const text = res.ok ? await res.text() : `Couldn't load (HTTP ${res.status})`;
      if (res.ok) harnessContent.set(key, { modified: item.modified, text });
      pre.textContent = text;
    } catch (err) {
      pre.textContent = `Couldn't load: ${err.message || err}`;
    }
  };
  // Restoring the open state still fires "toggle" (it's dispatched async),
  // so the handler ignores rows already marked open — only a real click
  // on a closed row loads.
  if (harnessOpen.has(key)) details.open = true;
  details.addEventListener("toggle", () => {
    if (!details.open) harnessOpen.delete(key);
    else if (!harnessOpen.has(key)) { harnessOpen.add(key); load(); }
  });
  if (details.open && !fresh) load(); // open row whose file changed on disk
  return details;
}

// Rebuilding the cards replaces every element, which resets the scroll
// offset of each inner scroll box (and can momentarily change the page
// height). Remember offsets by data-scroll-key and put them back.
function preserveScroll(container, rebuild) {
  const offsets = new Map();
  container.querySelectorAll("[data-scroll-key]").forEach((node) => {
    if (node.scrollTop || node.scrollLeft) {
      offsets.set(node.dataset.scrollKey, [node.scrollTop, node.scrollLeft]);
    }
  });
  const pageY = window.scrollY;
  rebuild();
  container.querySelectorAll("[data-scroll-key]").forEach((node) => {
    const o = offsets.get(node.dataset.scrollKey);
    if (o) [node.scrollTop, node.scrollLeft] = o;
  });
  if (window.scrollY !== pageY) window.scrollTo({ top: pageY });
}

function renderIntegrity(data) {
  const cards = [
    ["fa-database", "Data lake", data.datalake.path, data.datalake.checks, datalakeExtra(data.datalake)],
    ["fa-heart-pulse", "Agent health", `Bedrock · ${data.agent.model || "no model"}`,
      data.agent.checks, agentExtra(data.agent)],
    ["fa-book", "Agent harness", data.harness.path, data.harness.checks, null],
  ];
  const all = cards.flatMap((c) => c[3]);
  const failing = all.filter((c) => c.status === "fail").length;
  const warning = all.filter((c) => c.status === "warn").length;
  const pending = all.filter((c) => c.status === "pending").length;
  integrityBadgeEl.textContent = failing || "";

  const overall = failing ? "fail" : warning ? "warn" : pending ? "pending" : "pass";
  const headline = failing
    ? `${failing} check${failing > 1 ? "s" : ""} failing`
    : warning ? `${warning} warning${warning > 1 ? "s" : ""}`
    : pending ? "Checks in progress…" : "All checks passing";
  const refresh = el("button", "refresh", "Re-check");
  refresh.addEventListener("click", () => refreshIntegrity());
  integritySummaryEl.replaceChildren(
    statusIcon(overall), el("strong", null, headline),
    el("span", "when", `updated ${new Date().toLocaleTimeString()}`), refresh,
  );

  // Most polls return exactly what's already on screen — leave the cards
  // (and whatever the user has scrolled or expanded) untouched then.
  const sig = JSON.stringify(data);
  if (sig === lastIntegritySig) return pending > 0;
  lastIntegritySig = sig;

  preserveScroll(integrityCardsEl, () => {
    const nodes = cards.map(([icon, title, sub, checks, extra]) => {
      const card = integrityCard(icon, title, sub, checks);
      if (extra) card.append(extra);
      return card;
    });
    // Harness files go inside their card, under its checks.
    for (const item of data.harness.items) nodes[2].append(harnessItem(item));
    integrityCardsEl.replaceChildren(...nodes);
  });
  return pending > 0;
}

async function refreshIntegrity() {
  let pending = false;
  try {
    const res = await fetch("/api/integrity");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    pending = renderIntegrity(await res.json());
  } catch (err) {
    integritySummaryEl.replaceChildren(
      statusIcon("fail"), el("strong", null, `Couldn't load integrity checks: ${err.message || err}`),
    );
  }
  return pending;
}

let integrityGen = 0; // bumped on stop, so an in-flight tick can't reschedule

async function startIntegrity() {
  stopIntegrity();
  const gen = integrityGen;
  const tick = async () => {
    const pending = await refreshIntegrity();
    if (gen !== integrityGen) return; // tab was left while this tick ran
    integrityTimer = setTimeout(tick, pending ? 3000 : 15000);
  };
  tick();
}

function stopIntegrity() {
  integrityGen += 1;
  if (integrityTimer) clearTimeout(integrityTimer);
  integrityTimer = null;
}

refreshIntegrity(); // fill the tab badge on load, without polling

// ---- spending tab: AWS costs from GET /api/spending. Fetched when the tab
// is opened rather than polled: the server caches because Cost Explorer
// charges per request, and its data only updates a few times a day.
const spendingSummaryEl = document.getElementById("spending-summary");
const spendingBodyEl = document.getElementById("spending-body");
let spendingLoaded = false; // real figures are on screen
let spendingRequest = 0; // bumped per action, so a slow fetch can't overwrite a newer one

function formatMoney(amount, currency) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
}

function spendingTile(label, value, note, isHero) {
  const tile = el("div", isHero ? "spend-tile hero" : "spend-tile");
  tile.append(el("div", "label", label), el("div", "value", value));
  if (note) tile.append(el("div", "note", note));
  return tile;
}

// Evenly spaced axis steps with a round size: 1, 2 or 5 times a power of ten.
function spendingAxisStep(max) {
  const rough = max / 3;
  const power = 10 ** Math.floor(Math.log10(rough));
  for (const multiple of [1, 2, 5]) {
    if (multiple * power >= rough) return multiple * power;
  }
  return 10 * power;
}

function dayLabel(isoDate) {
  // Parsed as UTC and shown as UTC: these are Cost Explorer's UTC days.
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString(undefined, {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

function spendingDailyCard(daily, currency) {
  const card = el("section", "spend-card");
  card.append(
    el("h2", null, "Daily spend"),
    el("p", "note", "Last 30 days, UTC. The faded bar is today, which is still incomplete."),
  );

  let highest = 0;
  for (const day of daily) highest = Math.max(highest, day.amount);
  const step = highest > 0 ? spendingAxisStep(highest) : 1;
  const top = Math.max(Math.ceil(highest / step), 1) * step;

  const plot = el("div", "spend-plot");
  for (let value = step; value <= top + step / 1000; value += step) {
    const line = el("div", "spend-grid");
    line.style.bottom = `${(value / top) * 100}%`;
    line.append(el("span", null, formatMoney(value, currency)));
    plot.append(line);
  }

  const tooltip = el("div", "spend-tooltip");
  tooltip.hidden = true;
  daily.forEach((day, index) => {
    const column = el("button", "spend-column");
    const text = `${dayLabel(day.date)}: ${formatMoney(day.amount, currency)}`;
    column.setAttribute("aria-label", text);
    const bar = el("div", "spend-bar");
    if (index === daily.length - 1) bar.classList.add("partial");
    bar.style.height = `${(day.amount / top) * 100}%`;
    column.append(bar);
    const show = () => {
      tooltip.textContent = text;
      tooltip.hidden = false;
      const plotBox = plot.getBoundingClientRect();
      const columnBox = column.getBoundingClientRect();
      const halfWidth = tooltip.offsetWidth / 2;
      const center = columnBox.left - plotBox.left + columnBox.width / 2;
      tooltip.style.left = `${Math.min(Math.max(center, halfWidth), plotBox.width - halfWidth)}px`;
    };
    const hide = () => { tooltip.hidden = true; };
    column.addEventListener("mouseenter", show);
    column.addEventListener("focus", show);
    column.addEventListener("mouseleave", hide);
    column.addEventListener("blur", hide);
    plot.append(column);
  });
  plot.append(tooltip);

  const axis = el("div", "spend-axis");
  if (daily.length) {
    axis.append(el("span", null, dayLabel(daily[0].date)), el("span", null, dayLabel(daily[daily.length - 1].date)));
  }

  const tableWrap = el("details", "spend-table");
  tableWrap.append(el("summary", null, "Show as table"));
  const table = el("table");
  for (const day of daily) {
    const row = el("tr");
    row.append(el("td", null, dayLabel(day.date)), el("td", null, formatMoney(day.amount, currency)));
    table.append(row);
  }
  tableWrap.append(table);

  card.append(plot, axis, tableWrap);
  return card;
}

function spendingServicesCard(services, currency) {
  const card = el("section", "spend-card");
  card.append(el("h2", null, "By service"), el("p", "note", "This month so far."));
  let highest = 0;
  for (const service of services) highest = Math.max(highest, service.amount);
  if (!services.length) card.append(el("p", "note", "No spend recorded yet this month."));
  for (const service of services) {
    const row = el("div", "spend-service");
    const track = el("div", "track");
    const fill = el("div", "fill");
    fill.style.width = `${highest > 0 ? (service.amount / highest) * 100 : 0}%`;
    track.append(fill);
    row.append(
      el("div", "name", service.name), track,
      el("div", "amount", formatMoney(service.amount, currency)),
    );
    card.append(row);
  }
  return card;
}

// Grey shapes in the layout of the real tab, while the first fetch runs.
function renderSpendingLoading() {
  spendingSummaryEl.replaceChildren(
    faIcon("coins"), el("strong", null, "AWS spending"), el("span", "when", "Loading…"),
  );
  const tiles = el("div", "spend-tiles");
  for (const className of ["spend-tile hero", "spend-tile", "spend-tile"]) {
    const tile = el("div", className);
    tile.append(el("div", "spend-skeleton line"), el("div", "spend-skeleton value"));
    tiles.append(tile);
  }
  const card = el("section", "spend-card");
  card.append(el("div", "spend-skeleton line"), el("div", "spend-skeleton plot"));
  spendingBodyEl.replaceChildren(tiles, card);
  spendingBodyEl.setAttribute("aria-busy", "true");
}

// Made-up figures in the API's shape, for checking the tab's layout before
// the AWS permission exists. Always labelled as examples on screen.
function spendingExampleData() {
  const daily = [];
  const now = new Date();
  for (let daysAgo = 29; daysAgo >= 0; daysAgo--) {
    const day = new Date(now.getTime() - daysAgo * 86400000);
    // A weekly rhythm, so the bars look like real usage; today is incomplete.
    let amount = 38 + 14 * Math.sin(daysAgo * 0.9) + (daysAgo % 7 === 2 ? 22 : 0);
    if (daysAgo === 0) amount = 11;
    daily.push({ date: day.toISOString().slice(0, 10), amount });
  }
  return {
    currency: "USD",
    month_to_date: 412.58,
    projected_month: 1236.4,
    previous_month: 1184.92,
    previous_by_this_day: 386.17,
    daily,
    services: [
      { name: "Amazon Bedrock", amount: 248.31 },
      { name: "Amazon S3", amount: 71.12 },
      { name: "Amazon EC2", amount: 54.76 },
      { name: "AWS Lambda", amount: 21.4 },
      { name: "Other", amount: 16.99 },
    ],
    fetched_at: Date.now() / 1000,
  };
}

function showSpendingExample() {
  spendingRequest += 1; // a fetch still running shouldn't replace the example
  renderSpending(spendingExampleData(), true);
}

function renderSpending(data, isExample = false) {
  spendingBodyEl.removeAttribute("aria-busy");
  spendingLoaded = !isExample;
  const money = (amount) => formatMoney(amount, data.currency);
  const fetched = new Date(data.fetched_at * 1000).toLocaleTimeString();
  const refresh = el("button", "refresh", isExample ? "Back to live data" : "Refresh");
  refresh.addEventListener("click", () => refreshSpending(true));
  if (isExample) {
    spendingSummaryEl.replaceChildren(
      statusIcon("warn"), el("strong", null, "Example data"),
      el("span", "when", "made-up figures, not your AWS costs"), refresh,
    );
  } else {
    spendingSummaryEl.replaceChildren(
      faIcon("coins"), el("strong", null, "AWS spending"),
      el("span", "when", `whole account, before credits · fetched ${fetched}`), refresh,
    );
  }

  const tiles = el("div", "spend-tiles");
  tiles.append(
    spendingTile("This month so far", money(data.month_to_date),
      `${money(data.previous_by_this_day)} by this day last month`, true),
    spendingTile("Projected this month",
      data.projected_month === null ? "—" : money(data.projected_month),
      data.projected_month === null ? "AWS has no forecast yet" : "AWS forecast"),
    spendingTile("Last month", money(data.previous_month), "Full month"),
  );
  spendingBodyEl.replaceChildren(
    tiles,
    spendingDailyCard(data.daily, data.currency),
    spendingServicesCard(data.services, data.currency),
  );
}

async function refreshSpending(force = false) {
  spendingRequest += 1;
  const request = spendingRequest;
  // Real figures stay on screen while they update; otherwise show the shapes.
  if (!spendingLoaded) renderSpendingLoading();
  try {
    const res = await fetch(`/api/spending${force ? "?refresh=1" : ""}`);
    const data = await res.json();
    if (request !== spendingRequest) return; // a newer action took over
    if (!res.ok) {
      const error = new Error(data.error || `HTTP ${res.status}`);
      error.code = data.code;
      throw error;
    }
    renderSpending(data);
  } catch (err) {
    if (request !== spendingRequest) return;
    spendingLoaded = false;
    spendingBodyEl.removeAttribute("aria-busy");
    const retry = el("button", "refresh", "Try again");
    retry.addEventListener("click", () => refreshSpending(true));
    spendingSummaryEl.replaceChildren(
      statusIcon(err.code === "permission" ? "warn" : "fail"),
      el("strong", null, err.message || String(err)), retry,
    );
    const body = el("section", "spend-card spend-message");
    if (err.code === "permission") {
      body.append(el("p", "note", "Add ce:GetCostAndUsage and ce:GetCostForecast to the role, then try again. Permission changes can take a minute to apply."));
    }
    const example = el("button", "refresh", "Preview with example data");
    example.addEventListener("click", showSpendingExample);
    body.append(example);
    spendingBodyEl.replaceChildren(body);
  }
}

// ---- settings ----
const settingsVersionEl = document.getElementById("settings-version");
const settingsBetaEl = document.getElementById("settings-beta");
const settingsBetaHintEl = document.getElementById("settings-beta-hint");
const settingsMessageEl = document.getElementById("settings-message");
const settingsSetupBtn = document.getElementById("settings-setup");
const settingsCleanupBtn = document.getElementById("settings-cleanup");

const BETA_HINT = "Each start updates to the newest release, test builds included. They can be less stable. Turning this off keeps your version until a newer full release comes out.";
const BETA_HINT_CHECKOUT = "This copy runs from a git checkout, which updates with git, so this has no effect here.";

async function refreshSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    settingsVersionEl.textContent = data.version;
    settingsBetaEl.checked = data.beta_updates;
    settingsBetaEl.disabled = data.git_checkout;
    settingsBetaHintEl.textContent = data.git_checkout ? BETA_HINT_CHECKOUT : BETA_HINT;
  } catch (err) {
    settingsMessageEl.textContent = `Couldn't load settings: ${err.message || err}`;
  }
}

settingsBetaEl.addEventListener("change", async () => {
  const wanted = settingsBetaEl.checked;
  settingsBetaEl.disabled = true;
  settingsMessageEl.textContent = "";
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ beta_updates: wanted }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    settingsMessageEl.textContent = wanted
      ? "Beta releases are on. This applies the next time you open biomnikhil."
      : "Beta releases are off.";
  } catch (err) {
    settingsBetaEl.checked = !wanted;
    settingsMessageEl.textContent = `Couldn't save that: ${err.message || err}`;
  }
  settingsBetaEl.disabled = false;
});

async function runMaintenance(action, confirmation, closingMessage) {
  const ok = await confirmModal(confirmation.message, {
    title: confirmation.title, confirmLabel: confirmation.label,
  });
  if (!ok) return;
  settingsSetupBtn.disabled = true;
  settingsCleanupBtn.disabled = true;
  settingsMessageEl.textContent = "";
  try {
    const res = await fetch(`/api/maintenance/${action}`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    settingsMessageEl.textContent = closingMessage;
  } catch (err) {
    settingsMessageEl.textContent = err.message || String(err);
    settingsSetupBtn.disabled = false;
    settingsCleanupBtn.disabled = false;
  }
}

settingsSetupBtn.addEventListener("click", () => {
  runMaintenance("setup", {
    title: "Run setup again?",
    message: "biomnikhil will close, rebuild its Python environment and reopen. This needs an internet connection and can take a few minutes. Your chats, settings and data lake stay.",
    label: "Run setup",
  }, "Closing biomnikhil to run setup. It reopens when setup is done.");
});

settingsCleanupBtn.addEventListener("click", () => {
  runMaintenance("cleanup", {
    title: "Clean up?",
    message: "biomnikhil will close and a separate window will open. It lists files found outside this install and asks about each one, and nothing is deleted unless you say yes there. biomnikhil reopens when you're done.",
    label: "Continue",
  }, "Closing biomnikhil to clean up. Answer the questions in the window that opens.");
});

document.getElementById("user-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  document.querySelector('.tabs button[data-tab="settings"]').click();
});
