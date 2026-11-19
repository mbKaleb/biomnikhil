"""The Spending tab's figures and its handling of Cost Explorer failures."""
from datetime import date

import pytest
from botocore.exceptions import ClientError

from app.config import Config
from app.services import spending


def _day(day, services):
    groups = []
    for name, amount in services.items():
        cost = {"Amount": str(amount), "Unit": "USD"}
        groups.append({"Keys": [name], "Metrics": {spending.METRIC: cost}})
    return {"TimePeriod": {"Start": day}, "Groups": groups}


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def get_cost_and_usage(self, **request):
        self.calls += 1
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "no"}}, "GetCostAndUsage")
        if "NextPageToken" not in request:
            return {"ResultsByTime": [_day("2026-11-04", {"Bedrock": 2})], "NextPageToken": "more"}
        return {"ResultsByTime": [_day("2026-11-05", {"Bedrock": 3})]}

    def get_cost_forecast(self, **request):
        return {"Total": {"Amount": "10"}}


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SPENDING_CACHE_FILE", tmp_path / "spending-cache.json")
    monkeypatch.setattr(spending, "_cached", None)
    monkeypatch.setattr(spending, "_cached_at", 0.0)


def test_summary_splits_months_and_avoids_double_counting_today():
    results = [
        _day("2026-10-03", {"S3": 4}),
        _day("2026-10-20", {"S3": 6}),
        _day("2026-11-01", {"Bedrock": 5, "S3": 1}),
        _day("2026-11-05", {"Bedrock": 2}),
    ]
    summary = spending.summarize(results, 10.0, date(2026, 11, 5))
    assert summary["month_to_date"] == 8
    assert summary["previous_month"] == 10
    assert summary["previous_by_this_day"] == 4
    assert summary["projected_month"] == 16
    assert summary["services"][0] == {"name": "Bedrock", "amount": 7}


def test_small_services_fold_into_other():
    services = {}
    for i in range(spending.TOP_SERVICES + 2):
        services[f"service{i}"] = i + 1
    summary = spending.summarize([_day("2026-11-01", services)], None, date(2026, 11, 5))
    assert len(summary["services"]) == spending.TOP_SERVICES + 1
    assert summary["services"][-1] == {"name": "Other", "amount": 3}
    assert summary["projected_month"] is None


def test_route_reads_all_pages_and_caches(monkeypatch, client, signed_in):
    fake = FakeClient()
    monkeypatch.setattr(spending, "_client", lambda credentials: fake)
    response = client.get("/api/spending")
    assert response.status_code == 200
    assert fake.calls == 2
    client.get("/api/spending")
    client.get("/api/spending?refresh=1")  # too soon to pay for another fetch
    assert fake.calls == 2


def test_missing_permission_is_reported_as_such(monkeypatch, client, signed_in):
    fake = FakeClient(error="AccessDeniedException")
    monkeypatch.setattr(spending, "_client", lambda credentials: fake)
    response = client.get("/api/spending")
    assert response.status_code == 403
    assert response.get_json()["code"] == "permission"


def test_spending_needs_sign_in(client):
    assert client.get("/api/spending").status_code in (401, 302)


# ---- spending by chat and by user ------------------------------------------

from app.services import biomni_runner, chat_store  # noqa: E402


def _stages(main_in, main_out, assay_in=0, assay_out=0):
    stages = {"main": {"calls": 2, "input_tokens": main_in, "output_tokens": main_out, "cached_tokens": 0}}
    if assay_in or assay_out:
        stages["assay"] = {"calls": 1, "input_tokens": assay_in, "output_tokens": assay_out, "cached_tokens": 0}
    return stages


def test_chat_usage_is_saved_per_chat_with_who_ran_it():
    first = chat_store.create_chat("first")["id"]
    second = chat_store.create_chat("second")["id"]
    chat_store.add_run_usage(first, "t1", "ana@lab.org", "model-a", _stages(100, 10))
    chat_store.add_run_usage(first, "t2", "ben@lab.org", "model-b", _stages(200, 20))
    chat_store.add_run_usage(second, "t3", "ana@lab.org", "model-a", _stages(50, 5))

    runs = chat_store.chat_run_usage(first)

    assert [run["user"] for run in runs] == ["ana@lab.org", "ben@lab.org"]
    assert runs[0]["stages"]["main"]["input_tokens"] == 100
    assert len(chat_store.chat_run_usage(second)) == 1


def test_usage_for_an_unknown_chat_is_dropped():
    chat_store.add_run_usage("nosuchchat", "t1", "ana@lab.org", None, _stages(1, 1))
    assert chat_store.chat_run_usage("nosuchchat") == []


def test_deleting_a_chat_deletes_its_usage():
    chat_id = chat_store.create_chat("gone")["id"]
    chat_store.add_run_usage(chat_id, "t1", "ana@lab.org", None, _stages(1, 1))
    chat_store.delete_chat(chat_id)
    assert chat_store.chat_run_usage(chat_id) == []


def test_run_shares_follow_the_runs_cost():
    runs = [
        {"task_id": "a", "user": "ana", "model": None, "at": 1.0, "stages": _stages(100, 0)},
        {"task_id": "b", "user": "ben", "model": None, "at": 2.0, "stages": _stages(100, 20)},
    ]
    # Output counts 5x: ana weighs 100, ben weighs 200.
    rows = biomni_runner.run_breakdown(runs)
    assert rows[0]["share"] == pytest.approx(1 / 3)
    assert rows[1]["share"] == pytest.approx(2 / 3)


def test_stages_add_up_across_runs():
    runs = [
        {"stages": _stages(100, 10, 30, 3)},
        {"stages": _stages(50, 5)},
    ]
    totals = biomni_runner.sum_stages(runs)
    assert totals["main"]["input_tokens"] == 150
    assert totals["main"]["calls"] == 4
    assert totals["assay"]["output_tokens"] == 3


def test_users_are_ranked_by_share_of_cost():
    runs = [
        {"task_id": "a", "user": "ana", "model": None, "at": 1.0, "stages": _stages(100, 0)},
        {"task_id": "b", "user": "ben", "model": None, "at": 2.0, "stages": _stages(100, 40)},
        {"task_id": "c", "user": "ana", "model": None, "at": 3.0, "stages": _stages(100, 0)},
    ]
    rows = biomni_runner.user_breakdown(runs)
    assert [row["user"] for row in rows] == ["ben", "ana"]
    assert rows[1]["runs"] == 2
    assert rows[1]["calls"] == 4
    # Output counts 5x: ben weighs 300, ana 100 + 100, of 500 in all.
    assert rows[0]["share"] == pytest.approx(0.6)
    assert rows[1]["share"] == pytest.approx(0.4)


def test_stages_route_returns_only_the_asked_chat(client, signed_in):
    mine = chat_store.create_chat("mine")["id"]
    other = chat_store.create_chat("other")["id"]
    chat_store.add_run_usage(mine, "t1", "tester@example.org", "m", _stages(100, 10))
    chat_store.add_run_usage(other, "t2", "someone@else.org", "m", _stages(900, 90))

    data = client.get(f"/api/spending/stages?chat={mine}").get_json()

    assert [run["user"] for run in data["runs"]] == ["tester@example.org"]
    assert data["stages"][0]["input_tokens"] == 100
    assert data["stages"][0]["share"] == pytest.approx(1.0)


def test_stages_route_without_a_chat_is_empty(client, signed_in):
    data = client.get("/api/spending/stages").get_json()
    assert data == {"stages": [], "runs": []}
