const promptEl = document.getElementById("prompt");
const runBtn = document.getElementById("run");
const stepsEl = document.getElementById("steps");
const answerEl = document.getElementById("answer");
const attachHint = document.getElementById("attach-hint");
const fileInput = document.getElementById("file-input");
const dropzone = document.getElementById("dropzone");
const uploadStatus = document.getElementById("upload-status");
const fileList = document.getElementById("file-list");

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

refreshFiles();

// ---- task run ----
runBtn.addEventListener("click", async () => {
  const prompt = promptEl.value.trim();
  if (!prompt) return;

  runBtn.disabled = true;
  stepsEl.style.display = "block";
  stepsEl.textContent = "";
  answerEl.style.display = "none";
  answerEl.classList.remove("error");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, files: [...attached] }),
    });
    const { task_id, error } = await res.json();
    if (error) throw new Error(error);

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
        answerEl.style.display = "block";
        if (msg.status === "done") {
          answerEl.textContent = msg.result;
        } else {
          answerEl.classList.add("error");
          answerEl.textContent = msg.error || "task failed";
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
    answerEl.style.display = "block";
    answerEl.classList.add("error");
    answerEl.textContent = String(err);
  }
});
