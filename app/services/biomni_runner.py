"""Runs Biomni A1 tasks on background threads.

Windows-native by design: threads, no fork, no signals. The A1 import and
first construction are lazy so the Flask app boots instantly even before
the ~11 GB data lake exists — the download kicks off on the first task.
"""
from __future__ import annotations

import logging
import os
import shutil
import textwrap
import threading
import time
from pathlib import Path

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
from ..providers import max_output_tokens, resolve
from . import hints, leads, worker_client, workflow
from .tasks import registry

_agent = None
# Preprocess and review agents, by role: separate A1s, because each A1 rewrites
# its own system prompt per task and keeps its own log.
_stage_agents: dict = {}
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


# Effort for subsequent runs (None = the model's default). Read once when a
# run starts, so switching it mid-run applies to the next run, like the model.
_current_effort: str | None = "max"
# (agent LLM, effort) of the run in progress. Scoped to that LLM so biomni's
# database tools, which build their own, stay on the model's default effort.
_run_effort: tuple | None = None


def get_effort() -> str | None:
    """The effort the next task will run at."""
    return _current_effort


def set_effort(effort: str | None) -> str | None:
    """Switch the effort for subsequent runs. No agent rebuild needed: it's
    applied per request in _patch_sampling_params."""
    global _current_effort
    _current_effort = (effort or "").strip() or None
    return _current_effort


def get_model() -> str | None:
    """The model the next task will run on."""
    return _current_model


def agent_status() -> dict:
    """Whether the A1 agent is built (or being built right now) and whether
    a run holds the run slot — read without taking either lock, so a long
    build or run never blocks the Integrity tab. _agent_lock is only held
    for any length of time while _get_agent() constructs A1."""
    if Config.ISOLATED_RUNS:
        built = worker_client.state["built"]
        return {"built": built, "building": not built and _run_lock.locked(), "run_in_progress": _run_lock.locked()}
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

def drop_agent() -> None:
    """Forgets the built agent, so the next run builds a new one (the data lake
    was repaired). An in-flight run keeps the agent it has."""
    global _agent
    if Config.ISOLATED_RUNS:
        worker_client.drop_remote_agent()
        return
    with _agent_lock:
        _agent = None


# Hard cap on agent messages per run; a legitimate deep analysis stays well
# under this, while a stuck model would otherwise loop forever.
MAX_STEPS = 150


def _get_agent():
    if Config.ISOLATED_RUNS:
        return worker_client.RemoteAgent("main", _current_model)
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
            _patch_run_with_timeout()
            _patch_sampling_params()
            _hide_unusable_tools()
            _patch_cognito_client()
            _agent = _new_agent(A1, kwargs)
        return _agent


def _new_agent(agent_class, kwargs: dict):
    agent = agent_class(path=str(Config.DATA_PATH), **kwargs)
    agent.llm.callbacks = [*(agent.llm.callbacks or []), _untagged_reply_logger()]
    _uncap_agent_llm(agent.llm, kwargs["llm"])
    _quicken_tool_retrieval(agent)
    _label_streamed_text(agent.llm)
    _drop_rejected_thinking_display(agent.llm)
    _ask_when_stuck(agent.llm)
    return agent


def _get_stage_agent(role: str, model: str | None):
    """The agent for one optional stage, built the first time it's needed, on
    the model its run started with. _get_agent() has already run by then, which
    applies the environment and patches every agent relies on."""
    if Config.ISOLATED_RUNS:
        return worker_client.RemoteAgent(role, model)
    with _agent_lock:
        cached = _stage_agents.get(role)
        if cached is None or cached[0] != model:
            from biomni.agent import A1

            cached = (model, _new_agent(A1, resolve(model)))
            _stage_agents[role] = cached
        return cached[1]


def _uncap_agent_llm(llm, model: str) -> None:
    """biomni fixes max_tokens at 8192, which thinking at high effort can use
    up before any reply is written. Give the agent LLM the model's full
    output limit; the SDK refuses to send that much without streaming."""
    limit = max_output_tokens(model)
    if limit:
        llm.max_tokens = limit
    llm.streaming = True


# Model calls and tokens of the run in progress (runs execute one at a time),
# counted by the agent LLM's callback; reset when a run starts. biomni's
# database tools make their own LLM calls, which aren't counted here.
_run_usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}

# The same counts split by stage (main, preprocess, assay, review, revision),
# so the cost of each optional stage is visible. Stages run one after another,
# so the callback adds to whichever one _drive_agent last announced.
_stage_usage: dict[str, dict] = {}
_current_stage = {"name": "main"}

# Relative cost of a token, against a fresh input token: Anthropic bills
# output at 5x and cache reads at 0.1x. These weight the stage shares; they
# are not dollars, which depend on the model's price.
OUTPUT_COST_WEIGHT = 5.0
CACHED_COST_WEIGHT = 0.1


def _record_usage(usage: dict) -> None:
    """Adds one model call's token counts to the run and to its stage."""
    details = usage.get("input_token_details") or {}
    call = {
        "calls": 1,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cached_tokens": details.get("cache_read", 0) or 0,
    }
    stage = _stage_usage.setdefault(
        _current_stage["name"],
        {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0},
    )
    for key, amount in call.items():
        _run_usage[key] += amount
        stage[key] += amount


def reset_usage() -> None:
    """Starts the token counts over, for a run or a worker's stage."""
    _run_usage.update(calls=0, input_tokens=0, output_tokens=0, cached_tokens=0)
    _stage_usage.clear()


def _cost_weight(counts: dict) -> float:
    fresh_input = counts["input_tokens"] - counts["cached_tokens"]
    cached_input = counts["cached_tokens"] * CACHED_COST_WEIGHT
    output = counts["output_tokens"] * OUTPUT_COST_WEIGHT
    return fresh_input + cached_input + output


