import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.news_evidence import build_news_evidence_pack, validate_news_evidence_pack

pytestmark = pytest.mark.usefixtures("sqlite_db")

START = datetime.now(UTC) - timedelta(hours=1)
MARKET = "binance:perp:BTCUSDT"
FED_URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260928a.htm"
SEC_URL = "https://www.sec.gov/newsroom/press-releases/2026-101-market-statement"
TITLE = "Federal Reserve issues FOMC statement"
BODY = ("The Federal Open Market Committee issued its official monetary policy statement. "
        "The release discussed the policy decision and its monitoring of inflation and employment.")


def add_article(document_id, source, url, *, title=TITLE, body=BODY, ingested=START,
                published=START - timedelta(minutes=1), full=True):
    metadata = ({"kind": "fomc_statement", "origin": "official_release",
                 "content_quality": "body", "model_use_allowed": True} if full else
                {"content_quality": "summary", "origin": "official_sec_rss",
                 "model_use_allowed": True, "summary_truncated": True})
    with connect() as db:
        db.execute(
            "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
            "ingested_at,market_ids,metadata,content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (document_id, source, url, title, body, published, ingested, json.dumps([MARKET]),
             json.dumps(metadata), hashlib.sha256(document_id.encode()).hexdigest()),
        )
        db.commit()


def test_verified_fomc_publication_is_cited_without_policy_direction():
    init_db()
    add_article("fed-1", "fed_monetary_rss", FED_URL)
    with connect() as db:
        for source in ("fed_monetary_rss", "sec_press_rss"):
            db.execute("INSERT INTO news_source_checks(source,status,checked_at) "
                       "VALUES(?,'ok',?)", (source, START))
        db.commit()
    pack = build_news_evidence_pack(START, MARKET)
    assert pack["coverage_status"] == "partial"
    assert pack["eligible_event_count"] == 1
    assert pack["events"][0]["citation"]["source_url"] == FED_URL
    assert pack["events"][0]["claim_limit"] == (
        "publication_timing_only_no_policy_direction_or_numeric_value")
    assert "decision" not in pack["events"][0]["allowed_claim"]
    validate_news_evidence_pack(pack, cutoff=START, market_id=MARKET)
    future = json.loads(json.dumps(pack))
    future["events"][0]["citation"]["ingested_at"] = (
        START + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="future citation"):
        validate_news_evidence_pack(future, cutoff=START, market_id=MARKET)


def test_monthly_evidence_pack_includes_verified_publications_within_thirty_days():
    init_db()
    add_article("monthly-fed", "fed_monetary_rss",
                "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260910a.htm",
                published=START - timedelta(days=20), ingested=START - timedelta(days=19))
    add_article("too-old-fed", "fed_monetary_rss",
                "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260801a.htm",
                published=START - timedelta(days=31), ingested=START - timedelta(days=30))
    pack = build_news_evidence_pack(START, MARKET)
    assert pack["lookback_hours"] == 24 * 30
    assert [item["citation"]["document_id"] for item in pack["events"]] == ["monthly-fed"]
    validate_news_evidence_pack(pack, cutoff=START, market_id=MARKET)


def test_sec_excerpt_stays_excluded_even_after_jev_and_revisions_are_as_of():
    init_db()
    add_article("sec-1", "sec_press_rss", SEC_URL, full=False)
    before = build_news_evidence_pack(START, MARKET)
    assert before["eligible_event_count"] == 0
    assert before["exclusion_reasons"] == {"sec_excerpt_not_full_release": 1}
    with connect() as db:
        db.execute("INSERT INTO news_classifications(id,document_id,question_version,input_hash,"
                   "model,answers,usage,status,classified_at) "
                   "VALUES(?,?,?,?,?,?,?,'classified_unreviewed',?)",
                   ("jev-sec-1", "sec-1", "news_jev_v1", "a" * 64, "jev-fixture",
                    "{}", "{}", START + timedelta(minutes=5)))
        db.commit()
    assert build_news_evidence_pack(START + timedelta(minutes=4), MARKET)[
        "excluded_audit"][0]["classification_status"] == "pending"
    assert build_news_evidence_pack(START + timedelta(minutes=5), MARKET)[
        "excluded_audit"][0]["classification_status"] == "classified_unreviewed"
    add_article("sec-2", "sec_press_rss", SEC_URL, full=False,
                body=BODY + " This is a revised summary.",
                ingested=START + timedelta(minutes=10))
    old = build_news_evidence_pack(START + timedelta(minutes=5), MARKET)
    new = build_news_evidence_pack(START + timedelta(minutes=10), MARKET)
    assert old["excluded_audit"][0]["event_id"] == new["excluded_audit"][0]["event_id"]
    assert old["excluded_count"] == new["excluded_count"] == 1
    assert new["excluded_audit"][0]["classification_status"] == "pending"
    assert build_news_evidence_pack(START - timedelta(seconds=1), MARKET)["excluded_count"] == 0


