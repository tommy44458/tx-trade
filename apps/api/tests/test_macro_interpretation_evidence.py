import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.events import _record, save_source
from trade_helper.macro_actuals import BLS_API, save_actuals
from trade_helper.macro_interpretation_evidence import (
    build_macro_interpretation_evidence,
    load_macro_interpretation_evidence,
)
from trade_helper.news import save_news

AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
OBSERVED = AT - timedelta(days=3)
FOMC_PUBLISHED = datetime(2026, 9, 16, 18, tzinfo=UTC)
FOMC_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
FOMC_BODY = (
    "The Committee decided to maintain the target range for the federal funds rate "
    "at 3.5 to 3.75 percent."
)


def actual_payload(value="4.1", *, period="2026-08", previous="4.0", evidence="Published series value"):
    return {"source": "bls", "kind": "employment", "metric": "unemployment_rate",
            "label": "失業率", "period": period, "value": value, "unit": "percent",
            "previous_value": previous, "published_at": None, "source_url": BLS_API,
            "method": "published_bls_v1_series", "evidence": evidence}


def actual_snapshot(value="4.1", *, observed=OBSERVED, version=1, **kwargs):
    payload = actual_payload(value, **kwargs)
    return payload | {"id": "bls:unemployment_rate:" + payload["period"], "version": version,
                      "ingested_at": observed.isoformat(), "content_hash": hashlib.sha256(
                          json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                      ).hexdigest()}


def fomc_news(*, published=FOMC_PUBLISHED, url=FOMC_URL, body=FOMC_BODY, origin="official_release"):
    return {"source": "fed_monetary_rss", "source_url": url,
            "title": "Federal Reserve issues FOMC statement", "body": body,
            "published_at": published.isoformat(), "market_ids": [],
            "metadata": {"kind": "fomc_statement", "origin": origin,
                         "content_quality": "body", "model_use_allowed": True}}


def test_fingerprint_ignores_cutoff_poll_times_same_value_versions_and_calendar_movement():
    events = {"official_actuals": [actual_snapshot()], "events": [],
              "source_status": {"bls": "ok"}, "actual_source_status": {"bls": "ok"}}
    first = build_macro_interpretation_evidence(AT, events)
    noise = copy.deepcopy(events)
    noise["official_actuals"] = [actual_snapshot("4.100", observed=AT, version=2,
                                                previous="4.00", evidence="Same value, fetched again")]
    noise["events"] = [{"source": "fed", "kind": "fomc", "scheduled_date": "2026-10-30",
                        "ingested_at": AT.isoformat(), "version": 17}]
    noise["source_status"] = {"bls": "offline"}
    noise["cutoff"] = (AT + timedelta(hours=2)).isoformat()
    noise["checked_at"] = (AT + timedelta(hours=2)).isoformat()
    second = build_macro_interpretation_evidence(AT + timedelta(hours=2), noise)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["as_of"] != second["as_of"]
    assert first["evidence"][0]["version"] == 1
    assert second["evidence"][0]["version"] == 2
    assert second["evidence"][0]["value"] == "4.1"
    assert "stance" not in second


def test_new_actual_numeric_revision_previous_and_period_change_fingerprint():
    baseline = build_macro_interpretation_evidence(AT, {"official_actuals": [actual_snapshot()]})
    for update in [actual_snapshot("4.2", version=2), actual_snapshot(previous="3.9"),
                   actual_snapshot(period="2026-09")]:
        changed = build_macro_interpretation_evidence(AT, {"official_actuals": [update]})
        assert changed["fingerprint"] != baseline["fingerprint"]
    two = build_macro_interpretation_evidence(
        AT, {"official_actuals": [actual_snapshot(), actual_snapshot(period="2026-09")]}
    )
    assert two["evidence_count"] == 2
    assert two["fingerprint"] != baseline["fingerprint"]


def test_future_publication_observation_naive_time_and_calendar_only_are_excluded():
    future = actual_snapshot(observed=AT + timedelta(seconds=1))
    snapshot = build_macro_interpretation_evidence(
        AT, {"official_actuals": [future], "events": [{"source": "fed", "kind": "fomc",
                                                        "actual_value": "3.5–3.75%"}]}
    )
    assert snapshot["evidence_count"] == 0
    assert snapshot["coverage"]["excluded_count"] == 1
    with pytest.raises(ValueError, match="timezone"):
        build_macro_interpretation_evidence(AT.replace(tzinfo=None))
    unverified = {"status": "checked", "usable_for_strategy": False,
                  "unverified_forecast_samples": [{"forecast": "0.2"}]}
    assert build_macro_interpretation_evidence(AT, consensus=unverified)["evidence_count"] == 0


