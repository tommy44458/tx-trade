"""Deterministic action candidates for local perpetual positions."""

from decimal import Decimal

from .risk import round_stop_outward
from .support_levels_v3 import VERSION as LEVEL_VERSION

VERSION = "position_advice_v1"
SNAPSHOT_FIELDS = ("id", "version", "market_id", "side", "leverage", "margin_mode",
                   "entry_price", "quantity", "stop_loss", "take_profit", "previous_stop_loss",
                   "entry_time", "exchange_liquidation_price", "source", "contract_type", "notes")


def compact_positions(positions: list[dict], *, include_notes: bool = True) -> list[dict]:
    """Keep only trading inputs in the report and model context, never identity fields."""
    fields = SNAPSHOT_FIELDS if include_notes else tuple(field for field in SNAPSHOT_FIELDS if field != "notes")
    return [{field: position.get(field) for field in fields} for position in positions]


def _candidate(position: dict, kind: str, reason: str, *, proposed_stop: str | None = None,
               risk_change_usdt: str | None = None, level_id: str | None = None) -> dict:
    return {"id": f"{position['id']}:{kind}", "kind": kind, "reason": reason,
            "current_stop": position.get("stop_loss"), "proposed_stop": proposed_stop,
            "risk_change_usdt": risk_change_usdt, "level_id": level_id}


def _tighten_candidate(position: dict, price: Decimal, levels: list[dict],
                       atr: Decimal, tick: Decimal) -> dict | None:
    if atr <= 0:
        return None
    old_stop = Decimal(position["stop_loss"])
    side = position["side"]
    zone_kind = "support" if side == "long" else "resistance"
    qualifying = [level for level in levels if level.get("kind") == zone_kind and
                  level.get("algorithm_version") == LEVEL_VERSION and
                  level.get("zone_state") == "active" and
                  level.get("pivot_count", 0) >= 2 and
                  level.get("independent_touch_count", 0) >= 1 and
                  (Decimal(level["high"]) < price if side == "long" else
                   Decimal(level["low"]) > price)]
    if not qualifying:
        return None
    level = (max(qualifying, key=lambda item: Decimal(item["low"])) if side == "long" else
             min(qualifying, key=lambda item: Decimal(item["high"])))
    buffer = max(tick, atr / 10)
    boundary = Decimal(level["low"] if side == "long" else level["high"])
    proposed = round_stop_outward(boundary - buffer if side == "long" else boundary + buffer,
                                  tick, side)
    if (proposed <= 0 or (price - proposed if side == "long" else proposed - price) <
            max(2 * tick, atr / 2) or not (old_stop < proposed < price if side == "long" else
                                           price < proposed < old_stop)):
        return None
    direction = Decimal(1) if side == "long" else Decimal(-1)
    change = (old_stop - proposed) * direction * Decimal(position["quantity"])
    if change >= 0:
        return None
    return _candidate(position, "tighten_stop_review",
                      "合格 v3 區間提供較靠近現價的保護參考；調整前須核對觸發價格與可能提前出場。",
                      proposed_stop=str(proposed), risk_change_usdt=str(change),
                      level_id=level["id"])


def stop_exposure(position: dict, quote: dict, account_equity_usdt: str | None) -> dict | None:
    """Price giveback from current mark to a pending stop, not an account loss forecast."""
    if account_equity_usdt is None or not position.get("stop_loss"):
        return None
    equity = Decimal(account_equity_usdt)
    if not equity.is_finite() or equity <= 0:
        raise ValueError("Invalid account equity")
    price = Decimal(quote.get("mark_price") or quote["price"])
    stop = Decimal(position["stop_loss"])
    direction = Decimal(1) if position["side"] == "long" else Decimal(-1)
    gap = (price - stop) * direction
    if gap <= 0:
        return None
    exposure = gap * Decimal(position["quantity"])
    return {"stop_price_exposure_usdt": str(exposure),
            "stop_price_exposure_pct_of_equity": str((exposure / equity * 100).quantize(Decimal("0.01")))}


