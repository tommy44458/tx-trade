"""Deterministic, explicitly hypothetical execution costs for linear perps."""

import os
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

VERSION = "cost_scenario_v1"
DEFAULT_FEE_BPS = "5"
DEFAULT_SLIPPAGE_BPS = "2"


def _rate(value: str, name: str) -> Decimal:
    try:
        rate = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be between 0 and 100 basis points") from exc
    if not rate.is_finite() or not 0 <= rate <= 100:
        raise ValueError(f"{name} must be between 0 and 100 basis points")
    return rate


def configured_cost_scenario() -> dict:
    fee = _rate(os.getenv("TRADE_SCENARIO_FEE_BPS", DEFAULT_FEE_BPS), "Fee")
    slippage = _rate(os.getenv("TRADE_SCENARIO_SLIPPAGE_BPS", DEFAULT_SLIPPAGE_BPS), "Slippage")
    return {"version": VERSION, "fee_bps_per_fill": str(fee),
            "slippage_bps_per_fill": str(slippage),
            "source": "illustrative_not_account_specific",
            "fill_assumption": "same_fee_and_adverse_slippage_on_entry_and_exit",
            "funding_included": False}


def _scenario(quote: dict) -> tuple[dict, Decimal, Decimal]:
    assumptions = quote["cost_scenario"] if "cost_scenario" in quote else configured_cost_scenario()
    if not isinstance(assumptions, dict):
        raise TypeError("Unknown cost scenario")
    if (assumptions.get("version") != VERSION or
            assumptions.get("source") != "illustrative_not_account_specific" or
            assumptions.get("fill_assumption") != "same_fee_and_adverse_slippage_on_entry_and_exit" or
            assumptions.get("funding_included") is not False):
        raise ValueError("Unknown cost scenario")
    fee = _rate(assumptions["fee_bps_per_fill"], "Fee") / 10_000
    slippage = _rate(assumptions["slippage_bps_per_fill"], "Slippage") / 10_000
    return assumptions, fee, slippage


def validate_cost_scenario(assumptions: dict) -> None:
    _scenario({"cost_scenario": assumptions})


def round_stop_outward(stop: Decimal, tick: Decimal, side: str) -> Decimal:
    if side not in {"long", "short"} or not tick.is_finite() or tick <= 0:
        raise ValueError("Invalid exchange tick size")
    rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
    return (stop / tick).to_integral_value(rounding=rounding) * tick


def costed_risk(side: str, entry: Decimal, stop: Decimal, target: Decimal,
                leverage: int, quote: dict) -> dict:
    """One-unit result; fees use executed notional, and leverage only changes margin return."""
    if (side not in {"long", "short"} or not isinstance(leverage, int)
            or isinstance(leverage, bool) or not 1 <= leverage <= 125):
        raise ValueError("Invalid direction or leverage")
    if not all(value.is_finite() and value > 0 for value in (entry, stop, target)):
        raise ValueError("Risk prices must be positive and finite")
    if not (stop < entry < target if side == "long" else target < entry < stop):
        raise ValueError("Entry, stop and target have the wrong direction")
    assumptions, fee, slippage = _scenario(quote)
    sign = Decimal(1) if side == "long" else Decimal(-1)
    entry_fill = entry * (1 + sign * slippage)
    stop_fill = stop * (1 - sign * slippage)
    target_fill = target * (1 - sign * slippage)
    gross_reward = sign * (target - entry)
    gross_loss = sign * (entry - stop)
    target_fees = fee * (entry_fill + target_fill)
    stop_fees = fee * (entry_fill + stop_fill)
    net_reward = sign * (target_fill - entry_fill) - target_fees
    net_loss = sign * (entry_fill - stop_fill) + stop_fees
    if gross_loss <= 0 or net_loss <= 0:
        raise ValueError("Invalid stop risk")
    return {"version": VERSION, "cost_assumptions": assumptions,
            "entry_fill": str(entry_fill), "stop_fill": str(stop_fill),
            "target_fill": str(target_fill),
            "gross_reward_per_unit_usdt": str(gross_reward),
            "gross_loss_per_unit_usdt": str(gross_loss),
            "net_reward_per_unit_usdt": str(net_reward),
            "net_loss_per_unit_usdt": str(net_loss),
            "gross_risk_reward": str((gross_reward / gross_loss).quantize(Decimal("0.01"))),
            "net_risk_reward": str((net_reward / net_loss).quantize(Decimal("0.01"))) if net_reward > 0 else None,
            "risk_on_theoretical_margin_pct": str((net_loss / (entry / leverage) * 100).quantize(Decimal("0.01"))),
            "quantity_assumption": "one_base_unit", "liquidation_price": None}
