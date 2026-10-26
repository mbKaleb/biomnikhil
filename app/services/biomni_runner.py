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

from .. import auth
from ..config import Config
from ..providers import resolve
from . import hints
from .tasks import registry

_agent = None
_agent_lock = threading.Lock()

# One run at a time. Every task shares the single cached A1 — its log,
# memory checkpointer, system prompt and the REPL's persistent namespace —
# and the REPL captures output by swapping the process-wide sys.stdout, so
# two concurrent runs would leak variables into each other and clobber each
# other's output. Later tasks wait here (still "queued", still cancellable).
_run_lock = threading.Lock()


def _acquire_run_slot(task_id: str) -> None:
    if _run_lock.acquire(blocking=False):
        return
    registry.append_step(task_id, "system", "waiting for the current run to finish")
    while not _run_lock.acquire(timeout=1):
        if registry.is_cancel_requested(task_id):
            raise RuntimeError("stopped by user")

# Model used to build the agent. Starts from .env (Config.MODEL) but can be
# switched at runtime from the UI via set_model(); changing it drops the
# cached agent so the next task rebuilds A1 with the new model. An in-flight
# run is unaffected — it already holds its own agent reference.
_current_model: str | None = Config.MODEL


def get_model() -> str | None:
    """The model the next task will run on."""
    return _current_model


def agent_status() -> dict:
    """Whether the A1 agent is built (or being built right now) and whether
    a run holds the run slot — read without taking either lock, so a long
    build or run never blocks the Integrity tab. _agent_lock is only held
    for any length of time while _get_agent() constructs A1."""
    built = _agent is not None
    return {
        "built": built,
        "building": not built and _agent_lock.locked(),
        "run_in_progress": _run_lock.locked(),
    }


def set_model(model: str | None) -> str | None:
    """Switch the model for subsequent runs. Drops the cached agent so it
    rebuilds against the new model on the next task. Returns the model now in
    effect. No-op (and no rebuild) if the model is unchanged."""
    global _current_model, _agent
    model = (model or "").strip() or None
    with _agent_lock:
        if model != _current_model:
            _current_model = model
            _agent = None  # force a rebuild on the next _get_agent()
    return _current_model

# Hard cap on agent messages per run; a legitimate deep analysis stays well
# under this, while a stuck model would otherwise loop forever.
MAX_STEPS = 150


def _get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            kwargs = resolve(_current_model)

            # The A1 constructor kwargs only override the *agent* LLM.
            # Biomni's database/query tools use its default BiomniConfig,
            # which defaults to claude-sonnet-4-5 and reads BIOMNI_LLM from
            # the env — point it at the same model so those tools don't
            # demand an Anthropic key we may not have. Assigned (not
            # setdefault) so a runtime model switch takes effect for those
            # tools too, not just the main agent loop. Must happen before
            # the biomni import below, which is when that config loads.
            import os

            os.environ["BIOMNI_LLM"] = kwargs["llm"]
            # Bedrock ids ("anthropic.claude-…") would otherwise auto-detect
            # as biomni's "Bedrock" source (langchain-aws, not installed) in
            # those tools; pin them to the agent's source.
            os.environ["BIOMNI_SOURCE"] = os.environ["LLM_SOURCE"] = kwargs["source"]

            from biomni.agent import A1  # heavy import — keep it lazy

            _patch_run_python_repl()
            _patch_sampling_params()
            _patch_cognito_client()
            _agent = A1(path=str(Config.DATA_PATH), **kwargs)
            _agent.llm.callbacks = [*(_agent.llm.callbacks or []), _untagged_reply_logger()]
        return _agent


class ModelRefused(RuntimeError):
    pass