def build_position_options(positions: list[dict], quote: dict, levels: list[dict],
                           market_state: str, atr: Decimal,
                           directional_bias: str | None = None,
                           risk_tolerance: str | None = None,
                           account_equity_usdt: str | None = None) -> dict:
    """Propose review actions, never claim that an exchange order was filled or change it."""
    if not positions:
        raise ValueError("Position advice requires at least one selected position")
    price = Decimal(quote.get("mark_price") or quote["price"])
    tick = Decimal(str(quote["tick_size"]))
    if price <= 0 or tick <= 0 or atr < 0:
        raise ValueError("Invalid position advice market data")
    result = []
    seen = set()
    for position in positions:
        position_id = position["id"]
        if position_id in seen or position["side"] not in {"long", "short"}:
            raise ValueError("Duplicate position or invalid direction")
        seen.add(position_id)
        direction = Decimal(1) if position["side"] == "long" else Decimal(-1)
        stop = Decimal(position["stop_loss"]) if position.get("stop_loss") else None
        target = Decimal(position["take_profit"]) if position.get("take_profit") else None
        liquidation = (Decimal(position["exchange_liquidation_price"])
                       if position.get("exchange_liquidation_price") else None)
        if liquidation is not None and (price - liquidation) * direction <= 0:
            candidates = [_candidate(position, "verify_execution",
                                     "參考標記價已達手動登記的交易所強平價；先向交易所核對部位狀態，不能假定已強平。")]
        elif stop is not None and (price - stop) * direction <= 0:
            candidates = [_candidate(position, "verify_execution",
                                     "目前參考價格已達登記止損；先向交易所核對委託與實際成交，不能假定已平倉。")]
        elif target is not None and (target - price) * direction <= 0:
            candidates = [_candidate(position, "verify_execution",
                                     "目前參考價格已達登記止盈；先向交易所核對委託與實際成交，不能假定已平倉。")]
        elif stop is None or target is None:
            missing = "止損與止盈" if stop is None and target is None else "止損" if stop is None else "止盈"
            candidates = [_candidate(position, "review_protection",
                                     f"手動部位尚缺{missing}；請先檢查保護條件，系統不自動補造價位。")]
        else:
            candidates = [_candidate(position, "maintain",
                                     "登記的止損與止盈尚未觸及；維持原條件並等待下一次已收盤資料確認。")]
            tightened = _tighten_candidate(position, price, levels, atr, tick)
            if tightened:
                candidates.append(tightened)
            opposing = (market_state == "bearish" and position["side"] == "long" or
                        market_state == "bullish" and position["side"] == "short")
            event_window = (quote.get("event_risk") in {"high_impact_window", "unknown_major_event"} or
                            quote.get("news_risk") == "recent_fomc_release")
            bias_conflict = (directional_bias == "bearish" and position["side"] == "long" or
                             directional_bias == "bullish" and position["side"] == "short")
            low_risk_uncertainty = risk_tolerance == "low" and market_state in {
                "conflict", "insufficient"}
            if opposing or event_window or bias_conflict or low_risk_uncertainty:
                opposing_reason = ("跨週期市場方向與部位相反；可評估降低曝險或退出，但沒有帳戶權益時不指定減倉比例。"
                                   if account_equity_usdt is None else
                                   "跨週期市場方向與部位相反；可評估降低曝險或退出；仍缺少明確風險預算與成交條件，因此不指定減倉比例。")
                reason = (opposing_reason if opposing else
                          "目前處於重大事件風險窗口；可評估降低曝險，仍須自行核對交易所部位與成交。"
                          if event_window else
                          "當次個人方向判斷與持倉相反；可評估降低曝險，但個人看法不改變客觀市場數值。"
                          if bias_conflict else
                          "低風險偏好遇到跨週期不確定性；可評估降低曝險，不指定減倉比例。")
                candidates.append(_candidate(position, "reduce_exposure_review", reason))
        default_id = candidates[-1]["id"] if len(candidates) == 1 else next(
            (candidate["id"] for candidate in candidates if
             candidate["kind"] == "reduce_exposure_review"), candidates[0]["id"])
        exposure = stop_exposure(position, quote, account_equity_usdt)
        result.append({"position_id": position_id, "version": position["version"],
                       "candidates": candidates, "default_action_id": default_id} |
                      (exposure or {}))
    return {"version": VERSION, "quote_time": quote["observed_at"],
            "valuation_price": str(price),
            "valuation_price_type": "mark" if quote.get("mark_price") else "last_trade",
            "market_state": market_state, "positions": result}