def stage_breakdown(stages: dict) -> list[dict]:
    """The stages in run order, each with its share (0 to 1) of the run's
    relative cost."""
    total_weight = 0.0
    for counts in stages.values():
        total_weight += _cost_weight(counts)
    rows = []
    for name, counts in stages.items():
        share = 0.0
        if total_weight > 0:
            share = _cost_weight(counts) / total_weight
        rows.append({"name": name, **counts, "share": share})
    return rows

# The latest run's usage summary, for the Integrity tab.
last_run_usage: dict | None = None


def _biomni_usage(texts: list[str], agent) -> dict:
    """What a run drew on from biomni, read off its trace: the biomni tool
    functions its code called and the data lake files its code or output
    named. Code that only uses pandas/matplotlib shows up as nothing here —
    that run got nothing from biomni beyond the agent loop."""
    import re

    trace = "\n".join(texts)
    code = "\n".join(re.findall(r"<execute>(.*?)</execute>", trace, re.DOTALL))
    tool_names = {t["name"] for tools in (agent.module2api or {}).values() for t in tools}
    tools = sorted(n for n in tool_names if re.search(rf"\b{re.escape(n)}\s*\(", code))
    try:
        from biomni.env_desc import data_lake_dict
        lake = sorted(f for f in data_lake_dict if f in trace)
    except Exception:  # noqa: BLE001
        lake = []
    stages = {}
    for name, counts in _stage_usage.items():
        stages[name] = dict(counts)
    return {"tools": tools, "data_lake": lake, **_run_usage, "stages": stages}


def _usage_line(u: dict) -> str:
    tools = ", ".join(u["tools"]) if u["tools"] else "none"
    lake = ", ".join(u["data_lake"]) if u["data_lake"] else "none"
    cached = f" ({u['cached_tokens']:,} cached)" if u["cached_tokens"] else ""
    line = (
        f"biomni usage — tools: {tools} · data lake: {lake} · "
        f"{u['calls']} model calls, {u['input_tokens']:,} input{cached} / "
        f"{u['output_tokens']:,} output tokens"
    )
    rows = stage_breakdown(u.get("stages") or {})
    if len(rows) > 1:
        parts = []
        for row in rows:
            parts.append(f"{row['name']} {row['share']:.0%}")
        line += " · share of cost by stage: " + ", ".join(parts)
    return line


# Tools left out of the agent's prompt because they can't run in this install,
# {name: why} — filled when the agent is built; shown on the Integrity tab.
hidden_tools: dict[str, str] = {}


def _programs_in(source: str) -> set[str]:
    """External programs a tool's code starts: subprocess/os.system calls
    whose command is a literal list or string."""
    import re

    found = set(re.findall(r"subprocess\.\w+\(\s*\[\s*f?[\"']([\w.\-]+)[\"']", source))
    found |= set(re.findall(r"(?:subprocess\.\w+|os\.system)\(\s*f?[\"']([\w.\-]+)\s", source))
    return found


def _usable_tools(module2api: dict) -> dict:
    """biomni's tool list minus what can't work here: whole modules that fail
    to import (genetics and genomics need torch/esm, which aren't installed)
    and tools that start a program this install doesn't have (samtools, R,
    conda, …). Listing them anyway costs prompt tokens and sends the agent
    into failures. Records what was hidden in hidden_tools."""
    import importlib
    import inspect
    import shutil

    hidden_tools.clear()
    usable = {}
    for module_name, tools in module2api.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 — any import failure hides the module
            for tool in tools:
                hidden_tools[tool["name"]] = f"{module_name} doesn't load ({type(exc).__name__}: {exc})"
            continue
        keep = []
        for tool in tools:
            try:
                source = inspect.getsource(getattr(module, tool["name"]))
            except Exception:  # noqa: BLE001 — no source to check: keep it
                source = ""
            missing = sorted(p for p in _programs_in(source) if not shutil.which(p))
            if missing:
                hidden_tools[tool["name"]] = "needs " + ", ".join(missing) + ", not installed"
            else:
                keep.append(tool)
        if keep:
            usable[module_name] = keep
    logger.info("biomni tools: %d usable, %d hidden", sum(map(len, usable.values())), len(hidden_tools))
    return usable


def _hide_unusable_tools() -> None:
    """Filter the tool list A1 reads at construction (its prompt and its
    tool retriever both come from it). Idempotent."""
    import biomni.agent.a1 as a1

    if getattr(a1.read_module2api, "_biomnikhil_patched", False):
        return
    original = a1.read_module2api

    def read_module2api():
        return _usable_tools(original())

    read_module2api._biomnikhil_patched = True
    a1.read_module2api = read_module2api


class ModelRefused(RuntimeError):
    pass


# What the model-call watcher below reports through, set by _drive for the run
# in progress: notice(text) puts a line in the trace, cancelled() says whether
# Stop was pressed.
_run_watch: dict = {"notice": None, "cancelled": None}

# A model call that takes longer than this gets a line in the trace, and
# another each interval after. Thinking is hidden, so a long think and a hung
# connection look the same until then.
CALL_NOTICE_AFTER_SECONDS = 30
CALL_NOTICE_EVERY_SECONDS = 60
CALL_CHECK_SECONDS = 1  # how often the watcher looks at the clock and at Stop


def _post_notice(task_id: str, chat_id: str | None, text: str) -> None:
    """A line in the trace that isn't one of the agent's messages."""
    from . import chat_store

    registry.append_step(task_id, "system", text)
    if chat_id:  # persist the trace so a page refresh keeps it
        chat_store.add_message(chat_id, "trace", text)


def _watch_model_call(finished: threading.Event, notice, cancelled) -> None:
    """Runs on a thread of its own while a model call is out, until `finished`
    is set. Says in the trace that the call is still going, and that a Stop
    waits for it: the reply is read in one piece, so Stop acts once it ends."""
    started = time.monotonic()
    next_notice = CALL_NOTICE_AFTER_SECONDS
    stop_noticed = False
    while not finished.wait(CALL_CHECK_SECONDS):
        elapsed = time.monotonic() - started
        if cancelled() and not stop_noticed:
            stop_noticed = True
            notice("Stop pressed. It takes effect when the model's current reply ends.")
        if elapsed >= next_notice:
            next_notice += CALL_NOTICE_EVERY_SECONDS
            notice(f"Still waiting for the model's reply ({_fmt_duration(elapsed)} so far).")


