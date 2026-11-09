"""The Leads board: how a lead reads what was checked off an answer, and the API."""
import pytest

from app.config import Config
from app.services import biomni_runner, chat_store, leads
from app.services.tasks import registry


class ScriptedAgent:
    """Answers each prompt with the next reply; an Exception is raised instead."""

    llm = None
    module2api = {}

    def __init__(self, replies):
        self.replies = list(replies)

    def go_stream(self, prompt):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield {"output": f"<solution>{reply}</solution>"}


def _answer_from_run(monkeypatch, tmp_path, features, main, review=(), assay=()):
    """The final answer of a real run, so these tests read exactly what the
    runner writes rather than a copy of it."""
    agents = {
        "main": ScriptedAgent(main),
        "review": ScriptedAgent(review),
        "assay": ScriptedAgent(assay),
    }
    monkeypatch.setattr(biomni_runner, "_get_agent", lambda: agents["main"])
    monkeypatch.setattr(biomni_runner, "_get_stage_agent", lambda role, model: agents[role])
    monkeypatch.setattr(biomni_runner, "_reset_run_namespace", lambda: None)
    chat_id = chat_store.create_chat("test")["id"]
    task = registry.create("a prompt", chat_id=chat_id)
    output_dir = Config.OUTPUT_PATH / task.id
    output_dir.mkdir(parents=True)
    input_files = [tmp_path / "data.csv"]
    biomni_runner._run(task.id, "a prompt", chat_id, "a prompt", output_dir, None, features, input_files, "")
    return registry.get(task.id)["result"]


@pytest.fixture(autouse=True)
def empty_board():
    for lead in leads.list_leads():
        leads.delete_lead(lead["id"])


@pytest.mark.parametrize("reply, expected", [
    ("VERDICT: HOLDS\nchecked it", "holds"),
    ("VERDICT: HOLDS WITH CAVEATS\nmostly", "caveats"),
    ("it looked fine to me", "unclear"),
    (RuntimeError("the reviewer crashed"), "unreviewed"),
])
def test_review_state_reads_what_the_runner_wrote(monkeypatch, tmp_path, reply, expected):
    answer = _answer_from_run(monkeypatch, tmp_path, {"review": True}, ["found TP53"], review=[reply])
    assert leads.review_state(answer) == expected


def test_a_revised_answer_is_marked_revised(monkeypatch, tmp_path):
    answer = _answer_from_run(
        monkeypatch, tmp_path, {"review": True}, ["first try", "second try"],
        review=["VERDICT: DOES NOT HOLD\nthe test was wrong"],
    )
    assert leads.review_state(answer) == "revised"


def test_an_answer_that_failed_review_and_could_not_be_revised(monkeypatch, tmp_path):
    answer = _answer_from_run(
        monkeypatch, tmp_path, {"review": True}, ["first try", RuntimeError("no more")],
        review=["VERDICT: DOES NOT HOLD\nthe test was wrong"],
    )
    assert leads.review_state(answer) == "does_not_hold"


def test_an_answer_with_no_review_has_none():
    assert leads.review_state("TP53 is up 3x in treated samples.") == "none"


@pytest.mark.parametrize("reply, expected", [
    ("ASSAY: RAN\nfound 3x and up", "ran"),
    ("ASSAY: NOT APPLICABLE\nno group comparison", "not_applicable"),
    ("ASSAY: COULDN'T REPRODUCE\nhits differ", "couldnt_reproduce"),
    ("some text with no status", "unclear"),
    (RuntimeError("the assay crashed"), "untested"),
])
def test_assay_state_reads_what_the_runner_wrote(monkeypatch, tmp_path, reply, expected):
    answer = _answer_from_run(monkeypatch, tmp_path, {"assay": True}, ["found TP53"], assay=[reply])
    assert leads.assay_state(answer) == expected


def test_an_answer_with_no_assay_has_none():
    assert leads.assay_state("TP53 is up 3x in treated samples.") == "none"


@pytest.mark.parametrize("text, expected", [
    ("# TP53 is up in treated samples\n\nDetails follow.", "TP53 is up in treated samples"),
    ("\n\n**Key finding:** the knockout rescues growth\nmore", "Key finding: the knockout rescues growth"),
    ("- a bulleted first line", "a bulleted first line"),
    ("", ""),
    ("   \n  \n", ""),
])
def test_title_comes_from_the_first_line(text, expected):
    assert leads.title_from(text) == expected


def test_a_long_first_line_is_cut_at_a_word():
    title = leads.title_from("word " * 60)
    assert title.endswith("…")
    assert len(title) <= leads.DERIVED_TITLE_CHARS + 1
    assert "wor…" not in title


def _post(client, token, **body):
    return client.post("/api/leads", json=body, headers=token)


