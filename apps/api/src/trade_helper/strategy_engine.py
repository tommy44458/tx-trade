"""Versioned, deterministic conditional scenarios from confirmed v3 zones."""

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from .risk import costed_risk, round_stop_outward
from .support_levels_v3 import VERSION as LEVEL_VERSION
from .timeframes import advance_candle, analysis_timeframes, higher_timeframes

VERSION = "conditional_strategy_v2"
CONTEXT_VERSION = "timeframe_context_v2"
MIN_NET_RR = Decimal("1.5")


def other_timeframe(timeframe: str) -> str:
    return higher_timeframes(timeframe)[0]


def next_close(last_close: str, timeframe: str) -> str:
    return advance_candle(datetime.fromisoformat(last_close), timeframe).isoformat()


def compare_timeframes(primary: dict, context: dict | None, timeframe: str,
                       higher_metrics: dict[str, dict | None] | None = None) -> dict:
    """Use the same SMA20/SMA50 direction definition in both closed-candle series."""
    secondary = other_timeframe(timeframe)
    result = {"version": CONTEXT_VERSION, "primary_timeframe": timeframe,
              "context_timeframe": secondary, "primary_trend": primary["trend"],
              "primary_last_candle_at": primary["last_candle_at"]}
    frames = higher_timeframes(timeframe)
    sources = {secondary: context, **(higher_metrics or {})}
    result.update({"analysis_timeframes": list(analysis_timeframes(timeframe)),
                   "context_timeframes": list(frames),
                   "contexts": {frame: {"status": "available", "trend": data.get("trend"),
                                        "last_candle_at": data["last_candle_at"],
                                        "ma20": data.get("ma20"), "ma50": data.get("ma50")}
                                if (data := sources.get(frame)) else {"status": "unavailable"}
                                for frame in frames}})
    if context is None:
        return result | {"relation": "unavailable", "market_state": "insufficient",
                         "context_trend": None, "context_last_candle_at": None,
                         "context_ma20": None, "context_ma50": None}
    trend, background = primary["trend"], context["trend"]
    if trend == background == "mixed":
        relation, state = "range", "range"
    elif trend == background:
        relation, state = "aligned", trend
    elif trend != "mixed" and background != "mixed":
        relation, state = "conflict", "conflict"
    else:
        relation, state = "uncertain", "insufficient"
    return result | {"relation": relation, "market_state": state,
                     "context_trend": background,
                     "context_last_candle_at": context["last_candle_at"],
                     "context_ma20": context["ma20"], "context_ma50": context["ma50"]}


def _wait(title: str, reason: str, expires_at: str) -> list[dict]:
    return [{"id": "wait", "type": "wait", "title": title, "reason": reason,
             "status": "waiting", "confirmation_state": "not_applicable",
             "expires_at": expires_at, "strategy_rule_version": VERSION}]


def _round_out(price: Decimal, tick: Decimal, side: str) -> Decimal:
    rounding = ROUND_CEILING if side == "long" else ROUND_FLOOR
    return (price / tick).to_integral_value(rounding=rounding) * tick