def _untagged_reply_logger():
    """A callback that records each model call: its tokens, how long it took,
    and the raw reply when it has neither <execute> nor <solution>. biomni
    answers such a reply with a "no tags" message and never shows it, so
    otherwise the trace only shows the complaint; a reply with no text at all
    (only hidden thinking) gets a line of its own, since biomni skips it. A
    call that runs long gets a line while it waits (_watch_model_call). A
    refusal (stop_reason "refusal", empty reply) ends the run right away
    instead: biomni would just ask again and get refused again.
    Built lazily: langchain_core is a heavy import."""
    from langchain_core.callbacks import BaseCallbackHandler

    class UntaggedReplyLogger(BaseCallbackHandler):
        raise_error = True  # so ModelRefused reaches the run

        def __init__(self) -> None:
            self._calls: dict = {}  # run_id -> (finished event, start time)

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
            notice = _run_watch["notice"]
            if notice is None:
                return
            finished = threading.Event()
            self._calls[run_id] = (finished, time.monotonic())
            threading.Thread(
                target=_watch_model_call,
                args=(finished, notice, _run_watch["cancelled"]),
                daemon=True,
            ).start()

        def on_llm_error(self, error, *, run_id, **kwargs):
            self._finish_call(run_id)

        def _finish_call(self, run_id) -> float | None:
            """Stops the call's watcher; returns how long the call took."""
            call = self._calls.pop(run_id, None)
            if call is None:
                return None
            finished, started = call
            finished.set()
            return time.monotonic() - started

        def on_llm_end(self, response, *, run_id, **kwargs):
            elapsed = self._finish_call(run_id)
            for gens in response.generations:
                for gen in gens:
                    message = getattr(gen, "message", None)
                    usage = getattr(message, "usage_metadata", None) or {}
                    _record_usage(usage)
                    content = message.content if message is not None else gen.text
                    text = _text_of(content)
                    meta = getattr(message, "response_metadata", {}) or {}
                    if meta.get("stop_reason") == "refusal":
                        # "model_name" when the reply was streamed.
                        model = meta.get("model") or meta.get("model_name")
                        logger.warning("model refused (model=%s)", model)
                        raise ModelRefused(model or "the model")
                    if "<execute>" not in text and "<solution>" not in text:
                        logger.warning(
                            "model reply without <execute>/<solution> (stop_reason=%s, %s output tokens, %s): %.1500s",
                            meta.get("stop_reason"), usage.get("output_tokens", 0),
                            _fmt_duration(elapsed or 0), text or "(no text)",
                        )
                    if not _text_of(content).strip():
                        notice = _run_watch["notice"]
                        if notice is not None:
                            line = (
                                f"The model's reply had no text ({usage.get('output_tokens', 0):,} output "
                                f"tokens, stop reason {meta.get('stop_reason')}, {_fmt_duration(elapsed or 0)})."
                            )
                            thinking = _thinking_of(content)
                            if thinking:
                                line += f" Its thinking, summarized: {thinking[:MAX_THINKING_NOTICE_CHARS]}"
                            notice(line)

    return UntaggedReplyLogger()


def _text_of(content) -> str:
    """The text of a reply's content. A streamed reply with thinking has its
    text as bare strings in the list, beside the thinking block."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


MAX_THINKING_NOTICE_CHARS = 1500


def _thinking_of(content) -> str:
    """The thinking summary in a reply's content; empty when thinking was
    hidden or the reply is a plain string."""
    if isinstance(content, str):
        return ""
    summaries = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            summaries.append(block.get("thinking") or "")
    return "\n".join(summaries).strip()


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


# What biomni tells the model when a reply has no tag. It's a hard-coded string
# inside biomni's loop, so the request is reworded on its way out instead.
BIOMNI_FORMAT_NUDGE = "Each response must include thinking process"
FORMAT_NUDGE = (
    "Your last reply had no <execute> or <solution> tag, so nothing could run. "
    "Reply again with your reasoning followed by one of them: <execute> to run "
    "code, or <solution> if you have the final answer. If you can't tell what "
    'the user wants, ask them: <execute>ask_user("your question")</execute>.'
)
STUCK_QUESTION = (
    "I couldn't work out a next step. Can you tell me more about what you want, "
    "or what to focus on?"
)

# Replies in a row that biomni couldn't act on, in the run in progress, and
# what the model's thinking said in each (when it was summarized).
_untagged_streak: dict = {"count": 0, "thinking": []}


def _reset_untagged_streak() -> None:
    _untagged_streak["count"] = 0
    _untagged_streak["thinking"].clear()


def _stuck_question(summaries: list[str]) -> str:
    """STUCK_QUESTION, with what the model's thinking said beneath it when
    that was summarized, so the user can see what it was working on."""
    if not summaries:
        return STUCK_QUESTION
    quoted = textwrap.indent("\n\n".join(summaries), "> ", lambda line: True)
    return f"{STUCK_QUESTION}\n\nWhat the model was thinking, summarized:\n\n{quoted}"


def _reword_format_nudge(messages: list) -> None:
    """In place: biomni's "no tags" complaint becomes FORMAT_NUDGE, which tells
    the model about ask_user. Runs before consecutive user turns are merged."""
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if content.startswith(BIOMNI_FORMAT_NUDGE):
                message["content"] = FORMAT_NUDGE
            continue
        for block in content or []:
            text = block.get("text") if isinstance(block, dict) else None
            if isinstance(text, str) and text.startswith(BIOMNI_FORMAT_NUDGE):
                block["text"] = FORMAT_NUDGE


def _is_actionable(text: str) -> bool:
    """Whether biomni can do something with a reply: it looks for these tags,
    and runs a bare code block as code."""
    for marker in ("<execute>", "<solution>", "<think>", "```"):
        if marker in text:
            return True
    return False


def _ask_when_stuck(llm) -> None:
    """After a second reply in a row that biomni can't act on (the first got
    the nudge above), the user is asked what they want. The reply becomes an
    <execute> block calling ask_user, so biomni runs it like any other code:
    the question shows in the chat, the run waits, and the answer goes to the
    model as the code's output."""
    import json

    from langchain_core.messages import AIMessage

    model_invoke = llm.invoke

    def invoke(*args, **kwargs):
        response = model_invoke(*args, **kwargs)
        if _retrieving["active"]:
            return response
        if _is_actionable(_text_of(response.content)):
            _reset_untagged_streak()
            return response
        _untagged_streak["count"] += 1
        thinking = _thinking_of(response.content)
        if thinking:
            _untagged_streak["thinking"].append(thinking[:MAX_THINKING_NOTICE_CHARS])
        if _untagged_streak["count"] < 2:
            return response
        question = _stuck_question(_untagged_streak["thinking"])
        _reset_untagged_streak()
        code = f"ask_user({json.dumps(question)})"
        return AIMessage(content=f"<execute>\n{code}\n</execute>")

    # Set past pydantic, which refuses attributes the model doesn't declare.
    object.__setattr__(llm, "invoke", invoke)


