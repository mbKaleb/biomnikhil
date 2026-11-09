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
