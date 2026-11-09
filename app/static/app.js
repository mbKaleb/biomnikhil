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
const attachmentBadgeEl = document.getElementById("attachment-count-badge");
const attachmentChipsEl = document.getElementById("attachment-chips");
const attachmentsEmptyEl = document.getElementById("attachments-empty");
const attachmentsLockedNote = document.getElementById("attachments-locked-note");
const attachmentsChooseBtn = document.getElementById("attachments-choose");
const uploadHintEl = document.getElementById("upload-hint");
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
  thinkingStatusEl.textContent = text.charAt(0).toUpperCase() + text.slice(1);
}

// The chat's "agent is working" line, under the last message during a run.
// It mirrors the trace's status line and opens the trace when clicked. It's
// hidden while the agent waits for the user to answer a question.
const thinkingEl = document.createElement("button");
thinkingEl.type = "button";
thinkingEl.className = "thinking";
thinkingEl.title = "Show the agent's steps";
const thinkingDots = document.createElement("span");
thinkingDots.className = "thinking-dots";
for (let i = 0; i < 3; i++) {
  thinkingDots.appendChild(document.createElement("span"));
}
const thinkingStatusEl = document.createElement("span");
thinkingStatusEl.className = "thinking-status";
thinkingEl.append(thinkingDots, thinkingStatusEl);
thinkingEl.addEventListener("click", () => traceOpen());

function showThinking() {
  const wasNearBottom = messagesNearBottom();
  messagesEl.appendChild(thinkingEl); // moves it below anything added since
  if (wasNearBottom) thinkingEl.scrollIntoView({ block: "end" });
}

function hideThinking() {
  thinkingEl.remove();
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
  showThinking();
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
  hideThinking();
  stopElapsed();
}
document.getElementById("trace-btn").addEventListener("click", () =>
  traceEl.classList.toggle("open")
);
// Cmd+J on a Mac, Alt+J on Windows (VS Code's panel toggle), same split as the sidebar's B.
const traceKeyLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘J" : "Alt+J";
document.getElementById("trace-btn").title = `Agent trace (${traceKeyLabel})`;
document.addEventListener("keydown", (e) => {
  const isTraceKey = e.code === "KeyJ" && (e.metaKey || e.altKey) && !e.ctrlKey && !e.shiftKey;
  if (!isTraceKey) return;
  e.preventDefault();
  traceEl.classList.toggle("open");
});
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
let focusBeforeSidebar = null;

function openSidebar() {
  sidebar.classList.add("open");
  backdrop.classList.add("show");
  // Leave the composer, so the number keys pick a chat instead of typing.
  focusBeforeSidebar = document.activeElement;
  document.activeElement?.blur();
  refreshChats();
}
function closeSidebar() {
  closeChatMenu();
  sidebar.classList.remove("open");
  backdrop.classList.remove("show");
  // Back to where the user was, unless closing already moved focus somewhere new.
  const focusIsLost = document.activeElement === document.body || sidebar.contains(document.activeElement);
  if (focusIsLost) focusBeforeSidebar?.focus();
  focusBeforeSidebar = null;
}
menuBtn.addEventListener("click", () =>
  sidebar.classList.contains("open") ? closeSidebar() : openSidebar()
);
backdrop.addEventListener("click", closeSidebar);
document.getElementById("close-sidebar").addEventListener("click", closeSidebar);

// ---- sidebar resize (width remembered across reloads) ----
const SIDEBAR_WIDTH_KEY = "sidebarWidth";
const SIDEBAR_MIN = 240;
const SIDEBAR_MAX = 640;
function setSidebarWidth(px) {
  // The default width is half the window, so dragging must be able to reach it.
  const largest = Math.max(SIDEBAR_MAX, window.innerWidth / 2);
  const clamped = Math.min(largest, Math.max(SIDEBAR_MIN, px));
  document.documentElement.style.setProperty("--sidebar-w", clamped + "px");
  return clamped;
}
try {
  const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
  if (saved) setSidebarWidth(saved);
} catch (_) {}
const resizeHandle = document.getElementById("sidebar-resize");
resizeHandle.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  resizeHandle.setPointerCapture(e.pointerId);
  document.body.classList.add("resizing-sidebar");
});
resizeHandle.addEventListener("pointermove", (e) => {
  if (!resizeHandle.hasPointerCapture(e.pointerId)) return;
  setSidebarWidth(e.clientX);
});
resizeHandle.addEventListener("pointerup", (e) => {
  document.body.classList.remove("resizing-sidebar");
  const width = Math.round(sidebar.getBoundingClientRect().width);
  try { localStorage.setItem(SIDEBAR_WIDTH_KEY, String(width)); } catch (_) {}
});

// Show the shortcut that matches this platform (Cmd+B on Mac, Alt+B elsewhere).
const toggleKeyLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘B" : "Alt+B";
document.querySelector(".toggle-hint").textContent = toggleKeyLabel;
menuBtn.title = `Chats (${toggleKeyLabel})`;
const newChatKeyLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘N" : "Alt+N";
const headerNewChatBtn = document.getElementById("header-new-chat");
headerNewChatBtn.title = `New chat (${newChatKeyLabel})`;
headerNewChatBtn.addEventListener("click", () => newChatBtn.click());
// A dialog or the tour on top of the sidebar keeps the keyboard to itself.
function sidebarIsCovered() {
  if (confirmModalEl.classList.contains("show")) return true;
  if (previewModal.classList.contains("show")) return true;
  return document.getElementById("tour") !== null;
}
document.addEventListener("keydown", (e) => {
  if (sidebarIsCovered()) return;
  // Esc peels off the row menu first, leaving the sidebar open.
  if (e.key === "Escape" && chatMenu) {
    closeChatMenu({ returnFocus: true });
    return;
  }
  // Chat rename uses Esc to cancel, so the sidebar stays open for that one.
  if (e.key === "Escape" && e.target.tagName !== "INPUT") closeSidebar();
  // Cmd+B on a Mac, Alt+B on Windows. e.code, because Alt+B types a symbol on a Mac.
  const isToggleKey = e.code === "KeyB" && (e.metaKey || e.altKey) && !e.ctrlKey && !e.shiftKey;
  if (isToggleKey) {
    e.preventDefault();
    sidebar.classList.contains("open") ? closeSidebar() : openSidebar();
  }
  // Cmd+N on a Mac, Alt+N on Windows, from anywhere.
  const isNewChatKey = e.code === "KeyN" && (e.metaKey || e.altKey) && !e.ctrlKey && !e.shiftKey;
  if (isNewChatKey && !e.repeat) {
    e.preventDefault();
    newChatBtn.click();
  }
  // With the sidebar open, N starts a new chat and 1-9 opens that chat, newest first.
  const isTyping = e.target.matches?.("input, textarea, [contenteditable]");
  const hasModifier = e.metaKey || e.altKey || e.ctrlKey;
  if (sidebar.classList.contains("open") && !isTyping && !hasModifier && !e.repeat) {
    if (e.key === "n" || e.key === "N") newChatBtn.click();
    if (/^[1-9]$/.test(e.key)) {
      const chatId = chatShortcutIds[Number(e.key) - 1];
      if (chatId) selectChat(chatId);
    }
  }
});

// ---- chat list ----
const CHAT_SHORTCUT_COUNT = 9;
let chatShortcutIds = [];