def _add_cache_breakpoints(payload: dict) -> None:
    """Caches the agent loop's prompt between its calls. Every step resends
    the same system prompt plus the history so far, so after the first call
    those tokens are read from cache at a tenth of the input price.

    Two breakpoints: one on the system prompt, which never changes within a
    run, and Bedrock's automatic one (top-level cache_control), which moves to
    the end of the history as it grows. Prefixes under the model's minimum
    (4096 tokens on Haiku 4.5) silently aren't cached."""
    system = payload.get("system")
    if isinstance(system, str) and system:
        payload["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        ]
    payload["cache_control"] = {"type": "ephemeral"}


# Whether agent requests ask for thinking summaries. Thinking is hidden by
# default on newer models, which leaves an empty reply with nothing to explain
# it. Cleared for the process if the endpoint rejects the setting.
_thinking_display = {"summarized": True}


def _thinking_config(model: str, effort_sent: bool) -> dict | None:
    """The `thinking` setting for an agent request, or None to send none.
    Models that need it spelled out get it only along with an effort level,
    as before; models that think by default are sent it to ask for summaries."""
    from ..providers import can_summarize_thinking, needs_explicit_thinking

    if needs_explicit_thinking(model):
        if not effort_sent:
            return None
    elif not can_summarize_thinking(model):
        return None
    config = {"type": "adaptive"}
    if can_summarize_thinking(model) and _thinking_display["summarized"]:
        config["display"] = "summarized"
    return config


# True while biomni picks the tools and data for a task. That is one call that
# answers with a list of numbers, so it needn't think at the run's effort: at
# "max" it spent over a minute and 6-7k tokens of hidden thinking on it before
# the agent began, with nothing to show for it in the chat.
_retrieving = {"active": False}


def _quicken_tool_retrieval(agent) -> None:
    """Marks biomni's tool-selection call (see _retrieving), says in the trace
    that it is under way, and keeps its reply, which has no tags by design,
    from counting as a miss in _ask_when_stuck."""
    retriever = getattr(agent, "retriever", None)
    if retriever is None:
        return
    select = retriever.prompt_based_retrieval

    def prompt_based_retrieval(*args, **kwargs):
        notice = _run_watch["notice"]
        if notice is not None:
            notice("Choosing the tools and data for this task.")
        _retrieving["active"] = True
        try:
            return select(*args, **kwargs)
        finally:
            _retrieving["active"] = False

    retriever.prompt_based_retrieval = prompt_based_retrieval


def _label_streamed_text(llm) -> None:
    """With streaming and thinking both on, langchain-anthropic puts a reply's
    text in the content list as bare strings beside the thinking block. biomni
    reads only {"type": "text"} blocks of a list, so it saw every such reply as
    empty, threw away the code in it and answered with its "no tags" complaint.
    This labels the strings as text blocks before biomni sees the reply."""
    model_invoke = llm.invoke

    def invoke(*args, **kwargs):
        response = model_invoke(*args, **kwargs)
        if isinstance(response.content, list):
            blocks = []
            for block in response.content:
                if isinstance(block, str):
                    if block:
                        blocks.append({"type": "text", "text": block})
                else:
                    blocks.append(block)
            response.content = blocks
        return response

    # Set past pydantic, which refuses attributes the model doesn't declare.
    object.__setattr__(llm, "invoke", invoke)


def _drop_rejected_thinking_display(llm) -> None:
    """If the endpoint rejects thinking.display (a 400 naming it), stops
    sending it for the rest of the process and repeats the call without it,
    so a setting the endpoint doesn't know costs a warning, not a run."""
    from anthropic import BadRequestError

    model_invoke = llm.invoke

    def invoke(*args, **kwargs):
        try:
            return model_invoke(*args, **kwargs)
        except BadRequestError as exc:
            if "display" not in str(exc) or not _thinking_display["summarized"]:
                raise
            _thinking_display["summarized"] = False
            logger.warning("the endpoint rejected thinking.display; going on without thinking summaries: %s", exc)
            return model_invoke(*args, **kwargs)

    # Set past pydantic, which refuses attributes the model doesn't declare.
    object.__setattr__(llm, "invoke", invoke)


def _patch_sampling_params() -> None:
    """biomni's get_llm() always passes a temperature to ChatAnthropic (0.7
    by default, 0.0 for its database tools), but Claude Opus 4.7+, Sonnet 5+
    and Fable/Mythos reject sampling params with a 400 ("`temperature` is
    deprecated for this model"). get_llm is imported by name in six biomni
    modules, so rather than repoint each one, strip the params from the
    outgoing request payload for those models. Older models (Haiku 4.5,
    Sonnet 4.5, …) keep them. The agent loop's requests also get prompt
    caching (_add_cache_breakpoints).

    Also ends every request on a user turn. biomni appends each code result
    as an *assistant* message ("<observation>…"), so the call after any code
    runs ends with the assistant speaking — a "prefill", which newer models
    reject with a 400 ("does not support assistant message prefill").
    Observations come from the environment, not the model, so they're sent
    as user turns. Idempotent."""
    from langchain_anthropic import ChatAnthropic

    from ..providers import rejects_sampling_params, supported_efforts

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
        is_agent_llm = bool(_run_effort and self is _run_effort[0])
        if is_agent_llm:
            _reword_format_nudge(payload.get("messages") or [])
        _observations_as_user_turns(payload.get("messages") or [])
        if is_agent_llm:
            # Only the agent loop: biomni's helper LLMs make one-off calls that
            # would pay the cache write and never read it back.
            _add_cache_breakpoints(payload)
            effort = "low" if _retrieving["active"] else _run_effort[1]
            # Omitted when this model doesn't take that level, which would 400.
            effort_sent = effort in supported_efforts(payload.get("model", ""))
            if effort_sent:
                # extra_body for the same reason as above; merged so anything
                # langchain put in output_config survives.
                extra = payload.setdefault("extra_body", {})
                extra["output_config"] = {
                    **(payload.pop("output_config", None) or {}),
                    **(extra.get("output_config") or {}),
                    "effort": effort,
                }
            thinking = None
            if not _retrieving["active"]:
                thinking = _thinking_config(payload.get("model", ""), effort_sent)
            if thinking:
                extra = payload.setdefault("extra_body", {})
                extra["thinking"] = thinking
                # Thinking 400s on biomni's non-default temperature.
                payload.pop("temperature", None)
                extra.pop("temperature", None)
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


def _patch_run_with_timeout() -> None:
    """biomni stops a code block after agent.timeout_seconds (600). Time the
    block spends in ask_user, waiting on the user, doesn't count toward that:
    someone who takes a few minutes to reply shouldn't time the run out.

    Otherwise the same as biomni's run_with_timeout, which a1 also imports by
    name, so both references are repointed."""
    import ctypes
    import queue

    from biomni import utils

    def _run_with_timeout(func, args=None, kwargs=None, timeout=600):
        args = args or []
        kwargs = kwargs or {}
        results = queue.Queue()

        def target():
            try:
                results.put(("success", func(*args, **kwargs)))
            except Exception as exc:  # noqa: BLE001 — reported to the agent like biomni does
                results.put(("error", str(exc)))

        thread = threading.Thread(target=target, daemon=True)
        waited_before = _seconds_waiting_on_user()
        started = time.monotonic()
        thread.start()
        while True:
            thread.join(1)
            if not thread.is_alive():
                break
            waited = _seconds_waiting_on_user() - waited_before
            busy = time.monotonic() - started - waited
            if busy > timeout:
                # biomni's way of stopping the thread: raise SystemExit in it.
                stopped = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_long(thread.ident), ctypes.py_object(SystemExit)
                )
                if stopped > 1:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread.ident), None)
                return (
                    f"ERROR: Code execution timed out after {timeout} seconds. Please try "
                    "with simpler inputs or break your task into smaller steps."
                )
        try:
            status, result = results.get(block=False)
        except queue.Empty:
            return "Error: Execution completed but no result was returned"
        if status == "success":
            return result
        return f"Error in execution: {result}"

    utils.run_with_timeout = _run_with_timeout
    import biomni.agent.a1 as a1_module

    a1_module.run_with_timeout = _run_with_timeout


