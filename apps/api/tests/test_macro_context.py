import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.agent import _final_reasoning
from trade_helper.macro_context import build_macro_context


def test_monthly_macro_context_uses_only_as_of_verified_rate_result():
    cutoff = datetime(2026, 9, 29, tzinfo=UTC)
    published = cutoff - timedelta(days=10)
    event = {
        "id": "event-fed", "source": "fed", "kind": "fomc",
        "official_result": {
            "decision": "reduce", "lower_pct": "3.25", "upper_pct": "3.5",
            "published_at": published.isoformat(), "ingested_at": published.isoformat(),
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260919a.htm",
            "document_id": "release-1",
        },
    }
    news = {"evidence_pack": {"events": [{
        "event_id": "publication-1", "published_at": published.isoformat(),
        "citation": {"title": "FOMC statement", "source_url": event["official_result"]["source_url"]},
    }]}}
    context = build_macro_context(cutoff, {"events": [event]}, news)
    assert context["coverage"] == "partial"
    assert context["directional_evidence"][0]["id"] == "fomc:event-fed"
    assert context["official_publications"][0]["claim_limit"] == "publication_timing_only"
    event["official_result"]["ingested_at"] = (cutoff + timedelta(seconds=1)).isoformat()
    assert build_macro_context(cutoff, {"events": [event]}, news)["directional_evidence"] == []


def test_agent_cannot_claim_direction_from_publication_timing_alone():
    cutoff = datetime(2026, 9, 29, tzinfo=UTC)
    context = build_macro_context(cutoff, None, {"evidence_pack": {"events": []}})
    trace = [{"tool": "support_resistance"}, {"tool": "strategy_candidates", "result": {"candidates": []}}]
    report = {
        "market": "主週期方向尚不明確。", "levels": "區間僅供觀察。",
        "strategy": "暫時觀望。", "supporting_evidence": "方向證據不足。",
        "counter_evidence": "若新資料顯示方向，需改看法。",
        "evidence_tools": ["support_resistance", "strategy_candidates"],
        "strategy_decision": "wait", "agent_stance": "wait", "position_decisions": {},
        "entry_decision": {"action": "stand_aside", "side": None, "entry_price": None, "stop_loss": None, "take_profit": None, "trigger": None, "invalidation": None, "reason": "目前先觀望，沒有完整價位方案。", "basis_level_ids": []},
        "macro_outlook": {"stance": "bullish", "reason": "公告發布後偏多。", "evidence_ids": []},
    }
    with pytest.raises(ValueError, match="macro outlook"):
        _final_reasoning(json.dumps(report), trace, require_detail=True, macro_context=context)
    report["macro_outlook"] = {
        "stance": "neutral", "reason": "沒有可核對的實際數據，暫判中性。", "evidence_ids": []}
    assert _final_reasoning(json.dumps(report), trace, require_detail=True,
                            macro_context=context)["macro_outlook"]["stance"] == "neutral"



def test_agent_can_cite_verified_rate_result_for_macro_direction():
    context = {"directional_evidence": [{"id": "fomc:event-fed"}]}
    trace = [{"tool": "support_resistance"}, {"tool": "strategy_candidates", "result": {"candidates": []}}]
    report = {
        "market": "市場方向混合。", "levels": "價格接近阻力區。",
        "strategy": "現在偏多但留意阻力。", "supporting_evidence": "盤中買盤較強。",
        "counter_evidence": "若失守支撐，偏多判斷需調整。",
        "evidence_tools": ["support_resistance", "strategy_candidates"],
        "strategy_decision": "wait", "agent_stance": "long", "position_decisions": {},
        "entry_decision": {"action": "stand_aside", "side": None, "entry_price": None, "stop_loss": None, "take_profit": None, "trigger": None, "invalidation": None, "reason": "目前先觀望，沒有完整價位方案。", "basis_level_ids": []},
        "macro_outlook": {"stance": "bullish", "reason": "已核對的利率決策可能改善風險偏好。",
                          "evidence_ids": ["fomc:event-fed"]},
    }
    report["macro_outlook"]["reason"] = "已核對的目標區間上限為 3.5%，可作宏觀背景。"
    result = _final_reasoning(json.dumps(report), trace, require_detail=True,
                              macro_context=context)
    assert result["macro_outlook"]["stance"] == "bullish"
    report["macro_outlook"]["evidence_ids"] = ["unverified"]
    with pytest.raises(ValueError, match="macro outlook"):
        _final_reasoning(json.dumps(report), trace, require_detail=True,
                         macro_context=context)