async function refreshChats() {
  try {
    const res = await fetch("/api/chats");
    if (!res.ok) throw new Error(`server returned HTTP ${res.status}`);
    const { chats } = await res.json();
    // The rows are about to be rebuilt, and the menu belongs to one of them.
    closeChatMenu();
    chatListEl.innerHTML = "";
    chatShortcutIds = [];
    if (!chats || chats.length === 0) {
      chatListEl.innerHTML = '<li class="empty">No chats yet</li>';
      return;
    }
    let currentGroup = null;
    for (const chat of chats) {
      const group = chatDateGroup(chat.updated);
      if (group !== currentGroup) {
        currentGroup = group;
        const heading = document.createElement("li");
        heading.className = "chat-group";
        heading.textContent = group;
        chatListEl.appendChild(heading);
      }
      const row = chatRow(chat);
      if (chatShortcutIds.length < CHAT_SHORTCUT_COUNT) {
        chatShortcutIds.push(chat.id);
        const key = document.createElement("kbd");
        key.className = "chat-key";
        key.setAttribute("aria-hidden", "true");
        key.textContent = chatShortcutIds.length;
        row.querySelector(".chat-meta").after(key);
      }
      chatListEl.appendChild(row);
      if (chat.id === currentChatId) setChatName(chat.title);
    }
  } catch (err) {
    // A network-level failure (server unreachable) throws a generic
    // "Failed to fetch" here — distinguish that from a real HTTP error so
    // the message actually points at the right thing to check.
    const detail = err instanceof TypeError
      ? "can't reach the server — is it still running?"
      : err.message || String(err);
    chatShortcutIds = [];
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

// Chats arrive newest first, so each group only needs a heading where it starts.
function chatDateGroup(ts) {
  const startOfToday = new Date().setHours(0, 0, 0, 0) / 1000;
  if (ts >= startOfToday) return "Today";
  if (ts >= startOfToday - 86400) return "Yesterday";
  if (ts >= startOfToday - 86400 * 7) return "Previous 7 days";
  if (ts >= startOfToday - 86400 * 30) return "Previous 30 days";
  return "Older";
}

function chatRow(chat) {
  const li = document.createElement("li");
  li.tabIndex = 0;
  if (chat.id === currentChatId) {
    li.classList.add("active");
    li.setAttribute("aria-current", "true");
  }

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

  const moreBtn = document.createElement("button");
  moreBtn.className = "chat-more";
  moreBtn.title = "More options";
  moreBtn.setAttribute("aria-label", `More options for ${chat.title}`);
  moreBtn.setAttribute("aria-haspopup", "menu");
  moreBtn.innerHTML = '<i class="fa-solid fa-ellipsis"></i>';
  moreBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleChatMenu(moreBtn, li, chat, meta);
  });

  li.append(meta, moreBtn);
  li.addEventListener("click", () => selectChat(chat.id));
  li.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target === li) selectChat(chat.id);
  });
  return li;
}

// One row menu at a time: { element, anchor, row }, or null when none is open.
let chatMenu = null;

function closeChatMenu({ returnFocus = false } = {}) {
  if (!chatMenu) return;
  const { element, anchor, row } = chatMenu;
  chatMenu = null;
  element.remove();
  row.classList.remove("menu-open");
  if (returnFocus) anchor.focus();
}

function chatMenuItem(label, iconClass, onChoose) {
  const item = document.createElement("button");
  item.setAttribute("role", "menuitem");
  const icon = document.createElement("i");
  icon.className = `fa-solid ${iconClass}`;
  item.append(icon, label);
  item.addEventListener("click", (e) => {
    e.stopPropagation();
    closeChatMenu();
    onChoose();
  });
  return item;
}

async function deleteChat(chat) {
  const confirmed = await confirmModal(`Delete "${chat.title}"?`, { title: "Delete chat?" });
  if (!confirmed) return;
  await fetch(`/api/chats/${chat.id}`, { method: "DELETE" });
  if (chat.id === currentChatId) startNewChat();
  refreshChats();
}

// The menu hangs off the sidebar rather than the row, so the scrolling list can't clip it.
function positionChatMenu(menu, anchor) {
  const gap = 4;
  const sidebarBox = sidebar.getBoundingClientRect();
  const anchorBox = anchor.getBoundingClientRect();
  const fitsBelow = anchorBox.bottom + gap + menu.offsetHeight <= sidebarBox.bottom;
  const viewportTop = fitsBelow ? anchorBox.bottom + gap : anchorBox.top - gap - menu.offsetHeight;
  menu.style.top = `${viewportTop - sidebarBox.top}px`;
  menu.style.left = `${anchorBox.right - sidebarBox.left - menu.offsetWidth}px`;
}

function toggleChatMenu(anchor, li, chat, metaEl) {
  const wasOpenHere = chatMenu !== null && chatMenu.anchor === anchor;
  closeChatMenu();
  if (wasOpenHere) return;

  const menu = document.createElement("div");
  menu.id = "chat-menu";
  menu.setAttribute("role", "menu");
  const renameItem = chatMenuItem("Rename", "fa-pen", () => startRename(li, chat, metaEl));
  const deleteItem = chatMenuItem("Delete", "fa-trash", () => deleteChat(chat));
  deleteItem.classList.add("chat-delete");
  menu.append(renameItem, deleteItem);
  menu.addEventListener("click", (e) => e.stopPropagation());
  menu.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const nextItem = document.activeElement === renameItem ? deleteItem : renameItem;
    nextItem.focus();
  });

  sidebar.appendChild(menu);
  positionChatMenu(menu, anchor);
  li.classList.add("menu-open");
  chatMenu = { element: menu, anchor, row: li };
  renameItem.focus({ preventScroll: true });
}

document.addEventListener("click", () => closeChatMenu());
chatListEl.addEventListener("scroll", () => closeChatMenu());

function startRename(li, chat, metaEl) {
  const input = document.createElement("input");
  input.value = chat.title;
  li.replaceChild(input, metaEl);
  li.classList.add("renaming");
  input.focus();
  input.select();
  let finished = false;
  const done = async (save) => {
    // Rebuilding the list blurs the input, which would otherwise save a cancelled edit.
    if (finished) return;
    finished = true;
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
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    actions.appendChild(copy);
    if (!isError && !options.stopped) actions.appendChild(buildSaveLeadButton(text));
    div.appendChild(actions);
  }
  messagesEl.appendChild(div);
  // Someone reading further up shouldn't be pulled away by a new answer.
  if (!options.noScroll && (role === "user" || wasNearBottom)) {
    div.scrollIntoView({ block: "end" });
  }
  return div;
}

// A question the agent asked mid-run (ask_user), or the user's reply to it.
// `key` ("q0", "a0", ...) is the bubble's place in the current run, so the
// stream's replay of a run doesn't add bubbles the chat history already shows.
function addTurnMessage(kind, text, key = null, options = {}) {
  removeEmptyState();
  const wasNearBottom = messagesNearBottom();
  const div = document.createElement("div");
  if (kind === "question") {
    div.className = "msg assistant question";
    const label = document.createElement("div");
    label.className = "question-label";
    label.append(faIcon("circle-question"), document.createTextNode("The agent needs your input"));
    const body = renderMarkdown(text, []);
    if (body) {
      div.append(label, body);
    } else {
      div.append(label, document.createTextNode(text));
    }
  } else {
    div.className = "msg user answer";
    div.textContent = text;
  }
  if (key) div.dataset.runTurn = key;
  const thinkingShown = thinkingEl.parentNode === messagesEl;
  messagesEl.insertBefore(div, thinkingShown ? thinkingEl : null);
  if (!options.noScroll && (kind === "answer" || wasNearBottom)) {
    div.scrollIntoView({ block: "end" });
  }
  return div;
}

