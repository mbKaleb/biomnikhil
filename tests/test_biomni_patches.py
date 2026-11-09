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


def test_explicit_thinking_only_where_models_need_it(monkeypatch):
    biomni_runner._patch_sampling_params()
    from langchain_anthropic import ChatAnthropic

    def sent(model):
        llm = ChatAnthropic(model=model, api_key="unused", temperature=0.7)
        monkeypatch.setattr(biomni_runner, "_run_effort", (llm, "high"))
        return llm._get_request_payload([HumanMessage("hi")])

    older = sent("anthropic.claude-opus-4-8")
    assert older["extra_body"]["thinking"] == {"type": "adaptive"}
    assert _temperature(older) is None
    assert "thinking" not in sent("anthropic.claude-opus-5-5")["extra_body"]


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