# Execution guidance now lives in app/hints/ (core.txt always, plus
# domain-scoped packs like pyopenms.txt/mzml.txt only when the prompt
# actually looks relevant) instead of one hardcoded, ever-growing string —
# see services/hints.py for why.


def _link_input(source: Path, link: Path) -> None:
    """Puts an attached file in a run's input folder without duplicating it
    if possible: mzML files run to several GB. Windows only allows symlinks
    with extra privileges and hardlinks within one volume, hence the chain."""
    try:
        os.symlink(source, link)
        return
    except OSError:
        pass
    try:
        os.link(source, link)
        return
    except OSError:
        pass
    shutil.copy2(source, link)


def _prepare_inputs(task_id: str, attached: list[str]) -> list[Path]:
    """The run's own input folder, holding only the files attached to it."""
    input_dir = Config.INPUT_PATH / task_id
    input_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for name in attached:
        source = Path(name)
        link = input_dir / source.name
        if link.exists():
            continue
        _link_input(source, link)
        links.append(link)
    return links


def _inputs_note(links: list[Path]) -> str:
    """Tells the agent which user files this run may use. It guides the agent
    rather than enforcing anything: its code runs with full file access."""
    if links:
        lines = []
        for link in links:
            lines.append(f"- {link}")
        listing = "\n".join(lines)
        intro = f"The user attached these files (local paths):\n{listing}\n\nThese are"
    else:
        intro = "The user attached no files to this message, so there are"
    return (
        f"\n\n{intro} the only user files available to this run. Don't look "
        "for others on disk or rely on files from earlier runs. If the task "
        "needs a file that isn't listed and the message gives no path to it, "
        "say so and ask for it to be attached."
    )


def _reset_run_namespace() -> None:
    """Empties the namespace agent code runs in, so no variable or loaded
    dataframe from an earlier run or chat carries over. biomni re-injects
    its custom tools into it before executing each code block."""
    from biomni.tool import support_tools

    support_tools._persistent_namespace.clear()


# How long the run has spent in ask_user: the total of finished waits, and
# when the current wait began. One run at a time, so one of these is enough.
_user_wait = {"total": 0.0, "since": None}

ASK_USER_NOTE = (
    "\n\nIf the task can't be done without something only the user knows "
    "(which gene, sample, file or threshold they mean, for example), don't "
    "guess and don't explore to find out. Ask them from an <execute> block: "
    '`reply = ask_user("your question")`. The run pauses until they answer, '
    "and the call returns their reply as a string. Put all your questions in "
    "one call, and only ask when the answer changes what you'd do. A simple "
    "question you can answer right away gets its answer in <solution>."
)


