import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from trade_helper import market_catalog
from trade_helper.api import app
from trade_helper.bingx import normalize_positions
from trade_helper.config import data_dir
from trade_helper.market import fetch_quote, fetch_tick_size, symbol_for
from trade_helper.models import AnalysisRequest, PositionInput


def row(base, quote="USDT", **extra):
    return {"symbol": f"{base}{quote}", "baseAsset": base, "quoteAsset": quote,
            "marginAsset": quote, "status": "TRADING", "contractType": "PERPETUAL",
            "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.00001000"}], **extra}


@pytest.fixture
def empty_catalog(monkeypatch):
    monkeypatch.setattr(market_catalog, "_cache", None)
    monkeypatch.setattr(market_catalog, "_cache_path", None)
    monkeypatch.setattr(market_catalog, "_last_failure_at", 0.0)


def mock_response(payload):
    return httpx.Response(200, json=payload, request=httpx.Request("GET", "https://test.invalid"))


def install(payload):
    return market_catalog._install(market_catalog._parse_exchange_info(payload, time.time()),
                                   data_dir() / "market_catalog.json")


def test_catalog_includes_dynamic_usdt_perpetuals_and_unicode(monkeypatch, empty_catalog):
    calls = []
    payload = {"symbols": [row("BNB"), row("DOGE", "USDC"), row("币安人生"),
                           row("OLD", status="SETTLING"),
                           row("BTC", contractType="CURRENT_QUARTER"),
                           row("ETH", "BUSD")]}
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda url, **kwargs: calls.append(url) or mock_response(payload))
    response = TestClient(app).get("/api/v1/markets")
    assert response.status_code == 200
    assert response.headers["X-Market-Catalog-Status"] == "fresh"
    assert response.headers["X-Market-Catalog-Updated-At"]
    markets = response.json()
    assert {market["symbol"] for market in markets} == {"BNB/USDT", "币安人生/USDT"}
    for market in markets:
        assert market["quote_asset"] == market["margin_asset"] == market["settlement_asset"]
        assert AnalysisRequest(market_id=market["id"]).market_id == market["id"]
        assert PositionInput(market_id=market["id"], entry_price="1", quantity="1").market_id == market["id"]
        assert symbol_for(market["id"]) == market["binance_symbol"]
        assert fetch_tick_size(market["id"]) == Decimal("0.00001000")
    assert len(calls) == 1  # Tick and model validation reuse the whole catalog.
    assert (data_dir() / "market_catalog.json").exists()


@pytest.mark.parametrize("market_id", ["binance:perp:UNKNOWNUSDT", "other:perp:BTCUSDT",
                                      "binance:perp:BTCUSDT?token=secret", "binance:perp:../BTCUSDT",
                                      "binance:perp:BTCUSDT\n", "binance:perp:BTCUSD",
                                      "binance:perp:DOGEUSDC"])
def test_models_reject_unlisted_or_unsafe_market_ids(market_id):
    with pytest.raises(ValidationError):
        AnalysisRequest(market_id=market_id)
    with pytest.raises(ValidationError):
        PositionInput(market_id=market_id, entry_price="1", quantity="1")


def test_malformed_market_id_does_not_request_catalog(monkeypatch, empty_catalog):
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda *_args, **_kwargs: pytest.fail("Invalid IDs must fail before network"))
    with pytest.raises(ValueError):
        symbol_for("binance:perp:BTCUSDT&symbol=ETHUSDT")


def test_dynamic_bingx_mapping_only_includes_usdt_and_preserves_unmatched_count():
    install({"symbols": [row("BNB"), row("DOGE"), row("DOGE", "USDC")]})
    common = {"positionAmt": "2", "positionSide": "SHORT", "avgPrice": "1",
              "leverage": "5", "isolated": True}
    positions, skipped = normalize_positions("perpetual", [
        {**common, "positionId": "bnb", "symbol": "BNB-USDT"},
        {**common, "positionId": "doge", "symbol": "DOGE/USDT"},
        {**common, "positionId": "doge-usdc", "symbol": "DOGE-USDC"},
        {**common, "positionId": "unknown", "symbol": "UNKNOWN-USDT"},
    ])
    assert {position["market_id"] for position in positions} == {
        "binance:perp:BNBUSDT", "binance:perp:DOGEUSDT"}
    assert skipped == 2
    assert positions[1]["exchange_symbol"] == "DOGE-USDT"


