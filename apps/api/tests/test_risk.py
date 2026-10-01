from decimal import Decimal

import pytest

from trade_helper.risk import costed_risk, round_stop_outward, validate_cost_scenario

SCENARIO = {"cost_scenario": {"version": "cost_scenario_v1",
                              "fee_bps_per_fill": "5", "slippage_bps_per_fill": "2",
                              "source": "illustrative_not_account_specific",
                              "fill_assumption": "same_fee_and_adverse_slippage_on_entry_and_exit",
                              "funding_included": False}}


@pytest.mark.parametrize(("side", "stop", "target", "fills", "net_reward", "net_loss"), [
    ("long", "95", "110", ("100.0200", "94.9810", "109.9780"), "9.85300100", "5.13650050"),
    ("short", "105", "90", ("99.9800", "105.0210", "90.0180"), "9.86700100", "5.14350050"),
])
def test_costed_risk_uses_adverse_fills_and_executed_notional_fees(side, stop, target,
                                                                     fills, net_reward, net_loss):
    result = costed_risk(side, Decimal(100), Decimal(stop), Decimal(target), 5, SCENARIO)
    assert tuple(Decimal(result[key]) for key in ("entry_fill", "stop_fill", "target_fill")) == tuple(map(Decimal, fills))
    assert Decimal(result["net_reward_per_unit_usdt"]) == Decimal(net_reward)
    assert Decimal(result["net_loss_per_unit_usdt"]) == Decimal(net_loss)
    assert result["liquidation_price"] is None


def test_leverage_changes_only_theoretical_margin_risk():
    low = costed_risk("long", Decimal(100), Decimal(95), Decimal(110), 2, SCENARIO)
    high = costed_risk("long", Decimal(100), Decimal(95), Decimal(110), 10, SCENARIO)
    for key in ("net_reward_per_unit_usdt", "net_loss_per_unit_usdt", "net_risk_reward"):
        assert low[key] == high[key]
    assert abs(Decimal(high["risk_on_theoretical_margin_pct"]) - 5 * Decimal(low["risk_on_theoretical_margin_pct"])) <= Decimal("0.02")


def test_stop_rounds_away_from_entry_and_wrong_direction_rejected():
    assert round_stop_outward(Decimal("94.94"), Decimal("0.1"), "long") == Decimal("94.9")
    assert round_stop_outward(Decimal("105.01"), Decimal("0.1"), "short") == Decimal("105.1")
    with pytest.raises(ValueError, match="wrong direction"):
        costed_risk("short", Decimal(100), Decimal(95), Decimal(90), 5, SCENARIO)


def test_missing_or_unbounded_cost_assumptions_are_rejected():
    with pytest.raises(ValueError, match="Unknown cost scenario"):
        validate_cost_scenario({})
    with pytest.raises(ValueError, match="between 0 and 100"):
        validate_cost_scenario(SCENARIO["cost_scenario"] | {"fee_bps_per_fill": "101"})