def consensus_record(**updates):
    return {"id": "consensus-cpi-202609", "source": "verified-fixture", "metric": "cpi_mom",
            "period": "2026-09", "unit": "percent", "actual": "0.3", "forecast": "0.2",
            "previous": "0.1", "published_at": (AT - timedelta(hours=1)).isoformat(),
            "observed_at": AT.isoformat(), "source_url": "https://example.test/cpi",
            "usable_for_strategy": True} | updates


def test_verified_consensus_values_and_usability_participate_without_request_noise():
    none = build_macro_interpretation_evidence(AT)
    pack = {"status": "available", "items": [consensus_record()]}
    available = build_macro_interpretation_evidence(AT, consensus=pack)
    assert available["coverage"]["consensus_status"] == "available"
    assert available["evidence"][0]["forecast"] == "0.2"
    assert available["fingerprint"] != none["fingerprint"]
    noisy = {"status": "offline", "checked_at": (AT + timedelta(hours=1)).isoformat(),
             "items": [consensus_record(observed_at=(AT + timedelta(minutes=1)).isoformat())]}
    assert build_macro_interpretation_evidence(AT + timedelta(hours=1), consensus=noisy)["fingerprint"] == available["fingerprint"]
    revised = {"items": [consensus_record(forecast="0.4")]}
    assert build_macro_interpretation_evidence(AT, consensus=revised)["fingerprint"] != available["fingerprint"]
    blocked = {"items": [consensus_record(usable_for_strategy=False)]}
    assert build_macro_interpretation_evidence(AT, consensus=blocked)["fingerprint"] == none["fingerprint"]
    future = {"items": [consensus_record(published_at=(AT + timedelta(seconds=1)).isoformat())]}
    assert build_macro_interpretation_evidence(AT, consensus=future)["evidence_count"] == 0


