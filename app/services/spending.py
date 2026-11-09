"""AWS spending for the Spending tab, from Cost Explorer.

Figures are account-wide and exclude credits and refunds, so they show what
was used rather than what will be billed. Cost Explorer charges per request
(about $0.01), so results are cached and shared between users: they all sign
with the same role, and the data only updates a few times a day anyway.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, timedelta

from ..config import Config

logger = logging.getLogger(__name__)

CACHE_SECONDS = 3600
# How soon the Refresh button can trigger another paid fetch.
MIN_REFRESH_SECONDS = 60
DAILY_CHART_DAYS = 30
TOP_SERVICES = 8
# Cost Explorer's only endpoint, whatever region the app itself uses.
CE_REGION = "us-east-1"
METRIC = "UnblendedCost"
USAGE_ONLY = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}

_lock = threading.Lock()
_cached: dict | None = None
_cached_at = 0.0


def _load_disk_cache() -> None:
    """Pick up the figures the last run saved, so a restart doesn't cost a fetch."""
    global _cached, _cached_at
    try:
        saved = json.loads(Config.SPENDING_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return
    fetched_at = saved.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return
    _cached = saved
    _cached_at = fetched_at


def _save_disk_cache(figures: dict) -> None:
    try:
        Config.SPENDING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = Config.SPENDING_CACHE_FILE.with_name(f".{Config.SPENDING_CACHE_FILE.name}.tmp")
        temporary.write_text(json.dumps(figures), encoding="utf-8")
        os.replace(temporary, Config.SPENDING_CACHE_FILE)
    except OSError as exc:
        logger.warning("Couldn't save the spending cache: %s", exc)


class SpendingUnavailable(Exception):
    """Cost Explorer refused or failed. `code` is "permission" or "aws"."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _client(credentials):
    import boto3

    frozen = credentials.get_frozen_credentials()
    return boto3.client(
        "ce",
        region_name=CE_REGION,
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
    )


def _first_of_previous_month(day: date) -> date:
    last_of_previous = day.replace(day=1) - timedelta(days=1)
    return last_of_previous.replace(day=1)


def _first_of_next_month(day: date) -> date:
    # The 28th plus 4 days always lands in the next month.
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _fetch_results(client, start: date, end: date) -> list[dict]:
    """Daily costs per service, across every page of the response."""
    results = []
    request = {
        "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
        "Granularity": "DAILY",
        "Metrics": [METRIC],
        "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
        "Filter": USAGE_ONLY,
    }
    while True:
        page = client.get_cost_and_usage(**request)
        results.extend(page["ResultsByTime"])
        token = page.get("NextPageToken")
        if not token:
            return results
        request["NextPageToken"] = token


def _fetch_forecast(client, today: date) -> float | None:
    """Forecast cost from today to the end of the month, or None if there is
    none: new accounts don't have enough history, and a page without a
    forecast is still useful."""
    try:
        response = client.get_cost_forecast(
            TimePeriod={
                "Start": today.isoformat(),
                "End": _first_of_next_month(today).isoformat(),
            },
            Metric="UNBLENDED_COST",
            Granularity="MONTHLY",
            Filter=USAGE_ONLY,
        )
        return float(response["Total"]["Amount"])
    except Exception:  # noqa: BLE001
        logger.warning("no AWS cost forecast", exc_info=True)
        return None


def summarize(results: list[dict], forecast_remaining: float | None, today: date) -> dict:
    """The tab's figures, from Cost Explorer's daily per-service results."""
    month_start = today.replace(day=1)
    previous_start = _first_of_previous_month(today)
    chart_start = today - timedelta(days=DAILY_CHART_DAYS - 1)

    currency = "USD"
    month_to_date = 0.0
    before_today = 0.0
    previous_month = 0.0
    previous_by_this_day = 0.0
    daily = []
    by_service: dict[str, float] = {}

    for result in results:
        day = date.fromisoformat(result["TimePeriod"]["Start"])
        day_total = 0.0
        for group in result.get("Groups", []):
            cost = group["Metrics"][METRIC]
            currency = cost.get("Unit", currency)
            amount = float(cost["Amount"])
            day_total += amount
            if day >= month_start:
                service = group["Keys"][0]
                by_service[service] = by_service.get(service, 0.0) + amount

        if day >= month_start:
            month_to_date += day_total
            if day < today:
                before_today += day_total
        elif day >= previous_start:
            previous_month += day_total
            if day.day <= today.day:
                previous_by_this_day += day_total
        if day >= chart_start:
            daily.append({"date": day.isoformat(), "amount": day_total})

    ranked = sorted(by_service.items(), key=lambda item: item[1], reverse=True)
    services = []
    for name, amount in ranked[:TOP_SERVICES]:
        services.append({"name": name, "amount": amount})
    other = sum(amount for _name, amount in ranked[TOP_SERVICES:])
    if other > 0:
        services.append({"name": "Other", "amount": other})

    # The forecast starts today, which month_to_date already partly counts.
    projected = None
    if forecast_remaining is not None:
        projected = before_today + forecast_remaining

    return {
        "currency": currency,
        "month_to_date": month_to_date,
        "projected_month": projected,
        "previous_month": previous_month,
        "previous_by_this_day": previous_by_this_day,
        "daily": daily,
        "services": services,
    }


def get_spending(credentials, refresh: bool = False) -> dict:
    """The cached figures, fetched again once stale or on an allowed refresh."""
    global _cached, _cached_at

    from botocore.exceptions import BotoCoreError, ClientError

    with _lock:
        if _cached is None:
            _load_disk_cache()
        age = time.time() - _cached_at
        if _cached is not None:
            fresh = age < CACHE_SECONDS
            if refresh:
                fresh = age < MIN_REFRESH_SECONDS
            if fresh:
                return _cached

        # Cost Explorer's day boundaries are UTC.
        today = time.gmtime()
        today = date(today.tm_year, today.tm_mon, today.tm_mday)
        start = min(_first_of_previous_month(today), today - timedelta(days=DAILY_CHART_DAYS - 1))
        end = today + timedelta(days=1)  # exclusive
        try:
            client = _client(credentials)
            results = _fetch_results(client, start, end)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("AccessDeniedException", "UnauthorizedOperation"):
                # AWS's message says what refused it: the role, an org policy, or Cost Explorer being off.
                logger.warning("Cost Explorer refused: %s", exc)
                raise SpendingUnavailable(
                    "permission",
                    "Your AWS role can't read Cost Explorer yet.",
                ) from exc
            raise SpendingUnavailable("aws", f"Cost Explorer failed: {exc}") from exc
        except BotoCoreError as exc:
            raise SpendingUnavailable("aws", f"Couldn't reach Cost Explorer: {exc}") from exc

        forecast = _fetch_forecast(client, today)
        _cached = summarize(results, forecast, today)
        _cached["fetched_at"] = time.time()
        _cached_at = _cached["fetched_at"]
        _save_disk_cache(_cached)
        return _cached