def _untagged_reply_logger():
    """A callback that logs the model's raw reply when it has neither
    <execute> nor <solution>. biomni answers such a reply with a "no tags"
    message and never shows it, so otherwise the trace only shows the
    complaint. A refusal (stop_reason "refusal", empty reply) ends the run
    right away instead: biomni would just ask again and get refused again.
    Built lazily: langchain_core is a heavy import."""
    from langchain_core.callbacks import BaseCallbackHandler

    class UntaggedReplyLogger(BaseCallbackHandler):
        raise_error = True  # so ModelRefused reaches the run

        def on_llm_end(self, response, **kwargs):
            for gens in response.generations:
                for gen in gens:
                    message = getattr(gen, "message", None)
                    content = message.content if message is not None else gen.text
                    text = content if isinstance(content, str) else repr(content)
                    meta = getattr(message, "response_metadata", {}) or {}
                    if meta.get("stop_reason") == "refusal":
                        logger.warning("model refused (model=%s)", meta.get("model"))
                        raise ModelRefused(meta.get("model") or "the model")
                    if "<execute>" not in text and "<solution>" not in text:
                        logger.warning(
                            "model reply without <execute>/<solution> (stop_reason=%s): %.1500s",
                            meta.get("stop_reason"), text,
                        )

    return UntaggedReplyLogger()


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _observations_as_user_turns(messages: list) -> None:
    """In place: assistant messages that are biomni code results become user
    messages, merged into a user message right before them; if the
    conversation still ends on the assistant, a short user turn follows."""
    out = []
    for m in messages:
        if m.get("role") == "assistant" and _text_of(m.get("content", "")).lstrip().startswith("<observation>"):
            m = {**m, "role": "user"}
        if out and m["role"] == out[-1]["role"] == "user":
            prev = out[-1]["content"]
            prev = [{"type": "text", "text": prev}] if isinstance(prev, str) else list(prev)
            cur = m["content"]
            cur = [{"type": "text", "text": cur}] if isinstance(cur, str) else list(cur)
            out[-1] = {**out[-1], "content": prev + cur}
        else:
            out.append(m)
    if out and out[-1]["role"] == "assistant":
        out.append({"role": "user", "content": "Continue."})
    messages[:] = out


def _patch_sampling_params() -> None:
    """biomni's get_llm() always passes a temperature to ChatAnthropic (0.7
    by default, 0.0 for its database tools), but Claude Opus 4.7+, Sonnet 5+
    and Fable/Mythos reject sampling params with a 400 ("`temperature` is
    deprecated for this model"). get_llm is imported by name in six biomni
    modules, so rather than repoint each one, strip the params from the
    outgoing request payload for those models. Older models (Haiku 4.5,
    Sonnet 4.5, …) keep them.

    Also ends every request on a user turn. biomni appends each code result
    as an *assistant* message ("<observation>…"), so the call after any code
    runs ends with the assistant speaking — a "prefill", which newer models
    reject with a 400 ("does not support assistant message prefill").
    Observations come from the environment, not the model, so they're sent
    as user turns. Idempotent."""
    from langchain_anthropic import ChatAnthropic

    from ..providers import rejects_sampling_params

    if getattr(ChatAnthropic._get_request_payload, "_biomnikhil_patched", False):
        return
    original = ChatAnthropic._get_request_payload

    def _get_request_payload(self, *args, **kwargs):
        payload = original(self, *args, **kwargs)
        if rejects_sampling_params(payload.get("model", "")):
            # langchain-anthropic routes these through extra_body when the
            # installed SDK's create() doesn't name them, so check both.
            extra = payload.get("extra_body") or {}
            for key in ("temperature", "top_p", "top_k"):
                payload.pop(key, None)
                extra.pop(key, None)
            if "extra_body" in payload and not extra:
                payload.pop("extra_body")
        _observations_as_user_turns(payload.get("messages") or [])
        return payload

    _get_request_payload._biomnikhil_patched = True
    ChatAnthropic._get_request_payload = _get_request_payload


_cognito_patched = False


