"""Runs Biomni A1 tasks on background threads.

Windows-native by design: threads, no fork, no signals. The A1 import and
first construction are lazy so the Flask app boots instantly even before
the ~11 GB data lake exists — the download kicks off on the first task.
"""
from __future__ import annotations

import threading

from ..config import Config
from ..providers import resolve
from .tasks import registry

_agent = None
_agent_lock = threading.Lock()


def _get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            from biomni.agent import A1  # heavy import — keep it lazy

            kwargs = resolve(Config.PROVIDER, Config.MODEL)
            _agent = A1(path=str(Config.DATA_PATH), **kwargs)
        return _agent


def submit(prompt: str, chat_id: str | None = None) -> str:
    """Queue a prompt for the agent; returns a task id immediately. When a
    chat_id is given, the final answer (or error) is appended to that chat."""
    task = registry.create(prompt)
    thread = threading.Thread(
        target=_run, args=(task.id, prompt, chat_id), daemon=True
    )
    thread.start()
    return task.id


def _run(task_id: str, prompt: str, chat_id: str | None = None) -> None:
    from . import chat_store
    try:
        registry.append_step(
            task_id, "system",
            "initializing agent (first ever run downloads the data lake — be patient)",
        )
        agent = _get_agent()
        registry.mark_running(task_id)
        registry.append_step(task_id, "system", "agent running")

        # A1.go() blocks until the run completes and returns the trace + answer.
        # TODO(live streaming): if your installed biomni version exposes a step
        # callback or a go_stream() variant, hook it here and call
        # registry.append_step() per step so the UI updates mid-run.
        out = agent.go(prompt)
        if isinstance(out, tuple) and len(out) == 2:
            log, answer = out
        else:
            log, answer = [], out

        for entry in log or []:
            registry.append_step(task_id, "agent", str(entry))
        registry.finish(task_id, str(answer))
        if chat_id:
            chat_store.add_message(chat_id, "assistant", str(answer))
    except Exception as exc:  # surface everything to the UI — tools that
        # shell out to Linux-only binaries will land here on Windows.
        registry.fail(task_id, f"{type(exc).__name__}: {exc}")
        if chat_id:
            chat_store.add_message(
                chat_id, "assistant", f"[error] {type(exc).__name__}: {exc}"
            )
