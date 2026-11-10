"""biomni_runner patches private parts of biomni, langchain-anthropic and the
anthropic SDK (see the pins in app/requirements.txt). These tests fail when a
version bump moves what a patch replaces, before a release ships it.

The last test drives the real A1 agent loop end to end, with a scripted model
in place of Bedrock and no data lake."""
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app import auth
from app.services import biomni_runner
from conftest import FakeUser


@pytest.fixture
def run_user():
    auth.set_run_user(FakeUser())
    yield
    auth.set_run_user(None)


def test_run_python_repl_keeps_traceback_and_earlier_output():
    biomni_runner._patch_run_python_repl()
    import biomni.agent.a1 as a1
    from biomni.tool import support_tools

    # Both references, or <execute> blocks still get the original.
    assert a1.run_python_repl is support_tools.run_python_repl

    output = support_tools.run_python_repl("print('before the error')\nvalue = 1 / 0")
    assert "before the error" in output
    assert "ZeroDivisionError" in output
    assert "line 2" in output


def _temperature(payload):
    """Where langchain-anthropic put temperature: top level, or extra_body when
    the installed SDK's create() doesn't name it."""
    if "temperature" in payload:
        return payload["temperature"]
    extra = payload.get("extra_body") or {}
    return extra.get("temperature")


def test_sampling_params_dropped_only_for_models_that_reject_them():
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    messages = [HumanMessage("hi")]
    newer = ChatAnthropic(model="anthropic.claude-opus-4-7", api_key="unused", temperature=0.7)
    older = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused", temperature=0.7)
    assert _temperature(newer._get_request_payload(messages)) is None
    assert _temperature(older._get_request_payload(messages)) == 0.7


def test_observations_are_sent_as_user_turns():
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")
    messages = [
        HumanMessage("task"),
        AIMessage("<execute>print(1)</execute>"),
        AIMessage("<observation>1</observation>"),
    ]
    sent = llm._get_request_payload(messages)["messages"]
    roles = [m["role"] for m in sent]
    assert roles == ["user", "assistant", "user"]


def test_bedrock_requests_are_signed_with_the_run_users_credentials(run_user):
    biomni_runner._patch_cognito_client()
    from langchain_anthropic import ChatAnthropic

    client = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")._client
    assert type(client).__name__ == "CognitoMantle"

    request = httpx.Request("POST", "https://bedrock-mantle.us-east-1.api.aws/v1/messages", content=b"{}")
    client._prepare_request(request)
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDTESTONLY/")
    assert "/bedrock-mantle/aws4_request" in request.headers["authorization"]


def test_bedrock_requests_need_a_signed_in_user():
    biomni_runner._patch_cognito_client()
    from langchain_anthropic import ChatAnthropic

    auth.set_run_user(None)
    client = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")._client
    request = httpx.Request("POST", "https://bedrock-mantle.us-east-1.api.aws/v1/messages", content=b"{}")
    with pytest.raises(auth.SignInRequired):
        client._prepare_request(request)


def test_unusable_tools_are_hidden():
    biomni_runner._hide_unusable_tools()
    import biomni.agent.a1 as a1

    usable = a1.read_module2api()
    assert usable, "every biomni tool was hidden"
    for module_name, tools in usable.items():
        for tool in tools:
            assert tool["name"] not in biomni_runner.hidden_tools


def test_agent_loop_round_trip(tmp_path, run_user):
    """One <execute> step and a <solution> through A1's real graph, with every
    patch applied the way _get_agent applies them."""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    biomni_runner._patch_run_python_repl()
    biomni_runner._patch_sampling_params()
    biomni_runner._hide_unusable_tools()
    biomni_runner._patch_cognito_client()
    from biomni.agent import A1

    agent = A1(
        path=str(tmp_path),
        llm="anthropic.claude-haiku-4-5",
        source="Anthropic",
        use_tool_retriever=False,
        expected_data_lake_files=[],  # skips the 14 GB download
    )
    agent.llm = FakeMessagesListChatModel(responses=[
        AIMessage("<execute>print(6 * 7)</execute>"),
        AIMessage("<solution>42</solution>"),
    ])

    outputs = []
    for step in agent.go_stream("What is 6 times 7?"):
        outputs.append(step["output"])
    trace = "\n".join(outputs)
    assert "<observation>" in trace and "42" in trace
    assert biomni_runner._extract_answer(outputs[-1]) == "42"