def test_quote_preserves_dynamic_market_assets(monkeypatch):
    install({"symbols": [row("DOGE")]})
    seen = []

    def reply(url, **options):
        seen.append(options["params"]["symbol"])
        if url.endswith("/premiumIndex"):
            return mock_response({"markPrice": "1.0001", "indexPrice": "1.0002",
                                  "lastFundingRate": "0.0001", "nextFundingTime": 1700000000000})
        return mock_response({"price": "1"})

    monkeypatch.setattr("trade_helper.market.httpx.get", reply)
    quote = fetch_quote("binance:perp:DOGEUSDT")
    assert quote["quote_asset"] == quote["margin_asset"] == quote["settlement_asset"] == "USDT"
    assert quote["base_asset"] == "DOGE"
    assert quote["binance_symbol"] == "DOGEUSDT"
    assert seen == ["DOGEUSDT", "DOGEUSDT"]


def test_upstream_failure_returns_stale_last_good_and_reuses_after_restart(monkeypatch, empty_catalog):
    path = data_dir() / "market_catalog.json"
    snapshot = market_catalog._parse_exchange_info({"symbols": [row("BNB")]},
                                                   time.time() - 3600)
    market_catalog._save_disk(path, snapshot)
    calls = []

    def unavailable(*_args, **_kwargs):
        calls.append(1)
        raise httpx.ConnectError("Private upstream details")

    monkeypatch.setattr(market_catalog.httpx, "get", unavailable)
    response = TestClient(app).get("/api/v1/markets")
    assert response.status_code == 200
    assert response.headers["X-Market-Catalog-Status"] == "stale"
    assert response.json()[0]["id"] == "binance:perp:BNBUSDT"
    assert fetch_tick_size("binance:perp:BNBUSDT") == Decimal("0.00001000")
    assert len(calls) == 1


def test_fresh_disk_catalog_avoids_network_in_new_process(monkeypatch, empty_catalog):
    snapshot = market_catalog._parse_exchange_info({"symbols": [row("BNB")]}, time.time())
    market_catalog._save_disk(data_dir() / "market_catalog.json", snapshot)
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda *_args, **_kwargs: pytest.fail("Fresh disk catalog must be shared"))
    assert symbol_for("binance:perp:BNBUSDT") == "BNBUSDT"


def test_old_mixed_currency_disk_cache_is_filtered_to_usdt(monkeypatch, empty_catalog):
    snapshot = market_catalog._parse_exchange_info({"symbols": [row("BNB")]}, time.time())
    old_usdc = {**snapshot.markets[0], "id": "binance:perp:BNBUSDC", "symbol": "BNB/USDC",
                "binance_symbol": "BNBUSDC", "quote_asset": "USDC", "margin_asset": "USDC",
                "settlement_asset": "USDC"}
    mixed = market_catalog.CatalogSnapshot((*snapshot.markets, old_usdc), snapshot.updated_at,
                                           snapshot.fetched_at)
    market_catalog._save_disk(data_dir() / "market_catalog.json", mixed)
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda *_args, **_kwargs: pytest.fail("Fresh USDT disk data must be reused"))
    assert [market["id"] for market in market_catalog.get_catalog().markets] == ["binance:perp:BNBUSDT"]
    with pytest.raises(ValueError):
        symbol_for("binance:perp:BNBUSDC")


def test_cached_data_older_than_24_hours_is_rejected_on_failure(monkeypatch, empty_catalog):
    expired = market_catalog._parse_exchange_info({"symbols": [row("BNB")]},
                                                  time.time() - 25 * 3600)
    market_catalog._save_disk(data_dir() / "market_catalog.json", expired)

    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("Temporary failure")

    monkeypatch.setattr(market_catalog.httpx, "get", unavailable)
    client = TestClient(app)
    assert client.get("/api/v1/markets").status_code == 503
    assert client.get("/api/v1/quotes", params={"market_id": "binance:perp:BNBUSDT"}).status_code == 503


