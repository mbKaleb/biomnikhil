const promptEl = document.getElementById("prompt");
const runBtn = document.getElementById("run");
const stepsEl = document.getElementById("steps");
const answerEl = document.getElementById("answer");

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
      body: JSON.stringify({ prompt }),
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