@pytest.mark.usefixtures("sqlite_db")
def test_loader_keeps_fixed_actual_period_scope_after_daily_windows_expire():
    init_db()
    for period in ["2026-05", "2026-06", "2026-07", "2026-08"]:
        save_actuals("bls", [actual_payload(period=period)], OBSERVED)
    calendar = _record("fed", "2026:october", "fomc", "FOMC meeting", AT.date(), None,
                       "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    save_source("fed", [calendar], OBSERVED)
    first = load_macro_interpretation_evidence(AT)
    assert [item["period"] for item in first["evidence"]] == ["2026-06", "2026-07", "2026-08"]
    much_later = load_macro_interpretation_evidence(AT + timedelta(days=90))
    assert first["fingerprint"] == much_later["fingerprint"]
    assert all(not item["within_last_month"] for item in much_later["coverage"]["freshness"])
    assert not much_later["coverage"]["scope"]["calendar_window_eviction"]
    # A metadata-only version does not force another model interpretation.
    save_actuals("bls", [actual_payload(evidence="Same numeric statistic re-fetched")], AT)
    noise = load_macro_interpretation_evidence(AT)
    assert first["fingerprint"] == noise["fingerprint"]
    save_actuals("bls", [actual_payload("4.2")], AT + timedelta(minutes=1))
    assert load_macro_interpretation_evidence(AT)["fingerprint"] == first["fingerprint"]
    assert load_macro_interpretation_evidence(AT + timedelta(minutes=1))["fingerprint"] != first["fingerprint"]


@pytest.mark.usefixtures("sqlite_db")
def test_loader_uses_true_fomc_publication_without_calendar_and_no_32_day_eviction():
    init_db()
    save_news([fomc_news()], FOMC_PUBLISHED + timedelta(minutes=1))
    assert load_macro_interpretation_evidence(FOMC_PUBLISHED)["evidence_count"] == 0
    actual = load_macro_interpretation_evidence(AT)
    assert actual["evidence_count"] == 1
    fact = actual["evidence"][0]
    assert fact["type"] == "fomc_target_range"
    assert fact["lower_pct"] == "3.5" and fact["upper_pct"] == "3.75"
    assert fact["source_url"] == FOMC_URL
    assert fact["document_id"].startswith("news_")
    assert load_macro_interpretation_evidence(AT + timedelta(days=90))["fingerprint"] == actual["fingerprint"]


@pytest.mark.usefixtures("sqlite_db")
def test_loader_rejects_headlines_and_preserves_verified_publication_without_numeric_result():
    init_db()
    save_news([fomc_news(origin="official_rss")], FOMC_PUBLISHED + timedelta(minutes=1))
    assert load_macro_interpretation_evidence(AT)["evidence_count"] == 0
    save_news([fomc_news(body="The official FOMC statement discussed economic conditions.")],
              FOMC_PUBLISHED + timedelta(minutes=2))
    snapshot = load_macro_interpretation_evidence(AT)
    assert snapshot["evidence_count"] == 1
    assert snapshot["evidence"][0]["type"] == "official_publication"
    assert "lower_pct" not in snapshot["evidence"][0]


@pytest.mark.usefixtures("sqlite_db")
def test_unusable_provider_cache_and_future_result_completion_do_not_change_identity():
    init_db()
    first = load_macro_interpretation_evidence(AT)
    with connect() as db:
        db.execute("INSERT INTO consensus_provider_checks(provider,reserved_at,next_allowed_at,result) "
                   "VALUES(?,?,?,?)", ("jblanked", AT, AT + timedelta(days=1), json.dumps({
                       "status": "unavailable", "checked_at": AT.isoformat(), "http_status": 401,
                       "usable_for_strategy": False, "credits": 0})))
    assert load_macro_interpretation_evidence(AT)["fingerprint"] == first["fingerprint"]
    with connect() as db:
        db.execute("UPDATE consensus_provider_checks SET result=?", (json.dumps({
            "status": "available", "usable_for_strategy": True,
            "checked_at": (AT + timedelta(minutes=1)).isoformat(), "items": [consensus_record()]
        }),))
    assert load_macro_interpretation_evidence(AT)["fingerprint"] == first["fingerprint"]
    assert load_macro_interpretation_evidence(AT + timedelta(minutes=1))["fingerprint"] != first["fingerprint"]


@pytest.mark.usefixtures("sqlite_db")
@pytest.mark.parametrize("with_numeric_result", [True, False])
def test_fomc_statement_whitespace_only_refetch_preserves_cache_identity(with_numeric_result):
    init_db()
    body = (FOMC_BODY + " Inflation remains elevated and the Committee is attentive to risks."
            if with_numeric_result else
            "Economic activity continued to expand. The Committee is attentive to inflation risks.")
    save_news([fomc_news(body=body)], FOMC_PUBLISHED + timedelta(minutes=1))
    first = load_macro_interpretation_evidence(AT)
    # Paragraph, tab and multiple-space changes do not change policy wording.
    spaced = "\n\n".join(body.split(" ")) + "\t\n"
    save_news([fomc_news(body=spaced)], FOMC_PUBLISHED + timedelta(minutes=2))
    second = load_macro_interpretation_evidence(AT)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["evidence"][0]["statement_text"] == body
    assert second["evidence"][0]["statement_text"] == body
    assert second["evidence"][0]["type"] == (
        "fomc_target_range" if with_numeric_result else "official_publication")


@pytest.mark.usefixtures("sqlite_db")
@pytest.mark.parametrize("with_numeric_result", [True, False])
def test_fomc_policy_text_revision_invalidates_cache_even_when_rate_unchanged(with_numeric_result):
    init_db()
    body = (FOMC_BODY if with_numeric_result else "Economic activity continued to expand.")
    body += " Inflation remains elevated."
    save_news([fomc_news(body=body)], FOMC_PUBLISHED + timedelta(minutes=1))
    first = load_macro_interpretation_evidence(AT)
    revised = body.replace("Inflation remains elevated.", "Inflation has eased but remains elevated.")
    save_news([fomc_news(body=revised)], FOMC_PUBLISHED + timedelta(minutes=2))
    second = load_macro_interpretation_evidence(AT)
    assert first["fingerprint"] != second["fingerprint"]
    assert second["evidence"][0]["statement_text"] == revised
    if with_numeric_result:
        assert first["evidence"][0]["lower_pct"] == second["evidence"][0]["lower_pct"]
        assert first["evidence"][0]["upper_pct"] == second["evidence"][0]["upper_pct"]
    else:
        assert second["evidence"][0]["claim_limit"] == (
            "publication_timing_only_no_policy_direction_or_numeric_value")
        assert "lower_pct" not in second["evidence"][0]
