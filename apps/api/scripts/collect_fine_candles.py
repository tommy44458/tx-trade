"""Cache complete five-minute futures candles for intrabar friction replay."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from trade_helper.market import fetch_candles_range

START = datetime(2026, 9, 14, tzinfo=UTC)
END = datetime(2026, 9, 28, tzinfo=UTC)
OUTPUT = Path(__file__).resolve().parents[3] / "data" / "backtests" / "fine_2026-09-14_2026-09-28"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        path = OUTPUT / f"{symbol}_5m.json"
        if path.exists():
            candles = json.loads(path.read_text())
        else:
            candles = fetch_candles_range(f"binance:perp:{symbol}", "5m", START, END)
            path.write_text(json.dumps(candles, separators=(",", ":")))
        expected = int((END - START).total_seconds() // 300)
        if len(candles) != expected:
            raise ValueError(f"{symbol}: expected {expected} five-minute bars, got {len(candles)}")
        print(json.dumps({"market": symbol, "timeframe": "5m", "candles": len(candles),
                          "start": START.isoformat(), "end_exclusive": END.isoformat(),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