def _candidate(kind: str, side: str, entry: Decimal, stop: Decimal, target: Decimal,
               quote: dict, metrics: dict, context: dict, bias: str | None, risk: str | None,
               leverage: int, zone: dict, target_zone: dict, expiry: str,
               trading_style: str | None) -> dict | None:
    atr = Decimal(metrics["atr14"])
    current = Decimal(quote["price"])
    valid_prices = 0 < stop < entry < target if side == "long" else 0 < target < entry < stop
    if not valid_prices:
        return None
    crossed_risk_or_target = (current <= stop or current >= target) if side == "long" else (
        current >= stop or current <= target)
    if crossed_risk_or_target:
        return None
    if abs(current - entry) > 3 * atr:
        return None
    risk_metrics = costed_risk(side, entry, stop, target, leverage, quote)
    net_reward = Decimal(risk_metrics["net_reward_per_unit_usdt"])
    net_loss = Decimal(risk_metrics["net_loss_per_unit_usdt"])
    if net_reward <= 0 or net_reward / net_loss < MIN_NET_RR:
        return None
    bias_fit = "unspecified" if bias is None else "aligned" if bias == (
        "bullish" if side == "long" else "bearish") else "conflict"
    names = {"pullback": "趨勢回調", "breakout": "突破確認", "range": "區間測試"}
    action = "做多" if side == "long" else "做空"
    left_entry = trading_style == "left"
    if left_entry:
        trigger = ("價格進入已確認 v3 支撐區、尚未觸及失效止損；屬提前測試，無收盤反轉確認"
                   if side == "long" else
                   "價格進入已確認 v3 壓力區、尚未觸及失效止損；屬提前測試，無收盤反轉確認")
        invalidation = ("價格跌破支撐下緣或觸及失效止損" if side == "long"
                        else "價格突破壓力上緣或觸及失效止損")
    elif kind == "breakout":
        trigger = ("下一根已收盤 K 線收於 v3 壓力上緣之上，盤中穿越不算確認" if side == "long"
                   else "下一根已收盤 K 線收於 v3 支撐下緣之下，盤中穿越不算確認")
        invalidation = "收盤回到突破區內，或價格觸及失效止損"
    else:
        trigger = ("下一根已收盤 K 線測試支撐並收回區間上緣" if side == "long"
                   else "下一根已收盤 K 線測試壓力並收回區間下緣")
        invalidation = ("收盤跌破支撐下緣，或價格觸及失效止損" if side == "long"
                        else "收盤突破壓力上緣，或價格觸及失效止損")
    evidence_ids = [str(level["id"]) for level in (zone, target_zone) if level.get("id")]
    event_status = quote.get("events_status")
    if event_status in {"offline", "partial"}:
        counter = ["官方經濟日程來源離線或不完整，不能聲稱已排除事件風險"]
    elif event_status in {"available", "no_events"}:
        counter = ["已核對官方日程；Fed 標題消息另有來源狀態，指標實際值尚未接入"]
    else:
        counter = ["新聞與經濟事件尚未接入，不能聲稱已排除事件風險"]
    if bias_fit == "conflict":
        counter.append("情境方向與你的判斷相反")
    if left_entry:
        counter.append("左側區間測試尚無收盤確認，價格可能直接穿透支撐或壓力")
    return {"id": f"{kind}:{side}:{entry}", "type": kind, "side": side,
            "title": f"{names[kind]}{action}情境", "status": "waiting",
            "confirmation_state": "awaiting_zone_test" if left_entry else "awaiting_close",
            "entry_style": "left" if left_entry else "right",
            "entry_style_fit": "matched" if trading_style else "unspecified",
            "trigger": trigger,
            "trigger_met": False, "entry": str(entry), "stop_loss": str(stop),
            "take_profit": str(target), "targets": [{"price": str(target),
                                                       "net_risk_reward": risk_metrics["net_risk_reward"]}],
            "risk_reward": risk_metrics["net_risk_reward"],
            "gross_risk_reward": risk_metrics["gross_risk_reward"],
            "leverage": leverage,
            "risk_on_theoretical_margin_pct": risk_metrics["risk_on_theoretical_margin_pct"],
            "risk_metrics": risk_metrics, "entry_assumption": "first_tick_beyond_zone" if kind == "breakout" else "zone_boundary",
            "preference_fit": bias_fit, "level_algorithm_version": LEVEL_VERSION,
            "risk_fit": {"low": "higher_confirmation", "medium": "balanced",
                         "high": "earlier_candidate"}.get(risk, "unrated"),
            "strategy_rule_version": VERSION, "evidence_level_ids": evidence_ids,
            "timeframe_relation": context["relation"], "event_exposure": "calendar_checked" if event_status in {"available", "no_events"} else "unknown",
            "expires_at": expiry, "invalidation": invalidation,
            "counter_evidence": counter,
            "reason": (f"{names[kind]}為左側提前區間測試；v3 區間可能產生阻力，也可能被直接穿透，尚無收盤確認。"
                       if left_entry else
                       f"{names[kind]}僅是有條件的價格阻力情境；v3 區間不保證反轉或突破，需等待所選週期收盤確認。"),
            "fee_note": "風報比採明示示例手續費與不利滑價，非實際帳戶費率；未計資金費或強平"}


