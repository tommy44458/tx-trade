"""Evaluate frozen friction rules by month over Sep 2025–Aug 2026.

Historical Binance candles are cached locally so the exact study can be rerun.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from backtest_level_friction_2w import evaluate

from trade_helper.market import BASE_URL

WARMUP_START = datetime(2025, 7, 1, tzinfo=UTC)
EVALUATION_START = datetime(2025, 9, 1, tzinfo=UTC)
EVALUATION_END = datetime(2026, 9, 1, tzinfo=UTC)
DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "backtests" / "friction_2025-09_2026-08"


def fetch_history(symbol: str, timeframe: str) -> list[dict]:
    step_ms = (1 if timeframe == "1h" else 4) * 60 * 60 * 1000
    cursor = int(WARMUP_START.timestamp() * 1000)
    end_ms = int(EVALUATION_END.timestamp() * 1000) - 1
    candles = []
    with httpx.Client(timeout=30) as client:
        while cursor <= end_ms:
            response = client.get(f"{BASE_URL}/fapi/v1/klines", params={
                "symbol": symbol, "interval": timeframe, "startTime": cursor,
                "endTime": end_ms, "limit": 1000,
            })
            response.raise_for_status()
            rows = response.json()
            if not rows:
                raise ValueError(f"Binance returned no rows after {cursor} for {symbol} {timeframe}")
            for row in rows:
                if row[0] != cursor:
                    raise ValueError(f"Missing or duplicate candle at {cursor}: got {row[0]}")
                candles.append({
                    "open_time": datetime.fromtimestamp(row[0] / 1000, UTC).isoformat(),
                    "close_time": datetime.fromtimestamp(row[6] / 1000, UTC).isoformat(),
                    "open": str(Decimal(row[1])), "high": str(Decimal(row[2])),
                    "low": str(Decimal(row[3])), "close": str(Decimal(row[4])),
                    "volume": str(Decimal(row[5])),
                })
                cursor += step_ms
            print(f"{symbol} {timeframe}: fetched {len(candles)} candles", flush=True)
            if len(rows) < 1000 and cursor <= end_ms:
                raise ValueError(f"Unexpected short historical page for {symbol} {timeframe}")
    if cursor <= end_ms:
        raise ValueError(f"History ended before {EVALUATION_END.isoformat()}")
    return candles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Replace local candle snapshots")
    args = parser.parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timeframe in ("1h", "4h"):
            path = DATA_DIR / f"{symbol}_{timeframe}.json"
            if path.exists() and not args.refresh:
                candles = json.loads(path.read_text())
            else:
                candles = fetch_history(symbol, timeframe)
                path.write_text(json.dumps(candles, separators=(",", ":")))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result = evaluate(candles, timeframe, start=EVALUATION_START,
                              end_exclusive=EVALUATION_END)
            report = {"market": symbol, "timeframe": timeframe,
                      "source": "Binance USD-M /fapi/v1/klines",
                      "source_sha256": digest, "source_candles": len(candles),
                      "warmup_start": WARMUP_START.isoformat(),
                      "evaluation_start": EVALUATION_START.isoformat(),
                      "evaluation_end_exclusive": EVALUATION_END.isoformat(), **result}
            (DATA_DIR / f"{symbol}_{timeframe}_result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            summary = {key: value for key, value in result.items()
                       if key.startswith(("pivot_cluster_", "width_matched_"))}
            print(json.dumps({"market": symbol, "timeframe": timeframe,
                              "sha256": digest, "candles": len(candles), **summary}), flush=True)


if __name__ == "__main__":
    main()
