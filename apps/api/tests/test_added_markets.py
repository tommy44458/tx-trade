import pytest
from fastapi.testclient import TestClient

from trade_helper.api import app
from trade_helper.bingx import normalize_positions
from trade_helper.market import MARKETS, symbol_for
from trade_helper.models import AnalysisRequest, PositionInput

NEW_SYMBOLS = ("SOL", "ADA", "SUI")


@pytest.mark.parametrize("base", NEW_SYMBOLS)
def test_new_market_is_selectable_and_bingx_positions_map_to_it(base):
    market_id = f"binance:perp:{base}USDT"
    assert any(m["id"] == market_id and m["symbol"] == f"{base}/USDT" for m in MARKETS)
    assert symbol_for(market_id) == f"{base}USDT"
    assert AnalysisRequest(market_id=market_id).market_id == market_id
    assert PositionInput(market_id=market_id, entry_price="1", quantity="2").market_id == market_id
    perpetual, skipped = normalize_positions("perpetual", [{
        "positionId": f"p-{base}", "symbol": f"{base}-USDT", "positionAmt": "2",
        "positionSide": "LONG", "avgPrice": "1", "leverage": "5", "isolated": True,
    }])
    assert skipped == 0
    assert perpetual[0]["market_id"] == market_id
    standard, skipped = normalize_positions("standard", [{
        "symbol": f"{base}USDT", "positionAmt": "2", "positionSide": "LONG",
        "entryPrice": "1", "leverage": "5", "isolated": True, "time": 1700000000000,
    }])
    assert skipped == 0
    assert standard[0]["market_id"] == market_id


def test_market_endpoint_lists_five_pairs():
    response = TestClient(app).get("/api/v1/markets")
    assert response.status_code == 200
    assert {m["symbol"] for m in response.json()} == {
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT", "SUI/USDT"
    }


@pytest.mark.usefixtures("pg_schema")
@pytest.mark.parametrize("base", NEW_SYMBOLS)
def test_new_market_accepts_position_and_analysis(base, monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "added-market-test")
    market_id = f"binance:perp:{base}USDT"
    with TestClient(app) as client:
        position = client.post("/api/v1/positions", json={
            "market_id": market_id, "entry_price": "1", "quantity": "2",
        })
        assert position.status_code == 201, position.text
        analysis = client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": market_id, "timeframe": "4h",
            "position_ids": [position.json()["id"]],
        }, headers={"Idempotency-Key": f"added-market-{base}"})
        assert analysis.status_code == 202, analysis.text


@pytest.mark.usefixtures("pg_schema")
def test_existing_global_fed_evidence_is_available_to_new_markets():
    from datetime import UTC, datetime, timedelta

    from trade_helper.db import connect, init_db
    from trade_helper.news import news_snapshot
    from trade_helper.news_evidence import build_news_evidence_pack

    init_db()
    cutoff = datetime.now(UTC)
    published = cutoff - timedelta(hours=1)
    with connect() as db:
        db.execute(
            "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
            "ingested_at,market_ids,metadata,content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("old-fed", "fed_monetary_rss",
             "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260928a.htm",
             "Federal Reserve issues FOMC statement", "Official policy statement",
             published, published, '["binance:perp:BTCUSDT","binance:perp:ETHUSDT"]',
             '{"kind":"fomc_statement","origin":"official_release","content_quality":"body","model_use_allowed":true}', "old-hash"),
        )
        db.commit()
    for base in NEW_SYMBOLS:
        market_id = f"binance:perp:{base}USDT"
        assert news_snapshot(cutoff, market_id)["items"][0]["id"] == "old-fed"
        assert build_news_evidence_pack(cutoff, market_id)["events"][0][
            "citation"]["document_id"] == "old-fed"