// Bubbles from earlier runs keep their text but lose their keys, so a new
// run's questions aren't mistaken for them.
function forgetRunTurnKeys() {
  for (const bubble of messagesEl.querySelectorAll("[data-run-turn]")) {
    delete bubble.dataset.runTurn;
  }
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
    loadFeaturesForChat(chatId);
    syncModelSelects(chatId);
    refreshFiles(); // a file may have gone since the list was last fetched
    let replayedQuestions = 0;
    let replayedAnswers = 0;
    for (const m of chat.messages) {
      if (m.role === "trace") {
        traceLine("agent", m.content, m.created); // replay into the terminal panel
      } else if (m.role === "run_start" || m.role === "run_end") {
        traceLine(m.role, m.content, m.created); // run boundary markers
        if (m.role === "run_start") {
          forgetRunTurnKeys();
          replayedQuestions = 0;
          replayedAnswers = 0;
        }
      } else if (m.role === "question") {
        addTurnMessage("question", m.content, `q${replayedQuestions}`, { noScroll: true });
        replayedQuestions += 1;
      } else if (m.role === "answer") {
        addTurnMessage("answer", m.content, `a${replayedAnswers}`, { noScroll: true });
        replayedAnswers += 1;
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
        if (!awaitingAnswer) showThinking();
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
  loadFeaturesForChat(null);
  syncModelSelects(null);
  refreshFiles(); // a file may have gone since the list was last fetched
  renderEmptyState();
  promptEl.focus();
}

const chatContentEl = document.querySelector(".content");

function playChatEnterAnimation() {
  document.body.classList.remove("chat-entering");
  void document.body.offsetWidth; // restarts it if one is still playing
  document.body.classList.add("chat-entering");
}

chatContentEl.addEventListener("animationend", (event) => {
  if (event.target !== chatContentEl) return; // animations inside the chat bubble up
  document.body.classList.remove("chat-entering");
});

newChatBtn.addEventListener("click", () => {
  startNewChat();
  playChatEnterAnimation();
  closeSidebar();
  document.querySelector('.tabs button[data-tab="task"]').click();
});

// ---- tabs ----
// Shows one tab's panel. "settings" has no button in the bar; the header
// gear opens it, so no button is highlighted then.
const APP_LEVEL_BUTTONS = {
  leads: "leads-btn",
  files: "files-btn",
  spending: "account-spend-btn",
  integrity: "integrity-btn",
  settings: "user-link",
};

const APP_LEVEL_TITLES = {
  leads: "Leads",
  files: "User Files",
  spending: "Account spending",
  integrity: "Integrity",
  settings: "Settings",
};

// While an app-level panel is open, the app bar says what the task is doing.
// "done" sticks until the user returns to the task screen.
let finishedWhileAway = false;

function updateTaskStatus() {
  const pill = document.getElementById("task-status");
  const onAppPanel = !document.getElementById("app-bar").hidden;
  pill.classList.remove("running", "done", "waiting");
  if (!onAppPanel) {
    finishedWhileAway = false;
    pill.hidden = true;
    return;
  }
  if (runInProgress && awaitingAnswer) {
    pill.className = "waiting";
    pill.innerHTML = '<i class="fa-solid fa-comment-dots"></i> Task needs your reply';
  } else if (runInProgress) {
    pill.className = "running";
    pill.innerHTML = '<i class="fa-solid fa-circle"></i> Task running';
  } else if (finishedWhileAway) {
    pill.className = "done";
    pill.innerHTML = '<i class="fa-solid fa-check"></i> Task finished';
  } else {
    pill.hidden = true;
    return;
  }
  pill.hidden = false;
}

document.getElementById("task-status").addEventListener("click", () => activateTab("task"));

// Page scroll belongs to the task; back rooms are short and would reset it.
let taskScrollY = 0;
let currentTabName = "task";

function activateTab(name) {
  if (currentTabName === "task" && name in APP_LEVEL_BUTTONS) taskScrollY = window.scrollY;
  const returningToTask = name === "task" && currentTabName in APP_LEVEL_BUTTONS;
  currentTabName = name;
  document.querySelectorAll(".tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
  document.getElementById(`tab-${name}`).classList.add("active");
  // App-level panels aren't about the current task, so the task tab bar hides.
  const isAppLevel = name in APP_LEVEL_BUTTONS;
  document.querySelector(".tabs").hidden = isAppLevel;
  document.body.classList.toggle("back-room", isAppLevel);
  document.getElementById("app-bar").hidden = !isAppLevel;
  document.getElementById("app-bar-title").textContent = APP_LEVEL_TITLES[name] || "";
  updateTaskStatus();
  for (const [panel, buttonId] of Object.entries(APP_LEVEL_BUTTONS)) {
    document.getElementById(buttonId)?.classList.toggle("active", panel === name);
  }
  if (name === "leads") refreshLeads();
  if (name === "files") refreshFiles();
  if (name === "outputs") renderOutputTray();
  if (name === "citations") refreshCitations();
  if (name === "integrity") startIntegrity();
  else stopIntegrity();
  if (name === "spending") refreshSpending();
  if (name === "taskspend") refreshSpendingStages();
  if (name === "settings") refreshSettings();
  if (returningToTask) window.scrollTo(0, taskScrollY);
  else if (name in APP_LEVEL_BUTTONS) window.scrollTo(0, 0);
}

document.getElementById("back-to-task").addEventListener("click", () => activateTab("task"));
document.addEventListener("keydown", (e) => {
  const appBar = document.getElementById("app-bar");
  if (e.key === "Escape" && !appBar.hidden) activateTab("task");
});

// A header button opens its panel; clicking it again returns to the task.
function toggleAppPanel(name) {
  const isOpen = document.getElementById(`tab-${name}`).classList.contains("active");
  activateTab(isOpen ? "task" : name);
}

// The Trace button sits in this bar too, but opens a drawer instead of a tab.
const tabButtons = document.querySelectorAll(".tabs button[data-tab]");
const tabKeyPrefix = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl+";

tabButtons.forEach((btn, index) => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));

  // Number it by position, the same index the Cmd+number handler uses.
  const keyHint = document.createElement("span");
  keyHint.className = "tab-key";
  keyHint.textContent = String(index + 1);
  keyHint.title = `${tabKeyPrefix}${index + 1}`;
  btn.appendChild(keyHint);
});

// Cmd+1..n (Ctrl on Windows) picks a tab by its position in the bar.
document.addEventListener("keydown", (e) => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
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
let attachmentsLocked = false; // same rule as the chat's stages: fixed once a task is sent
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
  setAttachmentsLocked(chatSettingsAreFixed(chatId));
  if (allFiles.length) renderFileList();
}

function setAttachmentsLocked(locked) {
  attachmentsLocked = locked;
  const composerAttach = document.getElementById("composer-attach");
  composerAttach.disabled = locked;
  composerAttach.title = locked ? "Attachments are locked for this chat" : "Attach files";
  if (locked) document.getElementById("attach-menu").hidden = true;
  attachmentsLockedNote.hidden = !locked;
  attachmentsChooseBtn.hidden = locked;
  uploadHintEl.textContent = locked
    ? "This chat's attachments are locked, so uploads are only saved here"
    : "Uploads are attached to your next task automatically";
  updateAttachHint();
  if (allFiles.length) renderFileList();
}

function saveAttached() {
  localStorage.setItem(attachedKeyFor(currentChatId), JSON.stringify([...attached]));
}

// ---- workflow icons: each chat remembers which stages are on ----
const DEFAULT_FEATURES = { context: true, preprocess: false, assay: false, review: false };
let features = { ...DEFAULT_FEATURES };

function featuresKeyFor(chatId) {
  return `features:${chatId || "new"}`;
}

function renderFeatures() {
  for (const button of document.querySelectorAll(".feature-toggle")) {
    button.setAttribute("aria-pressed", features[button.dataset.feature] ? "true" : "false");
  }
}

function loadFeaturesForChat(chatId) {
  features = { ...DEFAULT_FEATURES };
  try {
    const saved = JSON.parse(localStorage.getItem(featuresKeyFor(chatId)) || "{}");
    for (const name of Object.keys(DEFAULT_FEATURES)) {
      if (typeof saved[name] === "boolean") features[name] = saved[name];
    }
  } catch {
    /* blocked or corrupt storage: use the defaults */
  }
  renderFeatures();
  setFeaturesLocked(chatSettingsAreFixed(chatId));
}

function unstartedForkKey(chatId) {
  return `unstartedFork:${chatId}`;
}

function isUnstartedFork(chatId) {
  try {
    return localStorage.getItem(unstartedForkKey(chatId)) !== null;
  } catch {
    return false; // blocked storage: the fork stays locked like any other chat
  }
}

