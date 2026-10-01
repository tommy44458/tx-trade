from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_helper.agent import agent_context
from trade_helper.analysis import position_review
from trade_helper.api import app
from trade_helper.db import connect
from trade_helper.position_advice import build_position_options

pytestmark = pytest.mark.usefixtures("pg_schema")
MARKET = "binance:perp:BTCUSDT"
BASE = {"market_id": MARKET, "side": "long", "leverage": 5, "margin_mode": "isolated",
        "entry_price": "100", "quantity": "2", "stop_loss": "90", "take_profit": "130"}


def test_local_position_details_are_versioned_and_notes_do_not_enter_agent():
    entered = (datetime.now(UTC) - timedelta(hours=6)).replace(microsecond=0)
    with TestClient(app) as client:
        response = client.post("/api/v1/positions", json=BASE | {
            "entry_time": entered.isoformat(), "exchange_liquidation_price": "80",
            "notes": "  private trading plan  "})
        assert response.status_code == 201
        position = response.json()
        assert position["entry_time"] == entered.isoformat()
        assert position["exchange_liquidation_price"] == "80"
        assert position["notes"] == "private trading plan"

        job = client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": MARKET, "position_ids": [position["id"]]},
            headers={"Idempotency-Key": str(uuid4())}).json()
        with connect() as db:
            import json

            snapshot = json.loads(db.execute("SELECT positions_json FROM analyses WHERE id=?",
                                             (job["id"],)).fetchone()["positions_json"])[0]
        assert snapshot["notes"] == "private trading plan"
        context = agent_context({"market_id": MARKET, "timeframe": "1h"},
                                [{"close_time": entered.isoformat()}],
                                {"observed_at": datetime.now(UTC).isoformat()}, None,
                                [snapshot])
        assert "notes" not in context["selected_positions"][0]
        assert context["selected_positions"][0]["exchange_liquidation_price"] == "80"

        review = position_review(snapshot, {"price": "110", "mark_price": "110"})
        assert review["distance_to_exchange_liquidation_pct"] == "27.27"
        assert review["entry_time"] == entered.isoformat()
        assert review["user_note"] == "private trading plan"
        warning = position_review(snapshot, {"price": "75", "mark_price": "75"})
        assert any("核對實際部位" in message for message in warning["messages"])
        options = build_position_options([snapshot],
                                         {"price": "75", "mark_price": "75", "tick_size": "0.1",
                                          "observed_at": datetime.now(UTC).isoformat()},
                                         [], "bullish", Decimal(2))
        assert options["positions"][0]["candidates"][0]["kind"] == "verify_execution"

        updated = client.patch(f"/api/v1/positions/{position['id']}", json=BASE | {
            "expected_version": 1, "entry_time": entered.isoformat(),
            "exchange_liquidation_price": "79", "notes": "revised"})
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert updated.json()["notes"] == "revised"
        assert client.get(f"/api/v1/analyses/{job['id']}").json()["freshness"] == "stale"


@pytest.mark.parametrize("extra", [
    {"entry_time": "2026-09-28T12:00:00"},
    {"entry_time": "2099-01-01T00:00:00+00:00"},
    {"exchange_liquidation_price": "-1"},
    {"notes": "x" * 501},
])
def test_local_position_details_reject_invalid_inputs(extra):
    with TestClient(app) as client:
        assert client.post("/api/v1/positions", json=BASE | extra).status_code == 422


def test_optional_equity_exposure_is_recomputed_by_report_contract():
    from pydantic import ValidationError

    from trade_helper.agent import fallback_analysis
    from trade_helper.analysis import build_report
    from trade_helper.models import AnalysisRequest
    from trade_helper.report_contract import validate_report

    from .test_analysis import sample_candles

    for bad in ("0", "-100", "NaN", "Infinity", "1000000000001"):
        with pytest.raises(ValidationError):
            AnalysisRequest.model_validate({"kind": "positions", "market_id": MARKET,
                                            "position_ids": ["p1"], "account_equity_usdt": bad})
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate({"kind": "market", "market_id": MARKET,
                                        "account_equity_usdt": "1000"})

    request = AnalysisRequest.model_validate({
        "kind": "positions", "market_id": MARKET, "timeframe": "1h",
        "position_ids": ["p1"], "account_equity_usdt": "1000"}).model_dump(mode="json")
    position = {"id": "p1", "version": 1, **BASE, "previous_stop_loss": None}
    quote = {"price": "110", "mark_price": "110", "tick_size": "0.1",
             "observed_at": datetime.now(UTC).isoformat(), "snapshot_hash": "a" * 64,
             "event_risk": "none", "events_status": "not_integrated"}
    candles = sample_candles(recent=True)
    decision = fallback_analysis(request, candles, quote, positions=[position])
    report = build_report(request, candles, quote, [position], decision)
    review = report["position_reviews"][0]
    assert report["account_equity_usdt"] == "1000"
    assert review["stop_price_exposure_usdt"] == "40"
    assert review["stop_price_exposure_pct_of_equity"] == "4.00"
    validate_report(report)
    report["position_reviews"][0]["stop_price_exposure_pct_of_equity"] = "1.00"
    with pytest.raises(ValueError, match="Position diagnostics differ"):
        validate_report(report)