def test_cross_source_exact_duplicate_merges_but_similar_story_does_not():
    init_db()
    add_article("fed-1", "fed_monetary_rss", FED_URL)
    add_article("sec-1", "sec_press_rss", SEC_URL, full=False,
                ingested=START + timedelta(minutes=1))
    add_article("sec-2", "sec_press_rss",
                "https://www.sec.gov/newsroom/press-releases/2026-102-other",
                title=TITLE + " update", full=False,
                ingested=START + timedelta(minutes=2))
    pack = build_news_evidence_pack(START + timedelta(minutes=2), MARKET)
    assert pack["eligible_event_count"] == 1
    assert pack["events"][0]["duplicate_document_count"] == 2
    assert pack["excluded_count"] == 1
    assert pack["exclusion_reasons"] == {"sec_excerpt_not_full_release": 1}



def test_candidate_overflow_retains_newest_verified_release():
    init_db()
    metadata = json.dumps({"content_quality": "summary", "origin": "official_sec_rss",
                           "model_use_allowed": True, "summary_truncated": True})
    with connect() as db:
        for number in range(500):
            document_id = f"sec-{number}"
            db.execute(
                "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
                "ingested_at,market_ids,metadata,content_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (document_id, "sec_press_rss",
                 f"https://www.sec.gov/newsroom/press-releases/2026-{number}-market",
                 f"SEC market notice {number}", BODY, START - timedelta(minutes=1), START,
                 json.dumps([MARKET]), metadata, hashlib.sha256(document_id.encode()).hexdigest()),
            )
        db.commit()
    add_article("fed-new", "fed_monetary_rss", FED_URL,
                ingested=START + timedelta(minutes=10))
    cutoff = START + timedelta(minutes=10)
    pack = build_news_evidence_pack(cutoff, MARKET)
    assert pack["query_truncated"] is True
    assert pack["coverage_status"] == "unknown"
    assert pack["eligible_event_count"] == 1
    assert pack["events"][0]["citation"]["document_id"] == "fed-new"
    validate_news_evidence_pack(pack, cutoff=cutoff, market_id=MARKET)


def test_agent_only_receives_verified_fact_and_report_rejects_future_evidence():
    from trade_helper.agent import agent_context, fallback_analysis
    from trade_helper.analysis import build_report
    from trade_helper.report_contract import validate_report

    from .test_analysis import sample_candles

    init_db()
    add_article("fed-1", "fed_monetary_rss", FED_URL)
    add_article("sec-1", "sec_press_rss", SEC_URL, title="SEC issues market statement",
                full=False)
    # Now, not START + 1 h: the quote must be fresh when the report is built, and
    # a slow run can reach this test minutes after the module was imported.
    analysis_time = datetime.now(UTC)
    pack = build_news_evidence_pack(analysis_time, MARKET)
    news = {"status": "available", "cutoff": analysis_time.isoformat(),
            "risk": "none", "items": [], "archive": [],
            "source_status": pack["source_status"], "evidence_pack": pack}
    request = {"market_id": MARKET, "timeframe": "1h", "leverage": 5}
    quote = {"price": "120", "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": analysis_time.isoformat(), "news_risk": "none",
             "news_context": news}
    candles = sample_candles(recent=True)
    context = agent_context(request, candles, quote, None)
    assert len(context["official_announcement_risk"]["verified_release_events"]) == 1
    assert "sec_excerpt_not_full_release" not in str(context)
    assert SEC_URL not in str(context)
    report = build_report(request, candles, quote, [],
                          fallback_analysis(request, candles, quote), news=news)
    validate_report(report)
    report["news_context"]["evidence_pack"]["events"][0]["published_at"] = (
        analysis_time + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="future citation"):
        validate_report(report)
    report["news_context"]["evidence_pack"]["events"][0]["published_at"] = (
        START - timedelta(minutes=1)).isoformat()
    report["news_context"]["evidence_pack"]["excluded_audit"][0][
        "document_versions"][0]["ingested_at"] = (analysis_time + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="future or unapproved"):
        validate_report(report)