def _seconds_waiting_on_user() -> float:
    total = _user_wait["total"]
    since = _user_wait["since"]
    if since is not None:
        total += time.monotonic() - since
    return total


def _wait_on_user(ask):
    """The ask_user(question) the agent's code can call, around `ask`, which
    takes the question to the user and returns their reply. The time spent
    waiting doesn't count toward the code's timeout, and the reply is printed
    too, so it reaches the agent's observation even if the code ignores the
    value."""

    def ask_user(question) -> str:
        """Ask the user a question and wait for their reply, which is returned."""
        question = str(question).strip()
        started = time.monotonic()
        _user_wait["since"] = started
        try:
            answer = ask(question)
        finally:
            _user_wait["since"] = None
            _user_wait["total"] += time.monotonic() - started
        print(f"The user replied: {answer}")
        return answer

    return ask_user


def _ask_user_function(task_id: str, chat_id: str | None):
    """ask_user for a run inside the app: the question shows in the chat, the
    run waits, and the reply goes to the agent."""
    from . import chat_store

    def ask(question: str) -> str:
        registry.ask(task_id, question)
        if chat_id:
            chat_store.add_message(chat_id, "question", question)
        answer = registry.wait_for_answer(task_id)
        if chat_id:
            chat_store.add_message(chat_id, "answer", answer)
        return answer

    return _wait_on_user(ask)


def submit(
    prompt: str,
    chat_id: str | None = None,
    user: auth.User | None = None,
    attached: list[str] | None = None,
    features: dict[str, bool] | None = None,
    earlier_messages: list[dict] | None = None,
    chat_model: dict | None = None,
) -> str:
    """Queue a prompt for the agent; returns a task id immediately. When a
    chat_id is given, the final answer (or error) is appended to that chat.
    `user` is the signed-in user whose credentials the run's model calls
    are signed with. `attached` holds the paths of the files attached to
    this message. `features` says which workflow stages are on.
    `earlier_messages` is the chat so far, and `chat_model` the {model, effort}
    this chat runs on."""
    # The id comes first because the input folder is named after it, and the
    # listing has to be in the task's prompt, so it's added once they exist.
    task = registry.create(prompt, chat_id=chat_id)
    links = _prepare_inputs(task.id, attached or [])
    prompt = prompt + _inputs_note(links)
    user_prompt = prompt

    # Give this run its own output folder so generated files (reports,
    # CSVs, plots) can be found afterward and served back to the UI,
    # without different runs' outputs colliding.
    output_dir = Config.OUTPUT_PATH / task.id
    output_dir.mkdir(parents=True, exist_ok=True)
    features = workflow.selected_features(features)
    lab_context = workflow.read_lab_context() if features["context"] else ""
    prompt_with_hint = (
        prompt
        + workflow.conversation_note(earlier_messages or [])
        + workflow.lab_context_note(lab_context)
        + _leads_note()
        + hints.build_hints(prompt)
        + ASK_USER_NOTE
        + f"\n\nSave any output files you generate (CSVs, plots, reports, "
        f"etc.) under this directory: {output_dir} — create it if needed "
        "and use subfolders freely; anything written there becomes "
        "downloadable from the UI after the run."
    )

    thread = threading.Thread(
        target=_run,
        args=(task.id, prompt_with_hint, chat_id, user_prompt, output_dir, user, features, links, lab_context, chat_model),
        daemon=True,
    )
    thread.start()
    return task.id