// A chat only gets an id when its first task starts, so having one means its
// stages are fixed. The exception is a fork, which is open until its own
// first task starts.
function chatSettingsAreFixed(chatId) {
  return Boolean(chatId) && !isUnstartedFork(chatId);
}

function setFeaturesLocked(locked) {
  document.getElementById("composer-features").classList.toggle("locked", locked);
  for (const button of document.querySelectorAll(".feature-toggle")) {
    button.disabled = locked;
  }
}

const chatLockFx = document.getElementById("chat-lock-fx");
const chatLockCard = document.getElementById("chat-lock-card");
const chatLockTitle = document.getElementById("chat-lock-title");
const chatLockHint = document.getElementById("chat-lock-hint");

function playChatLockAnimation() {
  chatLockTitle.textContent = "Settings locked for this chat";
  chatLockHint.textContent = "Fork the chat to change them.";
  chatLockFx.classList.add("play");
}

chatLockCard.addEventListener("animationend", (event) => {
  if (event.target !== chatLockCard) return; // the icon's own animations bubble up
  chatLockFx.classList.remove("play");
  chatLockTitle.textContent = "";
  chatLockHint.textContent = "";
});

const forkChatBtn = document.getElementById("fork-chat");

async function forkCurrentChat() {
  forkChatBtn.disabled = true;
  try {
    const res = await fetch(`/api/chats/${currentChatId}/fork`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    const forkId = body.chat.id;
    // The fork starts from this chat's stages and attachments.
    localStorage.setItem(unstartedForkKey(forkId), "1");
    localStorage.setItem(featuresKeyFor(forkId), JSON.stringify(features));
    localStorage.setItem(attachedKeyFor(forkId), JSON.stringify([...attached]));
    saveChatModel(forkId, shownChatModel());
    await selectChat(forkId);
    playChatEnterAnimation();
  } catch (err) {
    addMessage("assistant", `Fork failed: ${err.message || err}`, true);
  }
  forkChatBtn.disabled = runInProgress;
}
forkChatBtn.addEventListener("click", forkCurrentChat);

function saveFeatures() {
  try {
    localStorage.setItem(featuresKeyFor(currentChatId), JSON.stringify(features));
  } catch {
    /* blocked storage: the choice lasts until the page reloads */
  }
}

for (const button of document.querySelectorAll(".feature-toggle")) {
  button.addEventListener("click", () => {
    const name = button.dataset.feature;
    features[name] = !features[name];
    saveFeatures();
    renderFeatures();
  });
}
loadFeaturesForChat(currentChatId); // the HTML starts every icon off

const labContextModal = document.getElementById("lab-context-modal");
const labContextText = document.getElementById("lab-context-text");
const labContextStatus = document.getElementById("lab-context-status");
const labContextCount = document.getElementById("lab-context-count");
const labContextCondense = document.getElementById("lab-context-condense");
const labContextUndo = document.getElementById("lab-context-undo");
let labContextMaxChars = 20000;
let textBeforeCondense = null;

function setLabContextStatus(message, isNotice = false) {
  labContextStatus.textContent = message;
  labContextStatus.classList.toggle("notice", isNotice);
}

function renderLabContextCount() {
  const length = labContextText.value.length;
  labContextCount.textContent = `${length.toLocaleString()} of ${labContextMaxChars.toLocaleString()} characters`;
  labContextCount.classList.toggle("over", length > labContextMaxChars);
}

function forgetCondense() {
  textBeforeCondense = null;
  labContextUndo.hidden = true;
}

labContextText.addEventListener("input", () => {
  renderLabContextCount();
  forgetCondense(); // Undo would otherwise throw away these edits
});

labContextCondense.addEventListener("click", async () => {
  const source = labContextText.value;
  setLabContextStatus("Condensing…", true);
  labContextCondense.disabled = true;
  labContextText.readOnly = true;
  try {
    const res = await fetch("/api/lab-context/condense", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: source }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    textBeforeCondense = source;
    labContextText.value = body.text;
    labContextUndo.hidden = false;
    renderLabContextCount();
    let message = "Not saved yet. Compare it with your original, because a summary can drop or change details.";
    if (body.new_numbers.length) {
      message = `Check these numbers, which aren't in your original: ${body.new_numbers.join(", ")}. ${message}`;
    }
    setLabContextStatus(message, true);
  } catch (err) {
    setLabContextStatus(`Couldn't condense it: ${err.message}`);
  } finally {
    labContextCondense.disabled = false;
    labContextText.readOnly = false;
  }
});

labContextUndo.addEventListener("click", () => {
  labContextText.value = textBeforeCondense;
  forgetCondense();
  renderLabContextCount();
  setLabContextStatus("");
});

document.getElementById("lab-context-edit").addEventListener("click", async () => {
  setLabContextStatus("");
  forgetCondense();
  labContextText.value = "";
  renderLabContextCount();
  labContextModal.classList.add("show");
  try {
    const res = await fetch("/api/lab-context");
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    labContextText.value = body.text;
    labContextMaxChars = body.max_chars;
    renderLabContextCount();
  } catch (err) {
    setLabContextStatus(`Couldn't load it: ${err.message}`);
  }
});

document.getElementById("lab-context-cancel").addEventListener("click", () => {
  labContextModal.classList.remove("show");
});

document.getElementById("lab-context-save").addEventListener("click", async () => {
  setLabContextStatus("");
  try {
    const res = await fetch("/api/lab-context", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: labContextText.value }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    labContextModal.classList.remove("show");
  } catch (err) {
    setLabContextStatus(`Couldn't save it: ${err.message}`);
  }
});

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

  if (attachmentsLocked) return chip;

  const removeBtn = document.createElement("button");
  removeBtn.className = "attach-chip-remove";
  removeBtn.title = `Detach ${fileName}`;
  removeBtn.replaceChildren(faIcon("xmark"));
  removeBtn.addEventListener("click", () => detachFile(fileName));
  chip.appendChild(removeBtn);
  return chip;
}

function renderAttachmentsTab() {
  attachmentBadgeEl.textContent = attached.size || "";
  attachmentChipsEl.replaceChildren();
  attachmentChipsEl.hidden = attached.size === 0;
  for (const fileName of attached) {
    attachmentChipsEl.appendChild(createAttachChip(fileName));
  }
  attachmentsEmptyEl.hidden = attached.size > 0;
  attachmentsEmptyEl.textContent = attachmentsLocked
    ? "No files were attached to this chat."
    : "No files attached yet.";
}

function updateAttachHint() {
  renderAttachmentsTab();
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
  if (attachmentsLocked) return;
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
  attachAllBtn.disabled = visible.length === 0 || attachmentsLocked;

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
    toggle.disabled = attachmentsLocked;

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
    if (attachmentsLocked) {
      setTaskUploadStatus("Saved to User Files, but not attached: this chat's attachments are locked.");
    } else {
      for (const name of data.saved) attached.add(name);
      updateAttachHint();
      saveAttached();
    }
    await refreshFiles();
  } catch (err) {
    uploadStatus.textContent = `Upload failed: ${err.message || err}`;
    setTaskUploadStatus(`Upload failed: ${err.message || err}`);
  }
}

dropzone.addEventListener("click", () => fileInput.click());
const attachButton = document.getElementById("composer-attach");
const attachMenu = document.getElementById("attach-menu");

function setAttachMenuOpen(open) {
  attachMenu.hidden = !open;
  attachButton.setAttribute("aria-expanded", String(open));
}

attachButton.addEventListener("click", (e) => {
  e.stopPropagation();
  setAttachMenuOpen(attachMenu.hidden);
});
document.addEventListener("click", () => setAttachMenuOpen(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setAttachMenuOpen(false);
});
document.getElementById("attach-upload").addEventListener("click", () => fileInput.click());
document.getElementById("attach-library").addEventListener("click", () => activateTab("files"));
attachmentsChooseBtn.addEventListener("click", () => activateTab("files"));
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
// The current run asked the user something and is waiting: the composer
// sends its text there as the reply instead of starting a new run.
let awaitingAnswer = false;
const DEFAULT_PLACEHOLDER = promptEl.placeholder;

