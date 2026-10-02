import json

from trade_helper.analysis import hypothesis_assessment
from trade_helper.model_report import read_model_report

ASSESSMENT = {"long": {"verdict": "unsuitable", "reason": "Price is rejecting the 4H resistance with no room."},
              "short": {"verdict": "conditional", "reason": "Only after a close below the 1H support."}}


def read(direction_assessment):
    raw = json.dumps({"market": "m", "strategy": "s", "agent_stance": "wait",
                      "direction_assessment": direction_assessment})
    return read_model_report(raw, [])["direction_assessment"]


def test_both_directions_are_read_from_the_model_report():
    assert read(ASSESSMENT) == ASSESSMENT


def test_invalid_or_missing_direction_verdicts_are_dropped_not_invented():
    assert read(None) is None
    assert read("long is fine") is None
    assert read({"long": {"verdict": "great", "reason": "x"}, "short": {"verdict": "unsuitable", "reason": " "}}) is None
    assert read({"long": ASSESSMENT["long"], "sideways": ASSESSMENT["short"]}) == {"long": ASSESSMENT["long"]}


def test_the_users_hypothesis_is_matched_to_the_blind_verdict_for_that_side():
    assert hypothesis_assessment("bullish", ASSESSMENT) == {"side": "long", **ASSESSMENT["long"]}
    assert hypothesis_assessment("bearish", ASSESSMENT) == {"side": "short", **ASSESSMENT["short"]}
    assert hypothesis_assessment(None, ASSESSMENT) is None
    assert hypothesis_assessment("bullish", None) is None
    assert hypothesis_assessment("bearish", {"long": ASSESSMENT["long"]}) is None
