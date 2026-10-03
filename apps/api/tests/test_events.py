from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.events import (
    _record,
    event_snapshot,
    mark_source_offline,
    parse_bea_html,
    parse_bls_ics,
    parse_fed_html,
    save_source,
)

BLS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:bls-cpi-oct
SUMMARY:Consumer Price Index for September 2026
DTSTART;TZID=America/New_York:20261013T083000
END:VEVENT
BEGIN:VEVENT
UID:bls-employment-oct
SUMMARY:Employment Situation for September 2026
DTSTART:20261002T123000Z
END:VEVENT
END:VCALENDAR"""
BEA = """<h1>Release Schedule</h1><div>Year 2026</div><table>
<tr><td>September 30 8:30 AM</td><td>News</td><td>Personal Income and Outlays, August 2026</td></tr>
<tr><td>October 29 8:30 AM</td><td>News</td><td>GDP (Advance Estimate), 3rd Quarter 2026</td></tr>
</table>"""
FED = """<h3>2026 FOMC Meetings</h3><div>January</div><div>27-28</div>
<p>Minutes: (Released February 18, 2026)</p><div>October</div><div>27-28</div>
<h3>2025 FOMC Meetings</h3><div>March</div><div>17-18</div>"""


def test_official_parsers_preserve_timezone_and_unknown_fomc_time():
    bls = parse_bls_ics(BLS)
    assert [event["kind"] for event in bls] == ["cpi", "employment"]
    assert bls[0]["scheduled_at"] == "2026-10-13T12:30:00+00:00"
    assert bls[1]["scheduled_at"] == "2026-10-02T12:30:00+00:00"
    assert _record("bls", "x", "cpi", "CPI", datetime(2026, 10, 2, tzinfo=UTC).date(),
                   None, "javascript:alert(1)")["source_url"].startswith("https://www.bls.gov/")
    bea = parse_bea_html(BEA)
    assert [event["kind"] for event in bea] == ["pce", "gdp"]
    assert bea[0]["scheduled_at"] == "2026-09-30T12:30:00+00:00"
    fed = parse_fed_html(FED, 2026)
    assert [event["scheduled_date"] for event in fed] == ["2026-01-28", "2026-10-28"]
    assert all(event["scheduled_at"] is None and event["time_precision"] == "date" for event in fed)


def test_invalid_calendar_is_not_silently_treated_as_no_events():
    with pytest.raises(ValueError):
        parse_bls_ics("bad input")
    with pytest.raises(ValueError):
        parse_bea_html("bad input")
    with pytest.raises(ValueError):
        parse_fed_html("bad input", 2026)


@pytest.mark.usefixtures("sqlite_db")
def test_event_versions_are_visible_only_after_ingestion():
    init_db()
    first = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    event = parse_bls_ics(BLS)[0]
    assert save_source("bls", [event], first) == 1
    assert save_source("bls", [event], first + timedelta(seconds=1)) == 0
    earlier = event_snapshot(first - timedelta(seconds=1))
    assert earlier["events"] == []
    at_window = event_snapshot(first + timedelta(minutes=10))
    assert at_window["events"][0]["version"] == 1
    assert at_window["risk"] == "high_impact_window"
    revised = event | {"scheduled_at": "2026-10-13T13:00:00+00:00"}
    later = first + timedelta(minutes=20)
    assert save_source("bls", [revised], later) == 1
    assert event_snapshot(first + timedelta(minutes=10))["events"][0]["version"] == 1
    assert event_snapshot(later)["events"][0]["version"] == 2
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM economic_event_versions").fetchone()["n"] == 2


@pytest.mark.usefixtures("sqlite_db")
def test_no_events_and_offline_are_distinct_and_fomc_is_date_only():
    init_db()
    now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    far = _record("bls", "far", "cpi", "Future CPI", datetime(2027, 1, 1, tzinfo=UTC).date(),
                  None, "https://www.bls.gov/schedule/news_release/bls.ics")
    for source in ("bls", "bea", "fed"):
        save_source(source, [far | {"source": source}], now)
    assert event_snapshot(now)["status"] == "no_events"
    mark_source_offline("bea", now + timedelta(seconds=1), "ConnectError")
    assert event_snapshot(now + timedelta(seconds=1))["status"] == "partial"
    for source in ("bls", "fed"):
        mark_source_offline(source, now + timedelta(seconds=2), "ConnectError")
    assert event_snapshot(now + timedelta(seconds=2))["status"] == "offline"
    fomc = parse_fed_html(FED, 2026)[1]
    save_source("fed", [fomc], datetime(2026, 10, 27, 10, 0, tzinfo=UTC))
    result = event_snapshot(datetime(2026, 10, 28, 15, 0, tzinfo=UTC))
    assert result["risk"] == "unknown_major_event"
    assert result["events"][0]["actual_value"] is None


def test_report_waits_for_official_event_window_and_rejects_future_knowledge():
    from trade_helper.agent import fallback_analysis
    from trade_helper.analysis import build_report
    from trade_helper.report_contract import validate_report

    from .test_analysis import sample_candles

    now = datetime.now(UTC)
    cutoff = now.isoformat()
    events = {"status": "available", "risk": "high_impact_window", "cutoff": cutoff,
              "source_status": {"bls": "ok", "bea": "ok", "fed": "ok"},
              "events": [{"id": "evt-1", "version": 1, "source": "bls",
                          "ingested_at": (now - timedelta(minutes=5)).isoformat(),
                          "scheduled_at": (now + timedelta(minutes=10)).isoformat(),
                          "actual_value": None}]}
    quote = {"price": "120", "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": cutoff, "event_risk": events["risk"], "events_status": events["status"]}
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    candles = sample_candles(recent=True)
    decision = fallback_analysis(request, candles, quote)
    report = build_report(request, candles, quote, [], decision, events=events)
    assert report["strategies"][0]["type"] == "wait"
    assert report["events_status"] == "available"
    validate_report(report)
    report["event_context"]["events"][0]["ingested_at"] = (now + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="learned after"):
        validate_report(report)


def test_bls_own_eastern_zone_name_is_read_as_new_york():
    calendar = BLS.replace("DTSTART;TZID=America/New_York:", "DTSTART;TZID=US-Eastern:")
    assert calendar != BLS
    assert parse_bls_ics(calendar) == parse_bls_ics(BLS)