function setAwaitingAnswer(waiting) {
  awaitingAnswer = waiting;
  updateTaskStatus();
  promptEl.placeholder = waiting ? "Reply to the agent…" : DEFAULT_PLACEHOLDER;
  runBtn.title = waiting ? "Send reply" : "Run task";
  runBtn.setAttribute("aria-label", runBtn.title);
  updateRunEnabled();
}

function updateRunEnabled() {
  const hasPrompt = promptEl.value.trim() !== "";
  const busy = runInProgress && !awaitingAnswer;
  runBtn.disabled = busy || !hasPrompt;
  runBtn.classList.toggle("nothing-to-send", !busy && !hasPrompt);
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
  if (awaitingAnswer && currentTaskId) {
    sendAnswer(currentTaskId, prompt);
    return;
  }
  runTask(prompt, { fromComposer: true });
}

async function sendAnswer(taskId, text) {
  setAwaitingAnswer(false); // no second send while this one is in flight
  clearPrompt();
  const bubble = addTurnMessage("answer", text, `a${runAnswerCount}`);
  showThinking();
  setTraceStatus("reading your reply…");
  try {
    const res = await fetch(`/api/tasks/${taskId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  } catch (err) {
    bubble.remove();
    if (!promptEl.value.trim()) fillPrompt(text);
    let errorText = err.message || String(err);
    if (err instanceof TypeError) {
      errorText = "Can't reach the server. Is it still running?";
    }
    addMessage("assistant", `Your reply wasn't sent: ${errorText}`, true);
    // Still waiting unless the run moved on (answered elsewhere, or ended).
    if (currentTaskId === taskId && !(err.message || "").includes("isn't waiting")) {
      hideThinking();
      setTraceStatus("waiting for your reply");
      setAwaitingAnswer(true);
    }
  }
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
// Questions and replies the current stream has seen; the stream replays a
// run from its start, so these restart with each attachStream().
let runQuestionCount = 0;
let runAnswerCount = 0;

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
  if (runInProgress && !running && !document.getElementById("app-bar").hidden) {
    finishedWhileAway = true;
  }
  runInProgress = running;
  updateTaskStatus();
  updateRunEnabled();
  forkChatBtn.disabled = running; // the server refuses to copy a live run
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
    setAwaitingAnswer(false);
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
  setFeaturesLocked(true); // what's sent below is what the chat keeps
  setAttachmentsLocked(true);
  const chatModel = shownChatModel();
  lockedChatModel = chatModel;
  fillEfforts(); // disables the selects
  forgetRunTurnKeys();
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
      body: JSON.stringify({
        prompt,
        files: [...attached],
        chat_id: currentChatId,
        features,
        model: chatModel.model,
        effort: chatModel.effort,
      }),
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
    const isFirstTask = !currentChatId || isUnstartedFork(currentChatId);
    if (!currentChatId) localStorage.removeItem(attachedKeyFor(null));
    currentChatId = chat_id;
    localStorage.removeItem(unstartedForkKey(chat_id));
    saveChatModel(chat_id, chatModel);
    localStorage.setItem("lastChatId", chat_id);
    saveAttached(); // persist under the now-real chat id, not the "new" bucket
    saveFeatures();
    refreshChats();
    attachStream(task_id);
    if (isFirstTask) playChatLockAnimation();
  } catch (err) {
    setRunning(false);
    setFeaturesLocked(chatSettingsAreFixed(currentChatId));
    setAttachmentsLocked(chatSettingsAreFixed(currentChatId));
    syncModelSelects(currentChatId);
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
  runQuestionCount = 0;
  runAnswerCount = 0;
  setAwaitingAnswer(false);
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
      if (msg.step.kind === "question" || msg.step.kind === "answer") {
        showTurnStep(msg.step);
      } else {
        traceLine(msg.step.kind, msg.step.text, msg.step.t);
      }
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

// A question or reply from the run's stream. Either may already be on screen:
// from the chat history on a re-attach, or a reply this tab just sent.
function showTurnStep(step) {
  let key;
  if (step.kind === "question") {
    key = `q${runQuestionCount}`;
    runQuestionCount += 1;
  } else {
    key = `a${runAnswerCount}`;
    runAnswerCount += 1;
  }
  if (!messagesEl.querySelector(`[data-run-turn="${key}"]`)) {
    addTurnMessage(step.kind, step.text, key);
  }
  if (step.kind === "question") {
    hideThinking();
    setTraceStatus("waiting for your reply");
    setAwaitingAnswer(true);
    promptEl.focus();
  } else {
    showThinking();
    setTraceStatus("reading your reply…");
    setAwaitingAnswer(false);
  }
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
          `(${status.validation_error || "unknown error"}). Click "Download now" to retry.`;
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
          `(${status.validation_error || "unknown error"}). Click "Download now" to re-fetch it.`;
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
        `(${status.validation_error || "unknown error"}). Click "Download now" to re-fetch it.`;
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
// The {model, effort} the selects are held on while this chat is locked.
let lockedChatModel = null;
const FAMILY_TITLE = "Model for new runs, on Bedrock. Switching applies to the next task.";
const LOCKED_MODEL_TITLE = "Locked for this chat. Fork the chat to change it.";

function chatModelKey(chatId) {
  return `chatModel:${chatId}`;
}

function readChatModel(chatId) {
  try {
    return JSON.parse(localStorage.getItem(chatModelKey(chatId)));
  } catch {
    return null; // blocked or corrupt storage: use what the selects show
  }
}

function saveChatModel(chatId, chatModel) {
  try {
    localStorage.setItem(chatModelKey(chatId), JSON.stringify(chatModel));
  } catch {
    /* blocked storage: the chat keeps whatever the selects show next time */
  }
}

function shownChatModel() {
  return { model: versionSelect.value, effort: effortSelect.value };
}

// Puts the selects on this chat's model: held there once its settings are
// fixed, or just a starting point for a fork that hasn't run yet.
function syncModelSelects(chatId) {
  const savedChatModel = readChatModel(chatId);
  if (chatSettingsAreFixed(chatId)) {
    lockedChatModel = savedChatModel || shownChatModel();
  } else {
    lockedChatModel = null;
    if (savedChatModel) {
      currentModel = savedChatModel.model;
      currentEffort = savedChatModel.effort;
    }
  }
  if (!modelFamilies.length) return;
  familySelect.disabled = false;
  familySelect.title = FAMILY_TITLE;
  showModel(lockedChatModel ? lockedChatModel.model : currentModel);
}

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
  refreshModelPicker();
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
  const shownEffort = lockedChatModel ? lockedChatModel.effort : currentEffort;
  effortSelect.value = levels.includes(shownEffort) ? shownEffort : "";
  effortSelect.disabled = !levels.length;
  effortSelect.title = levels.length
    ? "How hard the model works on each step; higher costs more and takes longer. Applies to the next task."
    : "This model doesn't support effort levels.";
  if (lockedChatModel) {
    for (const select of [familySelect, versionSelect, effortSelect]) {
      select.disabled = true;
      select.title = LOCKED_MODEL_TITLE;
    }
  }
  refreshModelPicker();
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
  familySelect.title = FAMILY_TITLE;
  if (lockedChatModel) showModel(lockedChatModel.model);
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

// ---- composer model picker: a quiet trigger plus popover that drives the
// (hidden) selects above, so all the save/lock logic stays in one place. ----
const pickerButton = document.getElementById("model-picker");
const pickerLabel = document.getElementById("model-picker-label");
const pickerMenu = document.getElementById("model-menu");

// "Opus 5" under family "opus" -> "5"; unavailable markers dropped.
function shortVersion(text, family) {
  const plain = text.replace(/ \(unavailable\)$/, "");
  return plain.replace(new RegExp(`^${family}\\s*`, "i"), "");
}

const EFFORT_SHORT = { "": "Auto", Default: "Auto", Medium: "Med", Xhigh: "XHigh" };

function chosenOption(select) {
  return select.options[select.selectedIndex];
}

function refreshModelPicker() {
  const family = chosenOption(familySelect);
  const version = chosenOption(versionSelect);
  const picked = Boolean(familySelect.value);
  pickerLabel.replaceChildren();
  if (!picked) {
    pickerLabel.textContent = family ? family.textContent : "Models";
    pickerButton.disabled = true;
    closeModelMenu();
    return;
  }
  const name = el("b", null, familySelect.value);
  name.style.textTransform = "capitalize";
  pickerLabel.append(name);
  const versionText = version ? shortVersion(version.textContent, familySelect.value) : "";
  if (versionText) pickerLabel.append(` ${versionText}`);
  if (effortSelect.value) pickerLabel.append(` · ${effortSelect.value}`);
  const single = modelFamilies.length < 2 && versionSelect.disabled && effortSelect.disabled;
  pickerButton.disabled = Boolean(lockedChatModel) || single;
  pickerButton.title = lockedChatModel ? LOCKED_MODEL_TITLE : FAMILY_TITLE;
  if (pickerButton.disabled) closeModelMenu();
  else syncModelMenu();
}

// The menu is built once and then only updated in place (check marks, the
// flyout's rows, the effort buttons), so a click never redraws what's under
// the cursor and nothing jumps. Every click does the same thing: select it,
// keep the menu open, move the check.
const menuModels = el("div", "mm-section");
const menuEffort = el("div", "mm-seg");
const menuFlyout = el("div", "mm-flyout");
pickerMenu.append(
  el("div", "mm-head", "Model"),
  menuModels,
  el("div", "mm-head", "Effort"),
  menuEffort,
  menuFlyout,
);
let flyoutOpen = false;
let flyoutFamily = null;

function isModelMenuOpen() {
  return pickerMenu.classList.contains("open");
}

function closeModelMenu() {
  flyoutOpen = false;
  pickerMenu.classList.remove("open");
  menuFlyout.classList.remove("show");
  pickerButton.setAttribute("aria-expanded", "false");
}

function choose(select, value) {
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

function menuRow(text, onClick) {
  const row = el("button", "mm-row");
  row.type = "button";
  row.append(el("span", "mm-name", text), el("span", "mm-sub"), el("i", "fa-solid fa-check"));
  row.addEventListener("click", onClick);
  return row;
}

// Keeps a list of buttons matching the options; rebuilds only when the
// options themselves changed, otherwise just moves the active mark.
function syncRows(container, select, makeRow) {
  const signature = Array.from(select.options, (o) => `${o.value}|${o.textContent}|${o.disabled}`).join("\n");
  if (container.dataset.signature !== signature) {
    container.dataset.signature = signature;
    container.replaceChildren(...Array.from(select.options, makeRow));
  }
  Array.from(select.options).forEach((opt, index) => {
    const row = container.children[index];
    row.classList.toggle("on", opt.value === select.value);
    row.disabled = opt.disabled;
  });
}

function syncModelMenu() {
  syncRows(menuModels, familySelect, (opt) =>
    menuRow(opt.textContent, () => {
      flyoutOpen = true;
      choose(familySelect, opt.value);
    }),
  );
  // Each model row shows the version it would run: the chosen one for the
  // active model, otherwise its newest usable one.
  modelFamilies.forEach((fam, index) => {
    const row = menuModels.children[index];
    if (!row) return;
    const active = fam.name === familySelect.value;
    const shown = active
      ? chosenOption(versionSelect)?.textContent
      : (fam.versions.find((v) => v.available) || fam.versions[0])?.label;
    row.querySelector(".mm-sub").textContent = shown ? shortVersion(shown, fam.name) : "";
  });
  syncRows(menuEffort, effortSelect, (opt) => {
    const label = opt.textContent.replace(/ effort$/, "");
    const btn = el("button", null, EFFORT_SHORT[label] || EFFORT_SHORT[opt.textContent] || label);
    btn.type = "button";
    btn.addEventListener("click", () => choose(effortSelect, opt.value));
    return btn;
  });
  // No effort levels for this model: keep the row (so the menu doesn't change
  // height) but dim it.
  menuEffort.classList.toggle("off", effortSelect.disabled);
  if (flyoutFamily !== familySelect.value) {
    flyoutFamily = familySelect.value;
    menuFlyout.replaceChildren(el("div", "mm-head", `${familySelect.value} versions`));
  }
  const flyoutRows = menuFlyout.querySelector(".mm-rows") || menuFlyout.appendChild(el("div", "mm-rows"));
  syncRows(flyoutRows, versionSelect, (opt) =>
    menuRow(opt.textContent.replace(new RegExp(`^${familySelect.value}\\s*`, "i"), ""), () => choose(versionSelect, opt.value)),
  );
  menuFlyout.classList.toggle("show", flyoutOpen && versionSelect.options.length > 1);
}

pickerButton.addEventListener("click", () => {
  if (isModelMenuOpen()) {
    closeModelMenu();
    return;
  }
  syncModelMenu();
  pickerMenu.classList.add("open");
  pickerButton.setAttribute("aria-expanded", "true");
});
document.addEventListener("click", (event) => {
  if (isModelMenuOpen() && !event.target.closest("#composer-models")) closeModelMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModelMenu();
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

// Sections on this tab can be dragged into a new order by their handle on the
// left (or moved with the arrow keys). The order is remembered per browser.
const SPENDING_SECTION_IDS = ["tiles", "daily", "services"];
const SPENDING_ORDER_KEY = "spending-section-order";
const spendingTabEl = document.getElementById("spending-sections");

function loadSpendingOrder() {
  let saved = [];
  try {
    saved = JSON.parse(localStorage.getItem(SPENDING_ORDER_KEY)) || [];
  } catch (err) {
    saved = [];
  }
  const order = [];
  for (const id of saved) {
    if (SPENDING_SECTION_IDS.includes(id) && !order.includes(id)) order.push(id);
  }
  for (const id of SPENDING_SECTION_IDS) {
    if (!order.includes(id)) order.push(id);
  }
  return order;
}

let spendingOrder = loadSpendingOrder();

function applySpendingOrder() {
  for (const section of spendingTabEl.querySelectorAll("[data-section]")) {
    section.style.order = spendingOrder.indexOf(section.dataset.section);
  }
}

function saveSpendingOrder() {
  try {
    localStorage.setItem(SPENDING_ORDER_KEY, JSON.stringify(spendingOrder));
  } catch (err) {
    // Private mode or blocked storage: the order just won't be remembered.
  }
}

function moveSpendingSection(id, newIndex) {
  const clamped = Math.max(0, Math.min(spendingOrder.length - 1, newIndex));
  spendingOrder = spendingOrder.filter((other) => other !== id);
  spendingOrder.splice(clamped, 0, id);
  applySpendingOrder();
}

function makeSpendingSectionDraggable(section, id) {
  section.dataset.section = id;
  section.classList.add("spend-section");
  const handle = el("button", "spend-handle", "⋮⋮");
  handle.type = "button";
  handle.title = "Drag to reorder";
  handle.setAttribute("aria-label", `Reorder ${id}: drag, or use the up and down arrow keys`);

  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    section.classList.add("dragging");
  });
  handle.addEventListener("pointermove", (event) => {
    if (!section.classList.contains("dragging")) return;
    for (const other of spendingTabEl.querySelectorAll("[data-section]")) {
      if (other === section) continue;
      const box = other.getBoundingClientRect();
      if (event.clientY >= box.top && event.clientY <= box.bottom) {
        moveSpendingSection(id, spendingOrder.indexOf(other.dataset.section));
        break;
      }
    }
  });
  const stopDragging = () => {
    if (!section.classList.contains("dragging")) return;
    section.classList.remove("dragging");
    saveSpendingOrder();
  };
  handle.addEventListener("pointerup", stopDragging);
  handle.addEventListener("pointercancel", stopDragging);
  handle.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const step = event.key === "ArrowUp" ? -1 : 1;
    moveSpendingSection(id, spendingOrder.indexOf(id) + step);
    saveSpendingOrder();
    handle.focus();
  });

  section.prepend(handle);
  return section;
}

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

// What each stage of the latest run used. Token counts, not dollars: the
// share weights output and cached tokens by their usual price ratios.
const spendingStagesEl = document.getElementById("spending-stages");

function renderSpendingStages(stages) {
  const card = el("section", "spend-card");
  card.append(
    el("h2", null, "Latest run by stage"),
    el("p", "note", "Share of the run's cost, from token counts. The optional stages (preprocess, assay, review) are the extra cost over the main analysis."),
  );
  if (!stages.length) {
    card.append(el("p", "note", "No run since the app started."));
    spendingStagesEl.replaceChildren(card);
    return;
  }
  for (const stage of stages) {
    const row = el("div", "spend-service");
    const track = el("div", "track");
    const fill = el("div", "fill");
    fill.style.width = `${stage.share * 100}%`;
    track.append(fill);
    const name = el("div", "name", stage.name);
    const cached = stage.cached_tokens ? ` (${stage.cached_tokens.toLocaleString()} cached)` : "";
    name.append(el("span", "detail",
      `${stage.calls} calls · ${stage.input_tokens.toLocaleString()} in${cached} · ${stage.output_tokens.toLocaleString()} out`));
    row.append(name, track, el("div", "amount", `${Math.round(stage.share * 100)}%`));
    card.append(row);
  }
  spendingStagesEl.replaceChildren(card);
}

async function refreshSpendingStages() {
  try {
    const res = await fetch("/api/spending/stages");
    if (!res.ok) return;
    const data = await res.json();
    renderSpendingStages(data.stages);
  } catch (err) {
    spendingStagesEl.replaceChildren(); // Cost Explorer's figures above don't depend on this
  }
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
    makeSpendingSectionDraggable(tiles, "tiles"),
    makeSpendingSectionDraggable(spendingDailyCard(data.daily, data.currency), "daily"),
    makeSpendingSectionDraggable(spendingServicesCard(data.services, data.currency), "services"),
  );
  applySpendingOrder();
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
const settingsUpdateStatusEl = document.getElementById("settings-update-status");
const settingsBetaEl = document.getElementById("settings-beta");
const settingsBetaHintEl = document.getElementById("settings-beta-hint");
const settingsMessageEl = document.getElementById("settings-message");
const settingsSetupBtn = document.getElementById("settings-setup");
const settingsCleanupBtn = document.getElementById("settings-cleanup");

const BETA_HINT = "Each start updates to the newest release, test builds included. They can be less stable. Turning this off keeps your version until a newer full release comes out.";
const BETA_HINT_CHECKOUT = "This copy runs from a git checkout, which updates with git, so this has no effect here.";

function formatWhen(seconds) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function updateStatusText(gitCheckout, status) {
  if (gitCheckout) return "This copy updates with git.";
  if (!status || !status.checked_at) return "biomnikhil checks each time it starts.";
  const lines = [];
  if (status.outcome === "failed") {
    lines.push(`${status.message} (${formatWhen(status.checked_at)})`);
  } else {
    lines.push(`Up to date (${formatWhen(status.checked_at)})`);
  }
  if (status.updated_to && status.updated_at) {
    lines.push(`Updated to ${status.updated_to} on ${formatWhen(status.updated_at)}`);
  }
  return lines.join(". ");
}

async function refreshSettings() {
  try {
    const res = await fetch("/api/settings");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    settingsVersionEl.textContent = data.version;
    settingsUpdateStatusEl.textContent = updateStatusText(data.git_checkout, data.update_status);
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
    message: "biomnikhil will close and the setup window will rebuild its Python environment. This needs an internet connection and can take a few minutes. Your chats, settings and data lake stay.",
    label: "Run setup",
  }, "Closing biomnikhil to run setup. Click Open biomnikhil in the setup window when it finishes.");
});