def _patch_cognito_client() -> None:
    """Point every ChatAnthropic in the process (the agent loop and biomni's
    helper LLMs) at Bedrock Mantle, SigV4-signing each request with the
    Cognito credentials of the user whose run is in progress
    (auth.run_credentials) rather than an API key. The SDK's own Mantle
    client signs with credentials fixed at construction, but the agent and
    its clients are cached across runs and users, so signing is done per
    request here. Idempotent."""
    global _cognito_patched
    if _cognito_patched:
        return
    from functools import cached_property

    from anthropic.lib.bedrock import AnthropicBedrockMantle
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from langchain_anthropic import ChatAnthropic

    class CognitoMantle(AnthropicBedrockMantle):
        def _prepare_request(self, request) -> None:
            headers = {k: v for k, v in request.headers.items() if k.lower() != "connection"}
            aws_request = AWSRequest(
                method=request.method, url=str(request.url), headers=headers, data=request.read(),
            )
            SigV4Auth(auth.run_credentials(), "bedrock-mantle", self.aws_region).add_auth(aws_request)
            request.headers.update(
                {k: v for k, v in aws_request.prepare().headers.items() if v is not None}
            )

    def _client(self):
        params = {k: v for k, v in self._client_params.items() if k not in ("api_key", "base_url")}
        # skip_auth: the SDK adds no auth of its own; _prepare_request signs.
        return CognitoMantle(aws_region=Config.BEDROCK_REGION, skip_auth=True, **params)

    prop = cached_property(_client)
    prop.__set_name__(ChatAnthropic, "_client")
    ChatAnthropic._client = prop
    _cognito_patched = True


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


def submit(prompt: str, chat_id: str | None = None, user: auth.User | None = None) -> str:
    """Queue a prompt for the agent; returns a task id immediately. When a
    chat_id is given, the final answer (or error) is appended to that chat.
    `user` is the signed-in user whose credentials the run's model calls
    are signed with."""
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
        args=(task.id, prompt_with_hint, chat_id, user_prompt, output_dir, user),
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
    user: auth.User | None = None,
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
    holds_slot = False
    try:
        _acquire_run_slot(task_id)
        holds_slot = True
        auth.set_run_user(user)
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
            "task %s failed (chat=%s, model=%s, elapsed=%s, "
            "output_dir=%s, prompt=%r)",
            task_id, chat_id, _current_model or Config.MODEL,
            _fmt_duration(time.time() - t0), output_dir, prompt_preview,
        )
        message = _friendly_error(exc)
        verb = "stopped" if "stopped by user" in str(exc) else "failed"
        files = _collect_output_files(output_dir, task_id) if output_dir else []
        mark("run_end", f"{verb} after {_fmt_duration(time.time() - t0)}")
        registry.fail(task_id, message, files=files)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", f"[error] {message}", files=files)
    finally:
        if holds_slot:
            auth.set_run_user(None)
            _run_lock.release()


def _extract_answer(last_output: str) -> str:
    """The final streamed message carries the answer in a <solution> tag;
    fall back to the whole message if the tag is absent."""
    import re

    m = re.search(r"<solution>(.*?)</solution>", last_output, re.DOTALL)
    return m.group(1).strip() if m else last_output


def _friendly_error(exc: Exception) -> str:
    """Map Bedrock/plumbing exceptions to actionable messages; the full
    traceback stays in the server log via logger.exception."""
    name = type(exc).__name__
    text = str(exc)
    if "stopped by user" in text:
        return "Stopped by user."
    if isinstance(exc, ModelRefused):
        return (
            f"{text} declined this request (a safety refusal, not an error in "
            "your prompt or the app). Switch to another model in the menu at "
            "the top and run it again."
        )
    if isinstance(exc, auth.SignInRequired):
        return f"{text} (Reload the page to sign in.)"
    if "AuthenticationError" in name or "PermissionDenied" in name:
        return (
            "Bedrock refused the signed request. Sign out and back in; if it "
            "persists, check that the bedrock-app-access role allows "
            f"bedrock-mantle:CreateInference. ({name}: {text})"
        )
    if "RateLimit" in name or " 429" in text:
        return "Bedrock is rate-limiting requests. Wait a moment and retry."
    if "Connection" in name or "Timeout" in name:
        return (
            "Could not reach Bedrock — check your network connection and try again."
        )
    return f"{name}: {text}"
