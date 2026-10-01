import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from trade_helper.api import app
from trade_helper.bingx import BingXError, normalize_positions
from trade_helper.db import connect
from trade_helper.risk import costed_risk

from .test_bingx_sync import STANDARD, SWAP
from .test_risk import SCENARIO

MARKET = "binance:perp:BTCUSDT"


@pytest.mark.parametrize("leverage", [1, 7, 27, 42, 75, 100, 109, 125])
def test_custom_leverage_persists_analysis_and_manual_position_then_edits(leverage):
    with TestClient(app) as client:
        preference = client.put("/api/v1/settings", json={
            "trading_preferences": {"leverage": leverage},
        })
        assert preference.status_code == 200, preference.text
        assert client.get("/api/v1/settings").json()["trading_preferences"]["leverage"] == leverage
        created = client.post("/api/v1/positions", json={
            "market_id": MARKET, "leverage": leverage, "entry_price": "100", "quantity": "2",
        })
        assert created.status_code == 201, created.text
        position = created.json()
        assert position["leverage"] == leverage
        market_analysis = client.post("/api/v1/analyses", json={
            "market_id": MARKET, "leverage": leverage,
        }, headers={"Idempotency-Key": f"custom-market-{leverage}"})
        assert market_analysis.status_code == 202, market_analysis.text
        assert market_analysis.json()["submitted_input"]["leverage"] == leverage
        position_analysis = client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": MARKET, "leverage": leverage,
            "position_ids": [position["id"]],
        }, headers={"Idempotency-Key": f"custom-position-{leverage}"})
        assert position_analysis.status_code == 202, position_analysis.text
        with connect(readonly=True) as db:
            stored = db.execute("SELECT positions_json FROM analyses WHERE id=?",
                                (position_analysis.json()["id"],)).fetchone()
            assert json.loads(stored["positions_json"])[0]["leverage"] == leverage
        edited = client.patch(f"/api/v1/positions/{position['id']}", json={
            "market_id": MARKET, "leverage": 125, "entry_price": "100", "quantity": "2",
            "expected_version": position["version"],
        })
        assert edited.status_code == 200, edited.text
        assert edited.json()["leverage"] == 125
        assert client.get("/api/v1/positions").json()[0]["leverage"] == 125


@pytest.mark.parametrize("leverage", [0, 126, 137, -1, 5.5, "", None])
def test_invalid_leverage_is_rejected_without_silently_substituting_a_default(leverage):
    with TestClient(app) as client:
        body = {"market_id": MARKET, "leverage": leverage}
        assert client.post("/api/v1/analyses", json=body,
                           headers={"Idempotency-Key": "invalid-leverage"}).status_code == 422
        assert client.post("/api/v1/positions", json=body | {
            "entry_price": "100", "quantity": "2",
        }).status_code == 422
        existing = client.post("/api/v1/positions", json={
            "market_id": MARKET, "leverage": 42, "entry_price": "100", "quantity": "2",
        }).json()
        assert client.patch(f"/api/v1/positions/{existing['id']}", json=body | {
            "entry_price": "100", "quantity": "2", "expected_version": existing["version"],
        }).status_code == 422
        assert client.get("/api/v1/positions").json()[0]["leverage"] == 42
        assert client.get("/api/v1/analyses").json() == []


@pytest.mark.parametrize("kind,row", [("perpetual", SWAP), ("standard", STANDARD)])
@pytest.mark.parametrize("leverage", ["27", "75", "100", "125"])
def test_bingx_normalization_preserves_supported_custom_leverage(kind, row, leverage):
    positions, skipped = normalize_positions(kind, [row | {"leverage": leverage}])
    assert skipped == 0 and positions[0]["leverage"] == int(leverage)


@pytest.mark.parametrize("leverage", ["0", "126", "137", "5.5", "NaN", "Infinity"])
def test_bingx_normalization_rejects_invalid_leverage_without_updates(leverage):
    with pytest.raises(BingXError):
        normalize_positions("perpetual", [SWAP | {"leverage": leverage}])


@pytest.mark.parametrize("leverage", [27, 75, 100, 125])
def test_risk_calculation_accepts_custom_leverage_and_scales_margin_only(leverage):
    baseline = costed_risk("long", Decimal(100), Decimal(95), Decimal(110), 1, SCENARIO)
    custom = costed_risk("long", Decimal(100), Decimal(95), Decimal(110), leverage, SCENARIO)
    for key in ("net_reward_per_unit_usdt", "net_loss_per_unit_usdt", "net_risk_reward"):
        assert custom[key] == baseline[key]
    expected = Decimal(custom["net_loss_per_unit_usdt"]) / (Decimal(100) / leverage) * 100
    assert Decimal(custom["risk_on_theoretical_margin_pct"]) == expected.quantize(Decimal("0.01"))


@pytest.mark.parametrize("leverage", [0, 126, 137, -1, 5.5, True, "75"])
def test_risk_calculation_rejects_noninteger_or_out_of_range_leverage(leverage):
    with pytest.raises(ValueError, match="Invalid direction or leverage"):
        costed_risk("long", Decimal(100), Decimal(95), Decimal(110), leverage, SCENARIO)
