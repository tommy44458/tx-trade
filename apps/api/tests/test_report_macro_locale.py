from copy import deepcopy

from trade_helper.analysis import _reported_macro_outlook


def saved_macro():
    return {"status": "succeeded", "stale": False, "interpretation": {
        "id": "macro_original", "dataset_version": "unchanged_evidence",
        "outlook": {"stance": "neutral", "summary": "通膨降低，但就業資料仍不完整。"},
        "drivers": [{"evidence_ids": ["actual:cpi:2026-08"]}],
    }}


def test_english_report_retains_model_translation_and_saved_economic_decision():
    state = saved_macro()
    original = deepcopy(state)
    result = _reported_macro_outlook(state, {
        "stance": "neutral", "reason": "Inflation is easing, but employment coverage is incomplete.",
        "evidence_ids": ["invented"], "interpretation_id": "wrong_id",
    }, "en-US")
    assert result["reason"] == "Inflation is easing, but employment coverage is incomplete."
    assert result["stance"] == "neutral"
    assert result["evidence_ids"] == ["actual:cpi:2026-08"]
    assert result["interpretation_id"] == "macro_original"
    assert state == original


def test_same_language_and_legacy_reports_keep_saved_original_summary():
    state = saved_macro()
    result = _reported_macro_outlook(state, {"stance": "neutral", "reason": "不同摘要"}, "zh-TW")
    assert result["reason"] == state["interpretation"]["outlook"]["summary"]


def test_translation_does_not_introduce_a_different_macro_direction():
    state = saved_macro()
    result = _reported_macro_outlook(state, {"stance": "bullish", "reason": "A new bullish view."}, "en-US")
    assert result["stance"] == "neutral"
    assert result["reason"] == state["interpretation"]["outlook"]["summary"]


def test_missing_or_stale_macro_is_not_replaced_by_model_translation():
    assert _reported_macro_outlook(None, {"stance": "bullish", "reason": "Guess"}, "en-US") is None
    state = saved_macro() | {"stale": True}
    assert _reported_macro_outlook(state, {"stance": "neutral", "reason": "Translation"}, "en-US") is None
