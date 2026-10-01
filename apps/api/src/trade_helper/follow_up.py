"""Deterministic observation triggers, never entry orders."""
from decimal import Decimal

from .strategy_engine import next_close


def build_follow_up_plan(levels: list[dict], quote: dict, timeframe: str,
                         last_close: str) -> list[dict]:
    price = Decimal(quote["price"])
    plan = []
    for kind, label in (("support", "支撐"), ("resistance", "壓力")):
        zones = [zone for zone in levels if zone["kind"] == kind]
        if not zones:
            continue
        zone = min(zones, key=lambda item: (
            max(Decimal(item["low"]) - price, price - Decimal(item["high"]), Decimal(0)),
            str(item.get("id", ""))))
        inside = Decimal(zone["low"]) <= price <= Decimal(zone["high"])
        plan.append({
            "id": "retest_" + kind, "kind": "price_zone",
            "title": label + "區再次評估",
            "level_id": zone["id"], "low": zone["low"], "high": zone["high"],
            "snapshot_inside_zone": inside,
            "condition": ("分析時價格已在此區間；可核對最新報價後再次分析" if inside else
                          "價格進入此區間時，再按下分析"),
            "action": ("檢查是否收回區間、量能是否改變，並重新核對另一週期方向、"
                       "有效止損距離與成本後風報比。到價只代表重新評估，不代表進場。"),
            "invalidation": ("若價格直接穿透區間，重新分析區間是否失效；"
                             "不要沿用本次的支撐壓力判斷。"),
            "source_tool": "support_resistance",
        })
    plan.append({
        "id": "next_close", "kind": "candle_close", "title": "下一根收盤後更新",
        "time": next_close(last_close, timeframe),
        "condition": "所選週期下一根 K 線收盤後，再按下分析",
        "action": "用新收盤資料核對趨勢、區間生命週期與候選觸發條件；若資料不足，繼續等待。",
        "invalidation": "報告到期或重大事件、部位保護條件改變時，原建議也需要重新評估。",
        "source_tool": "strategy_candidates",
    })
    return plan
