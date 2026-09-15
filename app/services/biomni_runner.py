"""Runs Biomni A1 tasks on background threads.

Windows-native by design: threads, no fork, no signals. The A1 import and
first construction are lazy so the Flask app boots instantly even before
the ~11 GB data lake exists — the download kicks off on the first task.
"""
from __future__ import annotations

import logging
import os
import threading
import time

# Every task runs on a background thread (see _run below), but matplotlib
# defaults to a GUI backend (MacOSX/Qt/etc.) that only works on the main
# thread — any agent-generated plotting code crashes with "Cannot create a
# GUI FigureManager outside the main thread" otherwise. Must be set before
# matplotlib is ever imported anywhere in this process (by biomni's tool
# modules or by the agent's own exec()'d code), so it happens here, at
# import time of this module, which loads well before any task runs.
os.environ.setdefault("MPLBACKEND", "Agg")

logger = logging.getLogger(__name__)

from ..config import Config
from ..providers import resolve
from . import hints
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

            _patch_run_python_repl()
            _agent = A1(path=str(Config.DATA_PATH), **kwargs)
        return _agent


def _patch_run_python_repl() -> None:
    """biomni's code-execution tool discards two things on error: the real
    traceback (just `f"Error: {str(e)}"` — no exception type, no line
    number, nothing pointing at what actually failed) and any stdout the
    code printed before it crashed (the error string replaces it entirely
    rather than appending). "Error: can not handle type of ()" with zero
    other context is a direct result of this. Patch it to keep both.

    Two things need patching, not one: biomni.agent.a1 does
    `from biomni.tool.support_tools import run_python_repl` at its own
    module level, which snapshots the function at that moment rather than
    tracking the module attribute live — so patching
    support_tools.run_python_repl alone doesn't affect the actual
    <execute>-tag executor (a1.run_with_timeout(run_python_repl, ...) still
    calls the original). Both references get repointed here.
    """
    import re
    import sys
    import traceback
    from io import StringIO

    from biomni.tool import support_tools

    def _signature_hint(exc: Exception, source: str) -> str:
        """If the failing line called a method on a pyopenms object, look
        up that exact method's real docstring (the same help() text the
        agent would get if it thought to check) and attach it right next
        to the error. Targeted at the actual recurring failure — wrong
        pybind11 call signatures — without a persistent cache or any
        cross-run state; best-effort, never raises."""
        try:
            # The deepest frame overall is usually inside pyopenms's own
            # Cython internals (a real .pyx frame, not the agent's code) —
            # walk the chain and keep the last frame that's still within
            # the exec'd <string>, not the absolute deepest one.
            frame = exc.__traceback__
            target = None
            while frame:
                if frame.tb_frame.f_code.co_filename == "<string>":
                    target = frame
                frame = frame.tb_next
            if target is None:
                return ""
            frame = target
            lines = source.splitlines()
            if not (0 < frame.tb_lineno <= len(lines)):
                return ""
            m = re.search(r"(\w+)\.(\w+)\s*\(", lines[frame.tb_lineno - 1])
            if not m:
                return ""
            obj_name, method_name = m.groups()
            f_locals = frame.tb_frame.f_locals
            obj = f_locals.get(obj_name, frame.tb_frame.f_globals.get(obj_name))
            if obj is None or not type(obj).__module__.startswith("pyopenms"):
                return ""
            method = getattr(type(obj), method_name, None)
            doc = (method.__doc__ or "").strip() if method else ""
            if not doc:
                return ""
            return (
                f"\n\nReal signature for {type(obj).__name__}.{method_name} "
                f"(use this instead of guessing):\n{doc[:600]}"
            )
        except Exception:  # noqa: BLE001 — this is a bonus, never the main error path
            return ""

    def _verbose_run_python_repl(command: str) -> str:
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()
        clean_command = command.strip("```").strip()
        try:
            support_tools._apply_matplotlib_patches()
            exec(clean_command, support_tools._persistent_namespace)
            return mystdout.getvalue()
        except Exception as e:
            partial = mystdout.getvalue()
            prefix = f"{partial}\n" if partial else ""
            hint = _signature_hint(e, clean_command)
            return f"{prefix}Error:\n{traceback.format_exc()}{hint}"
        finally:
            sys.stdout = old_stdout

    support_tools.run_python_repl = _verbose_run_python_repl
    try:
        import biomni.agent.a1 as a1_module

        a1_module.run_python_repl = _verbose_run_python_repl
    except ImportError:
        pass  # not loaded yet — the support_tools patch alone still covers it