def _leads_note() -> str:
    """The leads board's settled leads for the prompt. A board that can't be
    read never stops a run."""
    try:
        return workflow.leads_note(leads.list_leads())
    except Exception:  # noqa: BLE001
        logger.exception("couldn't read the leads board for a prompt")
        return ""


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
            # Always "/"-separated: the UI splits on "/" to build URLs and
            # folder labels, and os.path.relpath gives "\\" on Windows.
            rel = Path(os.path.relpath(path, output_dir)).as_posix()
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
    features: dict[str, bool] | None = None,
    input_files: list[Path] | None = None,
    lab_context: str = "",
    chat_model: dict | None = None,
) -> None:
    from . import chat_store

    features = workflow.selected_features(features)
    input_files = input_files or []

    # Run boundary markers: the trace panel renders these as ▶/■ divider
    # lines, both live (registry steps) and on replay (chat messages).
    t0 = time.time()

    def mark(kind: str, text: str) -> None:
        registry.append_step(task_id, kind, text)
        if chat_id:
            chat_store.add_message(chat_id, kind, text)

    mark("run_start", user_prompt or prompt)
    holds_slot = False
    texts: list[str] = []  # the run's agent messages, for the usage summary
    agent = None

    def report_usage() -> None:
        """Adds the run's biomni usage to the trace, just before it ends."""
        global last_run_usage
        if agent is None:
            return
        try:
            last_run_usage = {**_biomni_usage(texts, agent), "task_id": task_id, "at": time.time()}
            line = _usage_line(last_run_usage)
            logger.info("task %s %s", task_id, line)
            registry.append_step(task_id, "system", line)
            if chat_id:
                chat_store.add_message(chat_id, "trace", line)
        except Exception:  # noqa: BLE001 — stats never break a run
            logger.exception("usage summary failed")
    try:
        _acquire_run_slot(task_id)
        holds_slot = True
        auth.set_run_user(user)
        reset_usage()
        # Set here, holding the slot, so a queued run can't switch a live one.
        if chat_model:
            if chat_model["model"]:
                set_model(chat_model["model"])
            set_effort(chat_model["effort"])
        # Both stay fixed for the whole run, even if the UI changes them mid-way.
        run_model = _current_model
        run_effort = _current_effort
        agent = _get_agent()
        input_fingerprints = workflow.file_fingerprints(input_files)
        registry.mark_running(task_id)
        registry.append_step(task_id, "system", "agent running")
        stage_count = (
            1
            + int(features["preprocess"] and bool(input_files))
            + int(features["assay"] and bool(input_files))
            + int(features["review"])
        )
        registry.set_max_steps(task_id, MAX_STEPS * stage_count)

        def note(text: str) -> None:
            registry.append_step(task_id, "system", text)
            if chat_id:
                chat_store.add_message(chat_id, "trace", text)

        def run_stage(role: str, stage_prompt: str, usage_name: str | None = None) -> tuple[str, list[str]]:
            stage_agent = agent if role == "main" else _get_stage_agent(role, run_model)
            stage_texts: list[str] = []
            try:
                answer = _drive_agent(
                    stage_agent, stage_prompt, task_id, chat_id, stage_texts, run_effort,
                    stage=usage_name or role,
                )
                return answer, stage_texts
            finally:
                texts.extend(stage_texts)

        blocked_reason = None
        preprocess_warning = None
        if features["preprocess"] and not input_files:
            note("data preprocessing skipped: no files attached")
        elif features["preprocess"]:
            note("data preprocessing agent started")
            folder = output_dir / workflow.PREPROCESS_FOLDER
            try:
                report, _stage_texts = run_stage(
                    "preprocess",
                    workflow.preprocess_prompt(user_prompt, input_files, folder, lab_context)
                    + hints.build_hints(user_prompt),
                )
            except Exception as exc:  # noqa: BLE001 — the task can still run on the originals
                if _is_stop(exc):
                    raise
                logger.exception("task %s preprocessing failed", task_id)
                reason = _friendly_error(exc)
                preprocess_warning = f"Data preprocessing didn't finish, so this used your original files: {reason}"
                note(f"data preprocessing failed, using the original files: {reason}")
            else:
                blocked_reason = workflow.preprocess_blocked_reason(report)
                if blocked_reason is None:
                    prompt += workflow.preprocess_note(folder, report)
                    if not workflow.preprocess_reported_ready(report):
                        preprocess_warning = "The data preprocessing agent didn't report a clear status, so check its data report."
                note("data preprocessing agent finished")

        main_steps_start = len(registry.get(task_id)["steps"])
        main_steps_end = main_steps_start
        save_recipe = blocked_reason is None
        if blocked_reason is not None:
            answer = f"**Stopped by the data preprocessing agent.**\n\n{blocked_reason}"
        else:
            main_texts: list[str] = []
            answer = _drive_agent(agent, prompt, task_id, chat_id, main_texts, run_effort)
            texts.extend(main_texts)
            main_steps_end = len(registry.get(task_id)["steps"])
            if preprocess_warning:
                answer = f"{answer}\n\n---\n\n**{preprocess_warning}**"
            if features["assay"] and not input_files:
                note("planted-truth assay skipped: no files attached")
            elif features["assay"]:
                # Before the review, so the reviewer can check the assay too.
                answer = _assay_answer(
                    answer, user_prompt, main_texts, output_dir,
                    input_files, lab_context, note, run_stage, task_id,
                )
            if features["review"] and registry.is_cancel_requested(task_id):
                answer = f"{answer}\n\n---\n\n**Stopped before the adversarial review, so this answer is unreviewed.**"
                save_recipe = False
            elif features["review"]:
                answer, save_recipe = _review_answer(
                    answer, prompt, user_prompt, main_texts, output_dir,
                    input_files, lab_context, note, run_stage, task_id,
                )
        changed_inputs = workflow.changed_files(input_fingerprints)
        if changed_inputs:
            names = []
            for path in changed_inputs:
                names.append(path.name)
            listing = ", ".join(names)
            note(f"attached files changed during the run: {listing}")
            answer = f"{answer}\n\n---\n\n**Warning: these attached files changed during the run:** {listing}"
        files = _collect_output_files(output_dir, task_id) if output_dir else []
        report_usage()
        mark("run_end", f"done in {_fmt_duration(time.time() - t0)}")
        registry.finish(task_id, str(answer), files=files)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", str(answer), files=files)
        snap = registry.get(task_id)
        if snap and save_recipe:
            # Only the main task's steps: a recipe shouldn't replay a review.
            main_steps = snap["steps"][main_steps_start:main_steps_end]
            hints.save_recipe_if_verified(user_prompt, main_steps, files)
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
        report_usage()
        mark("run_end", f"{verb} after {_fmt_duration(time.time() - t0)}")
        registry.fail(task_id, message, files=files)
        if chat_id:
            chat_store.add_message(chat_id, "assistant", f"[error] {message}", files=files)
    finally:
        # Removes only the links; the uploads they point at stay.
        shutil.rmtree(Config.INPUT_PATH / task_id, ignore_errors=True)
        if holds_slot:
            auth.set_run_user(None)
            _run_lock.release()


def _is_stop(exc: Exception) -> bool:
    return "stopped by user" in str(exc)


def _drive_agent(
    agent, prompt: str, task_id: str, chat_id: str | None, texts: list[str],
    effort: str | None, stage: str = "main",
) -> str:
    """One agent's run, from an emptied namespace; returns its answer and adds
    its messages to `texts`. `stage` is the name its token usage is counted under."""
    if isinstance(agent, worker_client.RemoteAgent):
        return agent.drive(prompt, task_id, chat_id, texts, effort, stage)

    from . import chat_store

    def emit(text: str) -> None:
        registry.append_step(task_id, "agent", text)
        if chat_id:  # persist the trace so a page refresh keeps it
            chat_store.add_message(chat_id, "trace", text)

    def notice(text: str) -> None:
        _post_notice(task_id, chat_id, text)

    def cancelled() -> bool:
        return registry.is_cancel_requested(task_id)

    return _drive(
        agent, prompt, texts, effort, stage, emit, notice, cancelled,
        _ask_user_function(task_id, chat_id),
    )