settingsCleanupBtn.addEventListener("click", () => {
  runMaintenance("cleanup", {
    title: "Clean up?",
    message: "biomnikhil will close and a separate window will open. It lists files found outside this install and asks about each one, and nothing is deleted unless you say yes there. biomnikhil reopens when you're done.",
    label: "Continue",
  }, "Closing biomnikhil to clean up. Answer the questions in the window that opens.");
});

// ---- leads ----
// Candidate findings the lab may want to follow up. An answer's "Save as lead"
// button or the New lead form adds one; each shows how far its answer was
// checked (the review and the planted-truth assay), and the lab moves it
// through new, testing, validated or failed. Kept on the server, across chats.
const LEAD_STATUSES = [
  { value: "new", label: "New" },
  { value: "testing", label: "Testing" },
  { value: "validated", label: "Validated" },
  { value: "failed", label: "Failed" },
];

const LEAD_REVIEW_BADGES = {
  holds: { text: "Review: holds", tone: "ok" },
  caveats: { text: "Review: holds with caveats", tone: "warn" },
  does_not_hold: { text: "Review: does not hold", tone: "bad" },
  revised: { text: "Redone after review, not re-reviewed", tone: "warn" },
  unclear: { text: "Review: no clear verdict", tone: "warn" },
  unreviewed: { text: "Review didn't finish", tone: "warn" },
  none: { text: "Not reviewed", tone: "muted" },
};