def test_effort_is_sent_only_for_the_run_llm_and_supporting_models(monkeypatch):
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    messages = [HumanMessage("hi")]
    agent_llm = ChatAnthropic(model="anthropic.claude-opus-5-5", api_key="unused")
    tool_llm = ChatAnthropic(model="anthropic.claude-opus-5-5", api_key="unused")
    haiku = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")

    def effort(llm):
        sent = llm._get_request_payload(messages)
        return ((sent.get("extra_body") or {}).get("output_config") or {}).get("effort")

    monkeypatch.setattr(biomni_runner, "_run_effort", (agent_llm, "max"))
    assert effort(agent_llm) == "max"
    assert effort(tool_llm) is None
    monkeypatch.setattr(biomni_runner, "_run_effort", (haiku, "max"))
    assert effort(haiku) is None
    monkeypatch.setattr(biomni_runner, "_run_effort", (agent_llm, None))
    assert effort(agent_llm) is None


def test_prompt_caching_only_for_the_run_llm(monkeypatch):
    biomni_runner._patch_sampling_params()
    import json

    import anthropic
    import httpx2
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import SystemMessage

    messages = [SystemMessage("biomni's system prompt"), HumanMessage("task")]
    agent_llm = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")
    tool_llm = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")
    monkeypatch.setattr(biomni_runner, "_run_effort", (agent_llm, None))

    helper_payload = tool_llm._get_request_payload(messages)
    assert "cache_control" not in helper_payload
    assert helper_payload["system"] == "biomni's system prompt"

    agent_payload = agent_llm._get_request_payload(messages)
    assert agent_payload["cache_control"] == {"type": "ephemeral"}
    assert agent_payload["system"][0]["cache_control"] == {"type": "ephemeral"}

    # The SDK must put both on the wire, not drop the top-level field.
    sent_bodies = []

    def capture(request):
        sent_bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
            "content": [], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    client = anthropic.Anthropic(api_key="unused", http_client=httpx2.Client(transport=httpx2.MockTransport(capture)))
    client.messages.create(**agent_payload)
    assert sent_bodies[0]["cache_control"] == {"type": "ephemeral"}
    assert sent_bodies[0]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_thinking_is_sent_where_a_model_needs_it_or_hides_it(monkeypatch):
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    def thinking(model, effort="high"):
        llm = ChatAnthropic(model=model, api_key="unused", temperature=0.7)
        monkeypatch.setattr(biomni_runner, "_run_effort", (llm, effort))
        payload = llm._get_request_payload([HumanMessage("hi")])
        return (payload.get("extra_body") or {}).get("thinking"), payload

    summarized = {"type": "adaptive", "display": "summarized"}
    sent, payload = thinking("anthropic.claude-opus-4-8")
    assert sent == summarized
    assert _temperature(payload) is None
    # Opus 4.6 needs it spelled out but already sends a summary.
    assert thinking("anthropic.claude-opus-4-6")[0] == {"type": "adaptive"}
    # Models that think by default are sent it for the summary, effort or not.
    assert thinking("anthropic.claude-opus-5-5")[0] == summarized
    assert thinking("anthropic.claude-opus-5-5", effort=None)[0] == summarized
    # Without an effort level Opus 4.8 still runs without thinking, as before.
    assert thinking("anthropic.claude-opus-4-8", effort=None)[0] is None
    assert thinking("anthropic.claude-haiku-4-5")[0] is None

    monkeypatch.setitem(biomni_runner._thinking_display, "summarized", False)
    assert thinking("anthropic.claude-opus-4-8")[0] == {"type": "adaptive"}


