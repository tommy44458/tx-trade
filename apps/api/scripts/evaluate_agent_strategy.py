"""Opt-in model evaluation with synthetic scenarios and optional frozen snapshots.

Rubrics stay outside model input. These are decision-quality checks, not backtests.
No exchange or application database writes are performed.
"""

import argparse
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from openai import APIError

from trade_helper import agent
from trade_helper.agent import STRATEGY_PROMPT_VERSION, analyze_with_tools, model_failure_reason
from trade_helper.analysis import build_report
from trade_helper.prompts import resolve_prompt
from trade_helper.report_contract import validate_report
from trade_helper.technical_snapshot import prepare_analysis_evidence


def _candles(timeframe: str, now: datetime, pattern: str, mirror: bool) -> list[dict]:
    hours = 1 if timeframe == "1h" else 4
    end = now.replace(hour=now.hour // hours * hours, minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=100 * hours)
    rows = []
    for index in range(100):
        high = "105" if index == 25 else "105.2" if index == 45 else "120" if index == 65 else "101"
        low = "95" if index == 20 else "95.2" if index == 40 else "90" if index == 80 else "99"
        rows.append({"open_time": (start + timedelta(hours=index * hours)).isoformat(),
                     "close_time": (start + timedelta(hours=(index + 1) * hours) -
                                    timedelta(milliseconds=1)).isoformat(),
                     "open": "100", "high": high, "low": low, "close": "100", "volume": "100"})
    patterns = {
        "rejection": [("100", "105.2", "99.8", "104.4"), ("104.4", "106", "104", "104.7"),
                      ("104.7", "105.8", "104.2", "104.9"), ("104.9", "105.7", "104.7", "105")],
        "continuation": [("100", "107", "99.8", "106"), ("106", "107.5", "105.8", "107"),
                         ("107", "107.2", "105.3", "106.5"), ("106.5", "108", "106.3", "107.5")],
    }
    if pattern in patterns:
        for row, values in zip(rows[-4:], patterns[pattern], strict=True):
            row.update(zip(("open", "high", "low", "close"), values, strict=True))
            row["volume"] = "300"
    if mirror:
        for row in rows:
            op, high, low, close = (Decimal(row[key]) for key in ("open", "high", "low", "close"))
            row.update(open=str(200 - op), high=str(200 - low), low=str(200 - high), close=str(200 - close))
    return rows


def synthetic_cases(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    cases = []
    for name, pattern, mirror in (
            ("resistance_rejection", "rejection", False),
            ("support_rejection", "rejection", True),
            ("accepted_breakout_against_short_bias", "continuation", False),
            ("accepted_breakdown_against_long_bias", "continuation", True),
            ("range_middle_low_risk", "middle", False)):
        rows = _candles("1h", now, pattern, mirror)
        context = _candles("4h", now, pattern, mirror)
        op = Decimal(rows[-1]["close"])
        price = Decimal("105.1" if pattern == "rejection" else "107.6" if pattern == "continuation" else "100")
        high, low = (Decimal("105.9"), Decimal("104.8")) if pattern == "rejection" else (
            (Decimal(108), Decimal("107.3")) if pattern == "continuation" else (Decimal("100.2"), Decimal("99.8")))
        if mirror:
            price, high, low = 200 - price, 200 - low, 200 - high
        opened = datetime.fromisoformat(rows[-1]["close_time"]) + timedelta(milliseconds=1)
        quote = {"price": str(price), "tick_size": "0.1", "observed_at": now.isoformat(),
                 "forming_candle": {"open_time": opened.isoformat(),
                     "close_time": (opened + timedelta(hours=1) - timedelta(milliseconds=1)).isoformat(),
                     "open": str(op), "high": str(high), "low": str(low), "close": str(price),
                     "volume": "80", "fetched_at": now.isoformat()}}
        request = {"market_id": "synthetic:perp:TESTUSDT", "timeframe": "1h", "kind": "market",
                   "directional_bias": None if pattern == "middle" else "bullish" if mirror else "bearish",
                   "risk_tolerance": "low" if pattern == "middle" else "high",
                   "trading_style": "right" if pattern == "middle" else "left", "leverage": 5}
        rubric = {"human_review": "核對價格位置、反面證據及偏好是否適用，不能以符合偏好代替行情判斷。"}
        if pattern == "continuation":
            rubric["avoid_immediate_side"] = "long" if mirror else "short"
        elif pattern == "middle":
            rubric["avoid_immediate_entry"] = True
        cases.append({"id": name, "source": "synthetic_not_historical_market", "request": request,
                      "snapshot": {"candles": rows, "context_candles": context,
                                   "quote": quote, "positions": []}, "rubric": rubric})
    return cases


def review_flags(case: dict, report: dict) -> list[str]:
    """Flag counterexamples for review; never change a production trading decision."""
    plan, rubric = report["entry_decision"], case.get("rubric", {})
    flags = []
    if plan["action"] == "open_now":
        if rubric.get("avoid_immediate_entry"):
            flags.append("Immediate entry in the low-risk range-middle fixture needs review")
        if plan["side"] == rubric.get("avoid_immediate_side"):
            flags.append("Immediate entry follows the contradicted directional bias; review its evidence")
    return flags


def evaluate_case(case: dict, *, prompt_locale: str = 'en-US',
                  response_locale: str = 'zh-TW') -> dict:
    snapshot = deepcopy(case["snapshot"])
    quote, request = snapshot["quote"], case["request"] | {'output_locale': response_locale}
    task = 'strategy_positions' if request.get('kind') == 'positions' else 'strategy_market'
    bundle = resolve_prompt(task, prompt_locale=prompt_locale, response_locale=response_locale,
                            inputs=request)
    quote.setdefault("snapshot_hash", sha256(json.dumps(snapshot, sort_keys=True,
                                                        separators=(",", ":")).encode()).hexdigest())
    trace = prepare_analysis_evidence(request, snapshot["candles"], quote,
                                      snapshot.get("context_candles"), snapshot.get("positions", []))
    attempts = []
    original_final = agent._final_reasoning
    def capture_final(raw, *args, **kwargs):
        attempts.append(raw)
        return original_final(raw, *args, **kwargs)
    try:
        with patch("trade_helper.agent._final_reasoning", capture_final):
            decision = analyze_with_tools(request, snapshot["candles"], quote,
                snapshot.get("context_candles"), snapshot.get("positions", []), prepared_trace=trace,
                prompt_bundle=bundle)
    except (APIError, RuntimeError, ValueError, TimeoutError) as exc:
        return {"id": case["id"], "source": case["source"], "contract_valid": False,
                "error_type": type(exc).__name__, "error_detail": model_failure_reason(exc),
                "model_attempts": attempts}

    class SnapshotClock(datetime):
        @classmethod
        def now(cls, tz=None):
            observed = datetime.fromisoformat(quote["observed_at"])
            return observed.astimezone(tz) if tz else observed.replace(tzinfo=None)

    # Replay freshness against the original clock, only inside this evaluation.
    # Production quote-age checks remain untouched.
    with patch("trade_helper.analysis.datetime", SnapshotClock):
        report = build_report(request, snapshot["candles"], quote, snapshot.get("positions", []),
            decision, snapshot.get("context_candles"), quote.get("event_context"), quote.get("news_context"))
    report["data_source"] = case["source"]
    report['output_locale'] = response_locale
    validate_report(report)
    return {"id": case["id"], "source": case["source"], "rubric": case.get("rubric"),
            "contract_valid": True, 'prompt_bundle': bundle.metadata(),
            "review_flags": review_flags(case, report), "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly use the configured model provider")
    parser.add_argument('--prompt-locale', choices=('en-US', 'zh-TW'), default='en-US')
    parser.add_argument('--response-locale', choices=('en-US', 'zh-TW'), default='zh-TW')
    parser.add_argument("--snapshot", type=Path, help="Optional frozen request/snapshot JSON; never includes future prices")
    parser.add_argument("--output", type=Path, help="Local results JSON (required with --live)")
    parser.add_argument("--case", action="append", dest="selected_cases", help="Run only a named case; repeat to select several")
    args = parser.parse_args()
    if args.live and not args.output:
        parser.error("--live requires --output")
    cases = synthetic_cases()
    if args.snapshot:
        frozen = json.loads(args.snapshot.read_text())
        snapshot = deepcopy(frozen["snapshot"])
        stored_hash = frozen.get("report", {}).get("market_snapshot_sha256")
        if stored_hash:
            snapshot["quote"]["snapshot_hash"] = stored_hash
        cases.append({"id": "historical_snapshot", "source": "frozen_historical_snapshot",
                      "request": frozen["request"], "snapshot": snapshot, "rubric": {
                          "human_review": "只按分析當時資料檢查合理性，不使用事後結果當作模型輸入或期待答案。"}})
    if args.selected_cases:
        if set(args.selected_cases) - {case["id"] for case in cases}:
            parser.error("Unknown case name")
        cases = [case for case in cases if case["id"] in args.selected_cases]
    output = {"prompt_version": STRATEGY_PROMPT_VERSION,
              'prompt_locale': args.prompt_locale, 'response_locale': args.response_locale,
              "recorded_at": datetime.now(UTC).isoformat(),
              "mode": "live_model_evaluation" if args.live else "offline_fixture_validation", "cases": []}
    for case in cases:
        if args.live:
            try:
                result = evaluate_case(case, prompt_locale=args.prompt_locale,
                                       response_locale=args.response_locale)
            except (APIError, RuntimeError, ValueError, TimeoutError) as exc:
                # Provider exception text may contain request details; record only its class.
                result = {"id": case["id"], "contract_valid": False, "error_type": type(exc).__name__,
                          "error_detail": model_failure_reason(exc)}
        else:
            s = case["snapshot"]
            prepare_analysis_evidence(case["request"], s["candles"], s["quote"], s.get("context_candles"))
            result = {"id": case["id"], "fixtures_valid": True}
        output["cases"].append(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        plan = result.get("report", {}).get("entry_decision", {})
        print(json.dumps({"id": case["id"], "action": plan.get("action"), "side": plan.get("side"),
                          **{k: v for k, v in result.items() if k in {"fixtures_valid", "contract_valid", "review_flags", "error_type", "error_detail"}}},
                         ensure_ascii=False), flush=True)
    if any(item.get("contract_valid") is False for item in output["cases"]):
        sys.exit(1)
    if any(item.get("review_flags") for item in output["cases"]):
        sys.exit(2)


if __name__ == "__main__":
    main()