def test_malformed_refresh_preserves_last_good_disk_catalog(monkeypatch, empty_catalog):
    path = data_dir() / "market_catalog.json"
    old = market_catalog._parse_exchange_info({"symbols": [row("BNB")]}, time.time() - 3600)
    market_catalog._save_disk(path, old)
    before = path.read_bytes()
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda *_args, **_kwargs: mock_response({"symbols": [row("BNB", filters=[])]}))
    response = TestClient(app).get("/api/v1/markets")
    assert response.status_code == 200
    assert response.headers["X-Market-Catalog-Status"] == "stale"
    assert response.json()[0]["id"] == "binance:perp:BNBUSDT"
    assert path.read_bytes() == before


@pytest.mark.parametrize("payload", [{"symbols": []}, {"code": -1},
                                     {"symbols": [row("BNB", filters=[])]}])
def test_bad_upstream_data_is_not_saved_or_presented_as_complete(monkeypatch, empty_catalog, payload):
    monkeypatch.setattr(market_catalog.httpx, "get", lambda *_args, **_kwargs: mock_response(payload))
    response = TestClient(app).get("/api/v1/markets")
    assert response.status_code == 503
    assert not (data_dir() / "market_catalog.json").exists()


def test_catalog_unavailable_is_safe_503_in_body_validation(monkeypatch, empty_catalog):
    def unavailable(*_args, **_kwargs):
        raise httpx.ConnectError("secret-token-example")

    monkeypatch.setattr(market_catalog.httpx, "get", unavailable)
    client = TestClient(app)
    for endpoint, body in [("analyses", {"market_id": "binance:perp:BNBUSDT"}),
                           ("positions", {"market_id": "binance:perp:BNBUSDT",
                                          "entry_price": "1", "quantity": "1"})]:
        response = client.post(f"/api/v1/{endpoint}", json=body,
                               headers={"Idempotency-Key": "catalog-unavailable"})
        assert response.status_code == 503
        assert "secret-token-example" not in response.text


def test_refresh_single_flight_under_concurrent_symbol_and_tick_lookup(monkeypatch, empty_catalog):
    entered, release = Event(), Event()
    calls = []

    def delayed(*_args, **_kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return mock_response({"symbols": [row("BNB")]})

    monkeypatch.setattr(market_catalog.httpx, "get", delayed)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(symbol_for, "binance:perp:BNBUSDT") for _ in range(8)]
        assert entered.wait(2)
        release.set()
        assert [future.result() for future in futures] == ["BNBUSDT"] * 8
    assert len(calls) == 1


def test_successful_refresh_drops_delisted_markets(monkeypatch):
    old = market_catalog._parse_exchange_info({"symbols": [row("BNB"), row("OLD")]},
                                             time.time() - 3600)
    market_catalog._install(old, data_dir() / "market_catalog.json")
    monkeypatch.setattr(market_catalog.httpx, "get",
                        lambda *_args, **_kwargs: mock_response({"symbols": [row("BNB"),
                                                                           row("OLD", status="SETTLING")]}))
    assert symbol_for("binance:perp:BNBUSDT") == "BNBUSDT"
    with pytest.raises(ValueError):
        symbol_for("binance:perp:OLDUSDT")


def test_news_route_validates_dynamic_catalog(monkeypatch):
    install({"symbols": [row("BNB")]})
    monkeypatch.setattr("trade_helper.api.news_snapshot", lambda _now, market_id: {"market_id": market_id})
    client = TestClient(app)
    assert client.get("/api/v1/news", params={"market_id": "binance:perp:BNBUSDT"}).status_code == 200
    assert client.get("/api/v1/news", params={"market_id": "binance:perp:OLDUSDT"}).status_code == 422