def test_a_rejected_thinking_display_is_dropped_and_the_call_repeated(monkeypatch):
    import anthropic
    import httpx2

    def bad_request(message):
        response = httpx2.Response(400, request=httpx2.Request("POST", "http://example.test"))
        return anthropic.BadRequestError(message, response=response, body=None)

    class FakeLLM:
        def __init__(self, error):
            self.error = error
            self.calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise self.error
            return "reply"

    monkeypatch.setitem(biomni_runner._thinking_display, "summarized", True)
    llm = FakeLLM(bad_request("thinking.adaptive.display: Extra inputs are not permitted"))
    biomni_runner._drop_rejected_thinking_display(llm)
    assert llm.invoke("hi") == "reply"
    assert llm.calls == 2
    assert biomni_runner._thinking_display["summarized"] is False

    # Any other 400 is not ours to hide.
    monkeypatch.setitem(biomni_runner._thinking_display, "summarized", True)
    other = FakeLLM(bad_request("prompt is too long"))
    biomni_runner._drop_rejected_thinking_display(other)
    with pytest.raises(anthropic.BadRequestError):
        other.invoke("hi")
    assert biomni_runner._thinking_display["summarized"] is True


def test_uncapped_agent_llm_streams_a_full_size_request(monkeypatch):
    """The real SDK refuses max_tokens this large unless streaming; the reply
    must still reach biomni and the callback intact."""
    import json

    import anthropic
    import httpx2
    from langchain_anthropic import ChatAnthropic

    biomni_runner._patch_sampling_params()
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "m", "type": "message", "role": "assistant", "model": "x", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 5, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "<solution>42</solution>"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "refusal", "stop_sequence": None},
                           "usage": {"input_tokens": 5, "output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    sse = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=sse)

    client = anthropic.Anthropic(api_key="unused", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    monkeypatch.setattr(ChatAnthropic, "_create", lambda self, payload: client.messages.with_raw_response.create(**payload))

    llm = ChatAnthropic(model="anthropic.claude-opus-5-5", api_key="unused", max_tokens=8192)
    biomni_runner._uncap_agent_llm(llm, "anthropic.claude-opus-5-5")
    monkeypatch.setattr(biomni_runner, "_run_effort", (llm, "max"))
    reply = llm.invoke([HumanMessage("hi")])

    body = requests[0]
    assert body["stream"] is True and body["max_tokens"] == 128_000
    assert body["output_config"]["effort"] == "max"
    assert reply.content == "<solution>42</solution>" or reply.content[0]["text"] == "<solution>42</solution>"
    # What the refusal check and usage line read.
    assert reply.response_metadata["stop_reason"] == "refusal"
    assert reply.usage_metadata["input_tokens"] == 5


def test_agent_can_ask_the_user_mid_run(tmp_path, run_user):
    """ask_user pauses the code block until the reply, longer than the code
    timeout, and the reply lands in the observation the model reads next."""
    import threading
    import time

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from app.services.tasks import registry

    biomni_runner._patch_run_python_repl()
    biomni_runner._patch_run_with_timeout()
    biomni_runner._patch_sampling_params()
    biomni_runner._hide_unusable_tools()
    biomni_runner._patch_cognito_client()
    from biomni.agent import A1

    agent = A1(
        path=str(tmp_path),
        llm="anthropic.claude-haiku-4-5",
        source="Anthropic",
        use_tool_retriever=False,
        expected_data_lake_files=[],
    )
    agent.timeout_seconds = 1
    agent.llm = FakeMessagesListChatModel(responses=[
        AIMessage('<execute>gene = ask_user("Which gene?")</execute>'),
        AIMessage("<solution>done</solution>"),
    ])
    task = registry.create("Design primers")
    registry.mark_running(task.id)

    def reply_when_asked():
        while registry.get(task.id)["question"] is None:
            time.sleep(0.05)
        time.sleep(2)  # past the 1s code timeout
        registry.answer(task.id, "BRCA1")

    threading.Thread(target=reply_when_asked, daemon=True).start()
    texts = []
    try:
        answer = biomni_runner._drive_agent(agent, "Design primers", task.id, None, texts, None)
    finally:
        registry.finish(task.id, "")
    assert answer == "done"
    trace = "\n".join(texts)
    assert "The user replied: BRCA1" in trace
    assert "timed out" not in trace


def test_format_nudge_tells_the_model_about_ask_user(monkeypatch):
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="anthropic.claude-haiku-4-5", api_key="unused")
    monkeypatch.setattr(biomni_runner, "_run_effort", (llm, None))
    messages = [
        HumanMessage("task"),
        AIMessage("no tags here"),
        HumanMessage(biomni_runner.BIOMNI_FORMAT_NUDGE + " followed by either <execute> or <solution> tag."),
    ]
    sent = llm._get_request_payload(messages)["messages"]
    assert sent[-1]["role"] == "user"
    last_text = sent[-1]["content"] if isinstance(sent[-1]["content"], str) else sent[-1]["content"][-1]["text"]
    assert last_text == biomni_runner.FORMAT_NUDGE
    assert "ask_user" in last_text


def test_user_is_asked_after_two_replies_biomni_cannot_use(tmp_path, run_user):
    """An empty reply gets the nudge; a second one makes the app ask the user,
    and their answer goes back to the model."""
    import threading
    import time

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from app.services.tasks import registry

    biomni_runner._patch_run_python_repl()
    biomni_runner._patch_run_with_timeout()
    biomni_runner._patch_sampling_params()
    biomni_runner._hide_unusable_tools()
    biomni_runner._patch_cognito_client()
    from biomni.agent import A1

    agent = A1(
        path=str(tmp_path),
        llm="anthropic.claude-haiku-4-5",
        source="Anthropic",
        use_tool_retriever=False,
        expected_data_lake_files=[],
    )
    agent.llm = FakeMessagesListChatModel(responses=[
        AIMessage(""),
        AIMessage(""),
        AIMessage("<solution>done</solution>"),
    ])
    biomni_runner._ask_when_stuck(agent.llm)
    task = registry.create("Do the thing")
    registry.mark_running(task.id)
    asked = []

    def reply_when_asked():
        while registry.get(task.id)["question"] is None:
            time.sleep(0.05)
        asked.append(registry.get(task.id)["question"])
        registry.answer(task.id, "use TP53")

    threading.Thread(target=reply_when_asked, daemon=True).start()
    texts = []
    try:
        answer = biomni_runner._drive_agent(agent, "Do the thing", task.id, None, texts, None)
    finally:
        registry.finish(task.id, "")
    assert answer == "done"
    assert asked == [biomni_runner.STUCK_QUESTION]
    assert "The user replied: use TP53" in "\n".join(texts)


def test_usage_is_counted_per_stage():
    biomni_runner._run_usage.update(calls=0, input_tokens=0, output_tokens=0, cached_tokens=0)
    biomni_runner._stage_usage.clear()

    biomni_runner._current_stage["name"] = "main"
    biomni_runner._record_usage({"input_tokens": 1000, "output_tokens": 100,
                                 "input_token_details": {"cache_read": 800}})
    biomni_runner._current_stage["name"] = "assay"
    biomni_runner._record_usage({"input_tokens": 500, "output_tokens": 50})
    biomni_runner._record_usage({"input_tokens": 500, "output_tokens": 50})

    assert biomni_runner._run_usage["calls"] == 3
    assert biomni_runner._run_usage["input_tokens"] == 2000
    assert biomni_runner._stage_usage["main"]["cached_tokens"] == 800
    assert biomni_runner._stage_usage["assay"]["calls"] == 2

    rows = biomni_runner.stage_breakdown(biomni_runner._stage_usage)
    assert [row["name"] for row in rows] == ["main", "assay"]
    assert abs(sum(row["share"] for row in rows) - 1.0) < 1e-9
    # Main: 200 fresh + 80 cached-weighted + 500 output = 780; assay: 1000 + 500 = 1500.
    assert rows[0]["share"] < rows[1]["share"]

    line = biomni_runner._usage_line({
        "tools": [], "data_lake": [], **biomni_runner._run_usage,
        "stages": biomni_runner._stage_usage,
    })
    assert "share of cost by stage: main 34%, assay 66%" in line

    biomni_runner._stage_usage.clear()
    biomni_runner._current_stage["name"] = "main"


def _watched_run(monkeypatch, cancelled=lambda: False):
    """The reply logger, set up as _drive does for a run; returns it and the
    lines it puts in the trace."""
    lines = []
    monkeypatch.setitem(biomni_runner._run_watch, "notice", lines.append)
    monkeypatch.setitem(biomni_runner._run_watch, "cancelled", cancelled)
    return biomni_runner._untagged_reply_logger(), lines


def _result_with(content, output_tokens=1223, stop_reason="end_turn"):
    from langchain_core.outputs import ChatGeneration, LLMResult

    message = AIMessage(
        content=content,
        usage_metadata={"input_tokens": 18291, "output_tokens": output_tokens, "total_tokens": 0},
        response_metadata={"stop_reason": stop_reason},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_a_reply_of_only_hidden_thinking_says_so_in_the_trace(monkeypatch):
    import uuid

    logger, lines = _watched_run(monkeypatch)
    thinking_only = ["", {"type": "thinking", "thinking": "", "signature": "abc"}]
    logger.on_llm_end(_result_with(thinking_only), run_id=uuid.uuid4())
    assert len(lines) == 1
    assert "no text" in lines[0]
    assert "1,223 output tokens" in lines[0]
    assert "end_turn" in lines[0]


def test_a_reply_with_text_adds_no_line(monkeypatch):
    import uuid

    logger, lines = _watched_run(monkeypatch)
    logger.on_llm_end(_result_with("I'll look at the data first."), run_id=uuid.uuid4())
    assert lines == []


def test_a_slow_model_call_and_a_stop_waiting_on_it_show_in_the_trace(monkeypatch):
    import time
    import uuid

    monkeypatch.setattr(biomni_runner, "CALL_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(biomni_runner, "CALL_NOTICE_AFTER_SECONDS", 0.1)
    monkeypatch.setattr(biomni_runner, "CALL_NOTICE_EVERY_SECONDS", 100)
    stop = []
    logger, lines = _watched_run(monkeypatch, cancelled=lambda: bool(stop))
    run_id = uuid.uuid4()
    logger.on_chat_model_start({}, [], run_id=run_id)
    deadline = time.time() + 5
    while not lines and time.time() < deadline:
        time.sleep(0.05)
    assert lines[0].startswith("Still waiting for the model's reply")

    stop.append(True)
    while len(lines) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert lines[1].startswith("Stop pressed")

    # Once the call ends the watcher goes quiet.
    logger.on_llm_error(RuntimeError("done"), run_id=run_id)
    time.sleep(0.3)
    assert len(lines) == 2


def test_an_empty_reply_shows_the_models_thinking_summary(monkeypatch):
    import uuid

    logger, lines = _watched_run(monkeypatch)
    summary = "The user didn't say which protein, so I should ask."
    content = ["", {"type": "thinking", "thinking": summary, "signature": "abc"}]
    logger.on_llm_end(_result_with(content), run_id=uuid.uuid4())
    assert len(lines) == 1
    assert lines[0].endswith("Its thinking, summarized: " + summary)


def test_the_question_after_two_empty_replies_carries_the_thinking_summary(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    def thinking_only(summary):
        return AIMessage(content=["", {"type": "thinking", "thinking": summary, "signature": "abc"}])

    llm = FakeMessagesListChatModel(responses=[
        thinking_only("No protein was named."),
        thinking_only("I need to ask which one.\nThen search."),
    ])
    biomni_runner._reset_untagged_streak()
    biomni_runner._ask_when_stuck(llm)
    assert llm.invoke("task").content == ["", {"type": "thinking", "thinking": "No protein was named.", "signature": "abc"}]
    question_call = llm.invoke("task").content
    assert biomni_runner.STUCK_QUESTION in question_call
    assert "> No protein was named." in question_call
    assert "> I need to ask which one.\\n> Then search." in question_call
    assert biomni_runner._untagged_streak == {"count": 0, "thinking": []}


def test_the_question_is_unchanged_when_thinking_was_not_summarized():
    assert biomni_runner._stuck_question([]) == biomni_runner.STUCK_QUESTION


def _streaming_llm(text):
    """A ChatAnthropic that streams a reply of thinking and then `text`, the
    way the endpoint does, ending on the </execute> stop sequence."""
    import json

    import anthropic
    import httpx2
    from langchain_anthropic import ChatAnthropic

    message = {"id": "m", "type": "message", "role": "assistant", "model": "x", "content": [],
               "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}
    events = [
        {"type": "message_start", "message": message},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "I'll pick TP53."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "stop_sequence", "stop_sequence": "</execute>"},
         "usage": {"output_tokens": 50}},
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    transport = httpx2.MockTransport(
        lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
    )
    client = anthropic.Anthropic(api_key="unused", http_client=httpx2.Client(transport=transport))
    llm = ChatAnthropic(model="claude-opus-4-8", api_key="unused", streaming=True)
    llm.__dict__["_client"] = client
    return llm


def test_text_streamed_beside_thinking_reaches_biomni_as_a_text_block():
    """biomni reads only {"type": "text"} blocks of a list reply; streamed text
    arrives as a bare string, which made every such reply look empty."""
    code = "<execute>\nprint(1)\n"
    llm = _streaming_llm(code)
    biomni_runner._reset_untagged_streak()
    biomni_runner._label_streamed_text(llm)
    biomni_runner._ask_when_stuck(llm)

    reply = llm.invoke("hi")

    assert {"type": "text", "text": code} in reply.content
    biomnis_reading = "".join(
        block.get("text", "") for block in reply.content if isinstance(block, dict) and block.get("type") == "text"
    )
    assert biomnis_reading == code
    assert biomni_runner._untagged_streak["count"] == 0


def test_text_beside_thinking_is_not_reported_as_an_empty_reply(monkeypatch):
    import uuid

    logger, lines = _watched_run(monkeypatch)
    content = ["", {"type": "thinking", "thinking": "A plan.", "signature": "abc"}, "<execute>print(1)</execute>"]
    logger.on_llm_end(_result_with(content), run_id=uuid.uuid4())
    assert lines == []
    assert biomni_runner._text_of(content) == "<execute>print(1)</execute>"


def test_tool_selection_goes_out_at_low_effort_without_thinking(monkeypatch):
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model="anthropic.claude-opus-4-8", api_key="unused")
    monkeypatch.setattr(biomni_runner, "_run_effort", (llm, "max"))

    def sent():
        payload = llm._get_request_payload([HumanMessage("hi")])
        extra = payload.get("extra_body") or {}
        return (extra.get("output_config") or {}).get("effort"), extra.get("thinking")

    assert sent() == ("max", {"type": "adaptive", "display": "summarized"})
    monkeypatch.setitem(biomni_runner._retrieving, "active", True)
    assert sent() == ("low", None)


def test_tool_selection_is_marked_announced_and_not_counted_as_a_miss(monkeypatch):
    seen = []

    class Retriever:
        def prompt_based_retrieval(self, query, resources, llm=None):
            seen.append(biomni_runner._retrieving["active"])
            return {"tools": []}

    class Agent:
        retriever = Retriever()

    lines = []
    monkeypatch.setitem(biomni_runner._run_watch, "notice", lines.append)
    biomni_runner._quicken_tool_retrieval(Agent)

    assert Agent.retriever.prompt_based_retrieval("task", {}) == {"tools": []}
    assert seen == [True]
    assert biomni_runner._retrieving["active"] is False
    assert lines == ["Choosing the tools and data for this task."]

    # The reply to it has no tags; it must not count toward asking the user.
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    llm = FakeMessagesListChatModel(responses=[AIMessage("TOOLS: [1, 2]"), AIMessage("<execute>print(1)</execute>")])
    biomni_runner._reset_untagged_streak()
    biomni_runner._ask_when_stuck(llm)
    monkeypatch.setitem(biomni_runner._retrieving, "active", True)
    llm.invoke("select")
    monkeypatch.setitem(biomni_runner._retrieving, "active", False)
    assert biomni_runner._untagged_streak["count"] == 0