def build_candidates(metrics: dict, context: dict, bias: str | None, risk: str | None,
                     quote: dict, leverage: int = 5, event_risk: str = "unavailable",
                     trading_style: str | None = None) -> list[dict]:
    if metrics.get("level_algorithm_version") != LEVEL_VERSION:
        raise ValueError("Strategy requires v3 support/resistance levels")
    if trading_style not in {None, "left", "right"}:
        raise ValueError("Unsupported trading style")
    timeframe = context["primary_timeframe"]
    expiry = next_close(metrics["last_candle_at"], timeframe)
    if event_risk == "recent_fomc_release":
        return _wait("等待 FOMC 聲明消化", "官方 FOMC 聲明發布後 15 分鐘內暫不產生新進場候選；尚未判讀政策方向", expiry)
    if event_risk in {"high_impact_window", "unknown_major_event"}:
        return _wait("等待事件結果", "重大事件結果未確認，暫不產生新進場候選", expiry)
    if context["relation"] == "unavailable":
        return _wait("等待跨週期資料", "缺少已收盤的另一週期行情，無法核對背景方向", expiry)
    if context["relation"] == "conflict":
        return _wait("等待週期方向一致", f"{timeframe.upper()} 與 {other_timeframe(timeframe).upper()} 的已收盤趨勢相反，先不推薦進場", expiry)
    if context["relation"] == "uncertain":
        return _wait("等待方向確認", "其中一個週期的均線與收盤價尚未形成一致趨勢", expiry)
    if trading_style == "left" and risk == "low":
        return _wait("等待確認以符合低風險傾向",
                     "左側提前測試尚無收盤確認，與本次低風險傾向衝突；請等待右側確認或調整偏好", expiry)
    trend = metrics["trend"]
    if bias is not None and bias != trend and risk == "low" and trend != "mixed":
        return _wait("你的看法與目前趨勢不同", "低風險傾向先等待新的收盤確認", expiry)
    eligible = [level for level in metrics["levels"] if level.get("algorithm_version") == LEVEL_VERSION
                and level.get("pivot_count", 0) >= 2 and level.get("independent_touch_count", 0) >= 1
                and level.get("zone_state") == "active"]
    supports = [level for level in eligible if level["kind"] == "support"]
    resistances = [level for level in eligible if level["kind"] == "resistance"]
    if not supports or not resistances:
        return _wait("價位依據不足", "缺少合格且已確認的 v3 支撐或壓力區", expiry)
    atr = Decimal(metrics["atr14"])
    tick = Decimal(str(quote["tick_size"]))
    if trend == "mixed" and not (Decimal(supports[0]["high"]) <= Decimal(quote["price"]) <=
                                 Decimal(resistances[0]["low"])):
        return _wait("等待回到區間內", "現價已在兩側 v3 候選區之外，不能假設區間持續有效", expiry)
    candidates: list[dict] = []

    def add(kind: str, side: str, entry: Decimal, stop: Decimal, target: Decimal,
            zone: dict, target_zone: dict) -> None:
        if trading_style == "left" and kind == "breakout":
            return
        if risk == "low" and (kind == "breakout" or zone["independent_touch_count"] < 2 or
                              (kind == "range" and target_zone["independent_touch_count"] < 2)):
            return
        candidate = _candidate(kind, side, entry, stop, target, quote, metrics, context,
                               bias, risk, leverage, zone, target_zone, expiry, trading_style)
        if candidate is not None and not (risk == "low" and candidate["preference_fit"] == "conflict"):
            candidates.append(candidate)

    if trend in {"bullish", "mixed"}:
        zone, opposite = supports[0], resistances[0]
        entry = Decimal(zone["high"])
        stop = round_stop_outward(Decimal(zone["low"]) - atr * Decimal("0.25"), tick, "long")
        add("range" if trend == "mixed" else "pullback", "long", entry, stop,
            Decimal(opposite["low"]), zone, opposite)
    if trend in {"bearish", "mixed"}:
        zone, opposite = resistances[0], supports[0]
        entry = Decimal(zone["low"])
        stop = round_stop_outward(Decimal(zone["high"]) + atr * Decimal("0.25"), tick, "short")
        add("range" if trend == "mixed" else "pullback", "short", entry, stop,
            Decimal(opposite["high"]), zone, opposite)
    if trend == "bullish" and len(resistances) > 1:
        zone, next_zone = resistances[:2]
        entry = _round_out(Decimal(zone["high"]) + tick, tick, "long")
        stop = round_stop_outward(Decimal(zone["low"]) - atr * Decimal("0.25"), tick, "long")
        add("breakout", "long", entry, stop, Decimal(next_zone["low"]), zone, next_zone)
    if trend == "bearish" and len(supports) > 1:
        zone, next_zone = supports[:2]
        entry = _round_out(Decimal(zone["low"]) - tick, tick, "short")
        stop = round_stop_outward(Decimal(zone["high"]) + atr * Decimal("0.25"), tick, "short")
        add("breakout", "short", entry, stop, Decimal(next_zone["high"]), zone, next_zone)
    candidates.sort(key=lambda item: (
        item["preference_fit"] == "conflict",
        item["type"] != "breakout" if trading_style == "right" else item["type"] == "breakout"))
    if candidates:
        return candidates[:3]
    reason = ("左側提前區間測試仍未通過 v3 區間、距離或示例成本後風報比檢核"
              if trading_style == "left" else
              "目前 v3 區間、距離與示例成本後風報比未同時達標")
    return _wait("等待更好的條件", reason, expiry)
