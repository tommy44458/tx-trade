"""Frozen one-year BTC/ETH v2/v3/v4 comparison using closed 1H/4H candles only."""

import argparse
import json
from datetime import UTC, datetime

from replay_v4 import evaluate

from trade_helper.config import data_dir
from trade_helper.market_store import cached_candles, digest, stamp

START = datetime(2025, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)
COARSE_START = {"1h": datetime(2025, 5, 20, tzinfo=UTC),
                "4h": datetime(2024, 7, 20, tzinfo=UTC)}
ROOT = data_dir() / "backtests" / "v4_main_tf_year_2025-09_2026-08"


def run(symbol: str, timeframe: str) -> dict:
    market = f"binance:perp:{symbol}"
    coarse = cached_candles(market, timeframe, COARSE_START[timeframe], END,
                            progress=lambda: print(f"Fetching missing {symbol} {timeframe} page", flush=True))
    print(f"Replaying {symbol} {timeframe}: {len(coarse)} closed main-timeframe candles", flush=True)
    result = evaluate(market, timeframe, 365, coarse_input=coarse,
                      evaluation_start=stamp(START.isoformat()), evaluation_end=stamp(END.isoformat()))
    result["evaluation_version"] = "v4_main_tf_year_1"
    result["source"] = {"coarse_source": "Binance USD-M /fapi/v1/klines",
                        "coarse_sha256": digest(coarse), "minute_data_used": False}
    path = ROOT / f"{symbol}_{timeframe}_result.json"
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if path.exists() and path.read_text() != encoded:
        raise ValueError(f"Refusing to overwrite changed frozen report: {path}")
    path.write_text(encoded)
    print(json.dumps({"market": symbol, "timeframe": timeframe, "counts": result["counts"],
                      "report": str(path)}, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=("BTCUSDT", "ETHUSDT"))
    parser.add_argument("--timeframe", choices=("1h", "4h"))
    args = parser.parse_args()
    symbols = (args.symbol,) if args.symbol else ("BTCUSDT", "ETHUSDT")
    timeframes = (args.timeframe,) if args.timeframe else ("1h", "4h")
    ROOT.mkdir(parents=True, exist_ok=True)
    for symbol in symbols:
        for timeframe in timeframes:
            run(symbol, timeframe)


if __name__ == "__main__":
    main()