const LEAD_ASSAY_BADGES = {
  ran: { text: "Assay ran", tone: "ok" },
  not_applicable: { text: "Assay: not applicable", tone: "muted" },
  couldnt_reproduce: { text: "Assay couldn't reproduce it", tone: "bad" },
  unclear: { text: "Assay: no clear status", tone: "warn" },
  untested: { text: "Assay didn't finish", tone: "warn" },
  none: { text: "No assay", tone: "muted" },
};

let leads = [];
let leadsFilter = "all";
const leadsFiltersEl = document.getElementById("leads-filters");
const leadsListEl = document.getElementById("leads-list");
const leadsEmptyEl = document.getElementById("leads-empty");
const leadsBadgeEl = document.getElementById("leads-badge");
const leadsNewForm = document.getElementById("leads-new");
const leadsNewTitle = document.getElementById("leads-new-title");
const leadsNewError = document.getElementById("leads-new-error");

function countLeads(status) {
  let count = 0;
  for (const lead of leads) {
    if (status === "all" || lead.status === status) count += 1;
  }
  return count;
}

// The header badge counts the leads still being worked: new and testing.
function updateLeadsBadge() {
  const open = countLeads("new") + countLeads("testing");
  if (open === 0) {
    leadsBadgeEl.textContent = "";
  } else if (open > 99) {
    leadsBadgeEl.textContent = "99+";
  } else {
    leadsBadgeEl.textContent = String(open);
  }
}

async function refreshLeads() {
  try {
    const res = await fetch("/api/leads");
    if (!res.ok) throw new Error("couldn't load leads");
    leads = (await res.json()).leads;
  } catch {
    return; // keep what's shown; opening the board tries again
  }
  renderLeads();
}

function renderLeadFilters() {
  leadsFiltersEl.replaceChildren();
  const options = [{ value: "all", label: "All" }, ...LEAD_STATUSES];
  for (const option of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "leads-filter";
    const isCurrent = leadsFilter === option.value;
    button.classList.toggle("active", isCurrent);
    button.setAttribute("aria-pressed", String(isCurrent));
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = String(countLeads(option.value));
    button.append(option.label, count);
    button.addEventListener("click", () => {
      leadsFilter = option.value;
      renderLeads();
    });
    leadsFiltersEl.append(button);
  }
}

