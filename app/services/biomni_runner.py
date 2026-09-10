"""Runs Biomni A1 tasks on background threads.

Windows-native by design: threads, no fork, no signals. The A1 import and
first construction are lazy so the Flask app boots instantly even before
the ~11 GB data lake exists — the download kicks off on the first task.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

from ..config import Config
from ..providers import resolve
from .tasks import registry

_agent = None
_agent_lock = threading.Lock()

# Hard cap on agent messages per run; a legitimate deep analysis stays well
# under this, while a stuck model would otherwise loop forever.
MAX_STEPS = 150


def _get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            kwargs = resolve(Config.PROVIDER, Config.MODEL)

            # The A1 constructor kwargs only override the *agent* LLM.
            # Biomni's database/query tools use its default BiomniConfig,
            # which defaults to claude-sonnet-4-5 and reads BIOMNI_LLM from
            # the env — point it at the same model so those tools don't
            # demand an Anthropic key we may not have. Must happen before
            # the biomni import below, which is when that config loads.
            import os

            os.environ.setdefault("BIOMNI_LLM", kwargs["llm"])

            from biomni.agent import A1  # heavy import — keep it lazy

            _agent = A1(path=str(Config.DATA_PATH), **kwargs)
        return _agent


# Biomni's executor only captures stdout; models (gpt-4o especially) keep
# ending code cells with bare expressions, get an empty observation, and
# loop retrying. Nudge the model up front.
_EXECUTION_HINT = (
    "\n\n(Execution note: code output is captured from stdout only — always "
    "wrap values you want to see in print(); a bare expression on the last "
    "line produces an empty observation. When reading spreadsheets, first "
    "inspect with header=None, then drop all-empty rows/columns and set the "
    "real header/index before analyzing — raw sheets are often plate-style "
    "grids with title rows, which otherwise become Unnamed columns and NaN "
    "values that poison downstream results.)"
)


def submit(prompt: str, chat_id: str | None = None) -> str:
    """Queue a prompt for the agent; returns a task id immediately. When a
    chat_id is given, the final answer (or error) is appended to that chat."""
    user_prompt = prompt
    prompt = prompt + _EXECUTION_HINT
    task = registry.create(prompt, chat_id=chat_id)
    thread = threading.Thread(
        target=_run, args=(task.id, prompt, chat_id, user_prompt), daemon=True
    )
    thread.start()
    return task.id


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def _run(
    task_id: str, prompt: str, chat_id: str | None = None, user_prompt: str = ""
) -> None:
    from . import chat_store

    # Run boundary markers: the trace panel renders these as ▶/■ divider
    # lines, both live (registry steps) and on replay (chat messages).
    t0 = time.time()

    def mark(kind: str, text: str) -> None:
        registry.append_step(task_id, kind, text)
        if chat_id:
            chat_store.add_message(chat_id, kind, text)

    mark("run_start", user_prompt or prompt)
    try:
        registry.append_step(
            task_id, "system",
            "initializing agent (first ever run downloads the data lake — be patient)",
        )
        agent = _get_agent()
        registry.mark_running(task_id)
        registry.append_step(task_id, "system", "agent running")

        if hasattr(agent, "go_stream"):
            # Streams each agent message as it happens, so the UI trace
            # panel updates live instead of dumping everything at the end.
            # Guardrails: a stuck model repeats itself indefinitely (burning
            # API credits), so bail on consecutive duplicates or runaway
            # step counts.
            last = ""
            repeats = 0
            steps = 0
            for step in agent.go_stream(prompt):
                if registry.is_cancel_requested(task_id):
                    raise RuntimeError("stopped by user")
                text = str(step.get("output", "")).strip()
                if not text:
                    continue
                repeats = repeats + 1 if text == last else 0
                last = text
                steps += 1
                registry.append_step(task_id, "agent", text)
                if chat_id:  # persist the trace so a page refresh keeps it
                    chat_store.add_message(chat_id, "trace", text)
                if repeats >= 2:
                    raise RuntimeError(
                        "agent repeated the same response 3 times — aborting "
                        "the run to avoid an infinite loop"
                    )
                if steps >= MAX_STEPS:
                    raise RuntimeError(
                        f"agent exceeded {MAX_STEPS} steps — aborting the run"
                    )
            answer = _extract_answer(last)
        else:
            out = agent.go(prompt)
            if isinstance(out, tuple) and len(out) == 2:
                log, answer = out
            else:
                log, answer = [], out
            for entry in log or []:
                registry.append_step(task_id, "agent", str(entry))
                if chat_id:
                    chat_store.add_message(chat_id, "trace", str(entry))
        mark("run_end", f"done in {_fmt_duration(time.time() - t0)}")
        registry.finish(task_id, str(answer))
        if chat_id:
            chat_store.add_message(chat_id, "assistant", str(answer))
    except Exception as exc:  # surface everything to the UI — tools that
        # shell out to Linux-only binaries will land here on Windows.
        logger.exception("task %s failed", task_id)
        message = _friendly_error(exc)
        verb = "stopped" if "stopped by user" in str(exc) else "failed"
        mark("run_end", f"{verb} after {_fmt_duration(time.time() - t0)}")
        registry.fail(task_id, message)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", f"[error] {message}")


def _extract_answer(last_output: str) -> str:
    """The final streamed message carries the answer in a <solution> tag;
    fall back to the whole message if the tag is absent."""
    import re

    m = re.search(r"<solution>(.*?)</solution>", last_output, re.DOTALL)
    return m.group(1).strip() if m else last_output


def _friendly_error(exc: Exception) -> str:
    """Map provider/plumbing exceptions to actionable messages; the full
    traceback stays in the server log via logger.exception."""
    name = type(exc).__name__
    text = str(exc)
    if "stopped by user" in text:
        return "Stopped by user."
    if "AuthenticationError" in name or "invalid_api_key" in text or " 401" in text:
        return (
            "The provider rejected the API key. Update the key in .env "
            "(e.g. OPENAI_API_KEY) and restart the server — the key is read "
            "once at startup."
        )
    if "RateLimit" in name or " 429" in text:
        return "The provider is rate-limiting requests. Wait a moment and retry."
    if "PermissionDenied" in name or "insufficient_quota" in text:
        return (
            "The provider refused the request (quota or permissions). "
            "Check your plan/billing on the provider dashboard."
        )
    if "Connection" in name or "Timeout" in name:
        return (
            "Could not reach the model provider — check your network "
            "connection and try again."
        )
    return f"{name}: {text}"