# Execution guidance now lives in app/hints/ (core.txt always, plus
# domain-scoped packs like pyopenms.txt/mzml.txt only when the prompt
# actually looks relevant) instead of one hardcoded, ever-growing string —
# see services/hints.py for why.


def submit(prompt: str, chat_id: str | None = None) -> str:
    """Queue a prompt for the agent; returns a task id immediately. When a
    chat_id is given, the final answer (or error) is appended to that chat."""
    user_prompt = prompt
    task = registry.create(prompt, chat_id=chat_id)

    # Give this run its own output folder so generated files (reports,
    # CSVs, plots) can be found afterward and served back to the UI,
    # without different runs' outputs colliding.
    output_dir = Config.OUTPUT_PATH / task.id
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_with_hint = (
        prompt
        + hints.build_hints(prompt)
        + f"\n\nSave any output files you generate (CSVs, plots, reports, "
        f"etc.) under this directory: {output_dir} — create it if needed "
        "and use subfolders freely; anything written there becomes "
        "downloadable from the UI after the run."
    )

    thread = threading.Thread(
        target=_run,
        args=(task.id, prompt_with_hint, chat_id, user_prompt, output_dir),
        daemon=True,
    )
    thread.start()
    return task.id


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s"


def _collect_output_files(output_dir, task_id: str) -> list[dict]:
    """Manifest of everything a run wrote to its output folder — served
    back to the UI so results are viewable/downloadable, not just described
    in the answer text. task_id travels with each entry (not just the
    surrounding message) because chat messages replayed after a server
    restart have no other way to know which task's output folder to hit."""
    files = []
    if not output_dir.exists():
        return files
    for root, _dirs, names in os.walk(output_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            rel = os.path.relpath(path, output_dir)
            files.append({"name": name, "path": rel, "size": size, "task_id": task_id})
    files.sort(key=lambda f: f["path"])
    return files


def _run(
    task_id: str,
    prompt: str,
    chat_id: str | None = None,
    user_prompt: str = "",
    output_dir=None,
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
        files = _collect_output_files(output_dir, task_id) if output_dir else []
        mark("run_end", f"done in {_fmt_duration(time.time() - t0)}")
        registry.finish(task_id, str(answer), files=files)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", str(answer), files=files)
        snap = registry.get(task_id)
        if snap:
            hints.save_recipe_if_verified(user_prompt, snap["steps"], files)
    except Exception as exc:  # surface everything to the UI — tools that
        # shell out to Linux-only binaries will land here on Windows.
        prompt_preview = (user_prompt or prompt or "")[:200]
        logger.exception(
            "task %s failed (chat=%s, provider=%s, model=%s, elapsed=%s, "
            "output_dir=%s, prompt=%r)",
            task_id, chat_id, Config.PROVIDER, Config.MODEL,
            _fmt_duration(time.time() - t0), output_dir, prompt_preview,
        )
        message = _friendly_error(exc)
        verb = "stopped" if "stopped by user" in str(exc) else "failed"
        files = _collect_output_files(output_dir, task_id) if output_dir else []
        mark("run_end", f"{verb} after {_fmt_duration(time.time() - t0)}")
        registry.fail(task_id, message, files=files)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", f"[error] {message}", files=files)


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