function renderLeads() {
  updateLeadsBadge();
  renderLeadFilters();
  const shown = [];
  for (const lead of leads) {
    if (leadsFilter === "all" || lead.status === leadsFilter) shown.push(lead);
  }
  leadsListEl.replaceChildren();
  for (const lead of shown) {
    leadsListEl.append(buildLeadCard(lead));
  }
  leadsEmptyEl.hidden = shown.length > 0;
  if (leads.length === 0) {
    leadsEmptyEl.textContent =
      "No leads yet. When an answer looks worth following up, click Save as lead under it, " +
      "or add one yourself with New lead.";
  } else if (shown.length === 0) {
    leadsEmptyEl.textContent = "No leads with this status.";
  }
}

function leadBadge(badges, state) {
  const badge = badges[state] || badges.none;
  const element = document.createElement("span");
  element.className = `lead-badge tone-${badge.tone}`;
  element.textContent = badge.text;
  return element;
}

let leadNoteTimer = null;

function showLeadNote(card, text, isError) {
  const note = card.querySelector(".lead-note");
  note.textContent = text;
  note.classList.toggle("error", isError);
  clearTimeout(leadNoteTimer);
  if (!isError) {
    leadNoteTimer = setTimeout(() => {
      note.textContent = "";
    }, 2500);
  }
}

// Saves changes to one lead. Returns whether the server took them.
async function saveLead(lead, card, changes) {
  try {
    const res = await fetch(`/api/leads/${lead.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || "Couldn't save");
    Object.assign(lead, body.lead);
    showLeadNote(card, "Saved", false);
    return true;
  } catch (err) {
    showLeadNote(card, err.message || "Couldn't save", true);
    return false;
  }
}

async function openLeadChat(lead, card) {
  const res = await fetch(`/api/chats/${lead.chat_id}`);
  if (!res.ok) {
    showLeadNote(card, "The chat this came from was deleted.", true);
    return;
  }
  await selectChat(lead.chat_id);
}

function buildLeadField(lead, card, field, label) {
  const wrapper = document.createElement("div");
  wrapper.className = "lead-field";
  const id = `lead-${lead.id}-${field}`;
  const labelEl = document.createElement("label");
  labelEl.htmlFor = id;
  labelEl.textContent = label;
  const textarea = document.createElement("textarea");
  textarea.id = id;
  textarea.value = lead[field];
  textarea.addEventListener("change", () => saveLead(lead, card, { [field]: textarea.value }));
  wrapper.append(labelEl, textarea);
  return wrapper;
}

function buildLeadEvidence(lead) {
  const details = document.createElement("details");
  details.className = "lead-evidence";
  const summary = document.createElement("summary");
  summary.textContent = "Evidence from the answer";
  const body = document.createElement("div");
  body.className = "lead-evidence-body";
  details.append(summary, body);
  // Rendered when first opened, so a long board doesn't render every answer up front.
  details.addEventListener("toggle", () => {
    if (!details.open || body.childElementCount > 0 || body.textContent) return;
    const rendered = renderMarkdown(lead.evidence, []);
    if (rendered) {
      body.append(rendered);
    } else {
      body.textContent = lead.evidence;
    }
  });
  return details;
}

function buildLeadCard(lead) {
  const card = document.createElement("li");
  card.className = "lead";

  const head = document.createElement("div");
  head.className = "lead-head";
  const title = document.createElement("input");
  title.type = "text";
  title.className = "lead-title";
  title.maxLength = 200;
  title.value = lead.title;
  title.title = lead.title; // the full text, when the box cuts it off
  title.setAttribute("aria-label", "Lead title");
  title.addEventListener("change", async () => {
    const newTitle = title.value.trim();
    if (!newTitle) {
      title.value = lead.title; // a lead always has a title
      return;
    }
    await saveLead(lead, card, { title: newTitle });
    title.value = lead.title;
    title.title = lead.title;
  });
  const status = document.createElement("select");
  status.className = `lead-status status-${lead.status}`;
  status.setAttribute("aria-label", "Status");
  for (const option of LEAD_STATUSES) {
    const optionEl = document.createElement("option");
    optionEl.value = option.value;
    optionEl.textContent = option.label;
    status.append(optionEl);
  }
  status.value = lead.status;
  status.addEventListener("change", async () => {
    const saved = await saveLead(lead, card, { status: status.value });
    if (saved) {
      renderLeads(); // the counts, the badge and the filter all depend on it
    } else {
      status.value = lead.status;
    }
  });
  head.append(title, status);

  const meta = document.createElement("div");
  meta.className = "lead-meta";
  meta.append(leadBadge(LEAD_REVIEW_BADGES, lead.review), leadBadge(LEAD_ASSAY_BADGES, lead.assay));
  const saved = document.createElement("span");
  saved.textContent = `Saved ${new Date(lead.created * 1000).toLocaleDateString()}`;
  meta.append(saved);
  if (lead.chat_id) {
    const openChat = document.createElement("button");
    openChat.type = "button";
    openChat.className = "lead-link";
    openChat.textContent = "Open the chat";
    openChat.addEventListener("click", () => openLeadChat(lead, card));
    meta.append(openChat);
  }
  const note = document.createElement("span");
  note.className = "lead-note";
  note.setAttribute("role", "status");
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "lead-delete";
  remove.textContent = "Delete lead";
  remove.addEventListener("click", async () => {
    const confirmed = await confirmModal(`Delete "${lead.title}"?`, { title: "Delete lead?" });
    if (!confirmed) return;
    const res = await fetch(`/api/leads/${lead.id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      showLeadNote(card, "Couldn't delete this lead.", true);
      return;
    }
    const remaining = [];
    for (const other of leads) {
      if (other.id !== lead.id) remaining.push(other);
    }
    leads = remaining;
    renderLeads();
  });
  meta.append(note, remove);
  card.append(head, meta);

  if (lead.evidence) card.append(buildLeadEvidence(lead));

  const fields = document.createElement("div");
  fields.className = "lead-fields";
  fields.append(
    buildLeadField(lead, card, "next_experiment", "Next experiment"),
    buildLeadField(lead, card, "notes", "Notes"),
  );
  card.append(fields);
  return card;
}

// The button under an answer that saves it to the board. Saving the same
// answer again changes nothing (the server returns the lead it already has).
function buildSaveLeadButton(answerText) {
  const button = document.createElement("button");
  button.className = "copy-btn save-lead-btn";
  button.title = "Save this answer to the Leads board";
  button.replaceChildren(faIcon("flask"));
  button.addEventListener("click", async () => {
    if (button.classList.contains("saved")) return;
    button.classList.remove("failed");
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ evidence: answerText, chat_id: currentChatId }),
      });
      if (!res.ok) throw new Error("couldn't save the lead");
      button.classList.add("saved");
      refreshLeads();
    } catch {
      button.classList.add("failed");
    }
  });
  return button;
}

document.getElementById("leads-add").addEventListener("click", () => {
  leadsNewForm.hidden = false;
  leadsNewTitle.focus();
});

function closeNewLeadForm() {
  leadsNewForm.hidden = true;
  leadsNewTitle.value = "";
  leadsNewError.textContent = "";
}

document.getElementById("leads-new-cancel").addEventListener("click", closeNewLeadForm);

leadsNewForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const title = leadsNewTitle.value.trim();
  if (!title) return;
  try {
    const res = await fetch("/api/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || "Couldn't add the lead");
    closeNewLeadForm();
    leadsFilter = "all";
    await refreshLeads();
  } catch (err) {
    leadsNewError.textContent = err.message || "Couldn't add the lead";
  }
});

refreshLeads(); // for the header badge

document.getElementById("leads-btn").addEventListener("click", () => toggleAppPanel("leads"));
document.getElementById("files-btn").addEventListener("click", () => toggleAppPanel("files"));
document.getElementById("integrity-btn").addEventListener("click", () => toggleAppPanel("integrity"));
document.getElementById("account-spend-btn").addEventListener("click", () => toggleAppPanel("spending"));

document.getElementById("user-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  toggleAppPanel("settings");
});