def test_saving_an_answer_makes_a_lead_with_its_checks(client, signed_in, token, monkeypatch, tmp_path):
    answer = _answer_from_run(
        monkeypatch, tmp_path, {"review": True, "assay": True}, ["TP53 is up\n\nSee table."],
        review=["VERDICT: HOLDS\nchecked"], assay=["ASSAY: RAN\n3x found"],
    )
    response = _post(client, token, evidence=answer, chat_id="chat1")
    assert response.status_code == 201
    lead = response.get_json()["lead"]
    assert lead["title"] == "TP53 is up"
    assert lead["status"] == "new"
    assert lead["review"] == "holds"
    assert lead["assay"] == "ran"
    assert lead["chat_id"] == "chat1"


def test_saving_the_same_answer_twice_returns_the_first_lead(client, signed_in, token):
    first = _post(client, token, evidence="TP53 is up.", chat_id="chat1")
    second = _post(client, token, evidence="TP53 is up.", chat_id="chat1")
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.get_json()["lead"]["id"] == first.get_json()["lead"]["id"]
    assert len(client.get("/api/leads").get_json()["leads"]) == 1


def test_the_same_answer_from_another_chat_is_its_own_lead(client, signed_in, token):
    _post(client, token, evidence="TP53 is up.", chat_id="chat1")
    other = _post(client, token, evidence="TP53 is up.", chat_id="chat2")
    assert other.status_code == 201


def test_a_lead_can_be_added_by_hand(client, signed_in, token):
    response = _post(client, token, title="Try the KRAS G12C panel", notes="from the journal club")
    lead = response.get_json()["lead"]
    assert lead["title"] == "Try the KRAS G12C panel"
    assert lead["notes"] == "from the journal club"
    assert lead["review"] == "none"
    assert lead["chat_id"] is None


@pytest.mark.parametrize("body", [
    {},
    {"title": "   "},
    {"title": 5},
    {"title": "x" * (leads.MAX_TITLE_CHARS + 1)},
    {"title": "ok", "evidence": ["not", "text"]},
    {"title": "ok", "chat_id": 7},
])
def test_a_bad_lead_is_refused(client, signed_in, token, body):
    response = _post(client, token, **body)
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_a_lead_is_edited_and_the_board_lists_the_newest_edit_first(client, signed_in, token):
    first = _post(client, token, title="first").get_json()["lead"]
    second = _post(client, token, title="second").get_json()["lead"]
    response = client.patch(
        f"/api/leads/{first['id']}",
        json={"status": "testing", "notes": "ordered primers", "next_experiment": "qPCR, 3 reps", "title": "first, renamed"},
        headers=token,
    )
    assert response.status_code == 200
    edited = response.get_json()["lead"]
    assert edited["status"] == "testing"
    assert edited["notes"] == "ordered primers"
    assert edited["next_experiment"] == "qPCR, 3 reps"
    assert edited["title"] == "first, renamed"
    listed = client.get("/api/leads").get_json()["leads"]
    assert [lead["id"] for lead in listed] == [first["id"], second["id"]]


def test_an_edit_cannot_change_what_was_checked(client, signed_in, token):
    lead = _post(client, token, title="a lead", evidence="plain").get_json()["lead"]
    response = client.patch(
        f"/api/leads/{lead['id']}", json={"review": "holds", "assay": "ran", "evidence": "other"}, headers=token,
    )
    edited = response.get_json()["lead"]
    assert edited["review"] == "none"
    assert edited["assay"] == "none"
    assert edited["evidence"] == "plain"


@pytest.mark.parametrize("body", [{"status": "done"}, {"status": None}, {"title": ""}, {"notes": 3}])
def test_a_bad_edit_is_refused_and_changes_nothing(client, signed_in, token, body):
    lead = _post(client, token, title="a lead").get_json()["lead"]
    assert client.patch(f"/api/leads/{lead['id']}", json=body, headers=token).status_code == 400
    unchanged = client.get("/api/leads").get_json()["leads"][0]
    assert unchanged["title"] == "a lead"
    assert unchanged["status"] == "new"
    assert unchanged["notes"] == ""


def test_an_unknown_lead_is_a_404(client, signed_in, token):
    assert client.patch("/api/leads/nope", json={"status": "failed"}, headers=token).status_code == 404
    assert client.delete("/api/leads/nope", headers=token).status_code == 404


def test_a_lead_is_deleted(client, signed_in, token):
    lead = _post(client, token, title="a lead").get_json()["lead"]
    assert client.delete(f"/api/leads/{lead['id']}", headers=token).status_code == 204
    assert client.get("/api/leads").get_json()["leads"] == []


def test_deleting_the_chat_keeps_the_lead(client, signed_in, token):
    chat_id = chat_store.create_chat("source")["id"]
    _post(client, token, title="a lead", chat_id=chat_id)
    chat_store.delete_chat(chat_id)
    assert len(client.get("/api/leads").get_json()["leads"]) == 1


def test_changes_need_the_page_token(client, signed_in):
    assert client.post("/api/leads", json={"title": "x"}).status_code == 403