def _drive(agent, prompt: str, texts: list[str], effort, stage: str, emit, notice, cancelled, ask_user) -> str:
    """Runs an agent. `emit(text)` gets each of its messages, `notice(text)` a
    line for the trace that isn't one (a model call that is slow, or came back
    empty), `cancelled()` says whether to stop, and `ask_user` is what its code
    calls to ask the user; this is the part that runs wherever the agent lives,
    the app or the worker."""
    global _run_effort
    from biomni.tool import support_tools

    _reset_run_namespace()
    _reset_untagged_streak()
    _current_stage["name"] = stage
    support_tools._persistent_namespace["ask_user"] = ask_user
    _run_effort = (agent.llm, effort)
    _run_watch.update(notice=notice, cancelled=cancelled)

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
            if cancelled():
                raise RuntimeError("stopped by user")
            text = str(step.get("output", "")).strip()
            if not text:
                continue
            repeats = repeats + 1 if text == last else 0
            last = text
            steps += 1
            texts.append(text)
            emit(text)
            if repeats >= 2:
                raise RuntimeError(
                    "agent repeated the same response 3 times — aborting "
                    "the run to avoid an infinite loop"
                )
            if steps >= MAX_STEPS:
                raise RuntimeError(
                    f"agent exceeded {MAX_STEPS} steps — aborting the run"
                )
        return _extract_answer(last)

    out = agent.go(prompt)
    if isinstance(out, tuple) and len(out) == 2:
        log, answer = out
    else:
        log, answer = [], out
    for entry in log or []:
        emit(str(entry))
    return answer


def _assay_answer(
    answer, user_prompt, main_texts, output_dir,
    input_files, lab_context, note, run_stage, task_id,
) -> str:
    """Runs the planted-truth assay on the main run's analysis and adds what it
    found to the answer. Whatever goes wrong here, the answer is kept."""
    folder = output_dir / workflow.ASSAY_FOLDER
    note("planted-truth assay agent started")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        code_file = folder / "analysis-code.txt"
        code_file.write_text(workflow.analysis_code(main_texts), encoding="utf-8")
        report, _assay_texts = run_stage(
            "assay",
            workflow.assay_prompt(
                user_prompt, str(answer), code_file,
                input_files, folder, lab_context,
            ) + hints.build_hints(user_prompt),
        )
    except Exception as exc:  # noqa: BLE001
        if _is_stop(exc):
            note("planted-truth assay stopped")
            return f"{answer}\n\n---\n\n**The planted-truth assay was stopped, so this analysis is untested.**"
        logger.exception("task %s assay failed", task_id)
        message = _friendly_error(exc)
        note(f"planted-truth assay failed: {message}")
        return f"{answer}\n\n---\n\n**The planted-truth assay failed, so this analysis is untested:** {message}"
    note("planted-truth assay agent finished")

    status = workflow.assay_status(report)
    if status == "RAN":
        heading = "Planted-truth assay"
    elif status == "NOT APPLICABLE":
        heading = "The planted-truth assay doesn't apply to this analysis"
    elif status == "COULDN'T REPRODUCE":
        heading = "The planted-truth assay couldn't reproduce this analysis, so it tested nothing"
    else:
        heading = "The planted-truth assay gave no clear status, so don't count it as a test"
    return f"{answer}\n\n---\n\n**{heading}**\n\n{report}"


def _review_answer(
    answer, task_prompt, user_prompt, main_texts, output_dir,
    input_files, lab_context, note, run_stage, task_id,
) -> tuple[str, bool]:
    """Runs the review, and one revision if the answer doesn't hold. Whatever
    goes wrong here, the main task's answer is kept. Returns the answer and
    whether the review passed the main run, so its code can become a recipe."""
    folder = output_dir / workflow.REVIEW_FOLDER
    note("adversarial review agent started")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        code_file = folder / "analysis-code.txt"
        code_file.write_text(workflow.analysis_code(main_texts), encoding="utf-8")
        review, _review_texts = run_stage(
            "review",
            workflow.review_prompt(
                user_prompt, str(answer), code_file, output_dir,
                input_files, folder, lab_context,
            ) + hints.build_hints(user_prompt),
        )
    except Exception as exc:  # noqa: BLE001
        if _is_stop(exc):
            note("adversarial review stopped")
            return f"{answer}\n\n---\n\n**Adversarial review was stopped, so this answer is unreviewed.**", False
        logger.exception("task %s review failed", task_id)
        message = _friendly_error(exc)
        note(f"adversarial review failed: {message}")
        return f"{answer}\n\n---\n\n**Adversarial review failed, so this answer is unreviewed:** {message}", False
    note("adversarial review agent finished")

    review_section = f"**Adversarial review**\n\n{review}"
    verdict = workflow.review_verdict(review)
    if verdict is None:
        return f"{answer}\n\n---\n\n**The review gave no clear verdict, so don't count it as a pass.**\n\n{review_section}", False
    if verdict != "DOES NOT HOLD":
        return f"{answer}\n\n---\n\n{review_section}", True

    note("revising the answer after the review")
    registry.set_max_steps(task_id, registry.get(task_id)["max_steps"] + MAX_STEPS)
    try:
        # The main agent, so the revision starts with a fresh namespace but
        # the same tools; A1 keeps no messages between runs.
        revised, _revision_texts = run_stage(
            "main",
            workflow.revision_prompt(task_prompt, str(answer), review, output_dir / workflow.REVISION_FOLDER),
            usage_name="revision",
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_stop(exc):
            logger.exception("task %s revision failed", task_id)
        note("revision didn't finish")
        return f"{answer}\n\n---\n\n**This answer did not hold up in review, and the revision didn't finish.**\n\n{review_section}", False
    revised_answer = (
        f"{revised}\n\n---\n\n**Revised after review. The revision itself wasn't re-reviewed.**"
        f"\n\n{review_section}\n\n---\n\n**Original answer**\n\n{answer}"
    )
    return revised_answer, False


def _extract_answer(last_output: str) -> str:
    """The final streamed message carries the answer in a <solution> tag;
    fall back to the whole message if the tag is absent."""
    import re

    m = re.search(r"<solution>(.*?)</solution>", last_output, re.DOTALL)
    return m.group(1).strip() if m else last_output


def _friendly_error(exc: Exception) -> str:
    """Map Bedrock/plumbing exceptions to actionable messages; the full
    traceback stays in the server log via logger.exception."""
    # An error from the worker keeps the name it had over there.
    name = getattr(exc, "remote_type", None) or type(exc).__name__
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
