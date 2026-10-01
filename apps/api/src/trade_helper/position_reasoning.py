"""Offline evaluation of price citations in Chinese and English decisions.

These helpers are not a production strategy/tool validation gate.
"""

import re
from decimal import Decimal, InvalidOperation

_PRICE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:,\d{3})*(?:\.\d+)?(?!\d|\.\d)")
_FRAME = re.compile(r"(?<![A-Za-z0-9])(?:(?:12|[14])\s*[Hh]|[13]\s*[Dd]|1\s*[Ww]|1\s*M)(?![A-Za-z0-9])")
_FORBIDDEN = ("止損", "止盈", "BingX", "Binance", "跨合約", "跨市場", "交易所不同", "無保護", "未設保護", "報價來源")
_ENGLISH_FORBIDDEN = ("stop-loss", "stop loss", "take-profit", "take profit", "cross-venue",
                      "cross-contract", "unprotected")
_CLAIM = re.compile(r"支撐|壓力|\bsupport\b|\bresistance\b", re.IGNORECASE)


def validate_position_reason(reason: str, levels: list[dict], *,
                             primary_timeframe: str | None = None,
                             secondary_context: dict | None = None) -> None:
    """A level claim must give both bounds of a verified zone of that kind."""
    if any(term.casefold() in reason.casefold() for term in _FORBIDDEN + _ENGLISH_FORBIDDEN):
        raise ValueError("AI position reason includes excluded risk or venue claims")
    primary = primary_timeframe or next((z["timeframe"] for z in levels if z.get("timeframe")), None)
    context = secondary_context or {}
    secondary = context.get("timeframe")
    cited = {}
    for clause in re.split(r"[。；;\n]|\.(?=\s+[A-Z])", reason):
        claims = list(_CLAIM.finditer(clause))
        for index, claim in enumerate(claims):
            end = claims[index + 1].start() if index + 1 < len(claims) else len(clause)
            segment = clause[claim.start():end] if len(claims) > 1 else clause
            frames = list(_FRAME.finditer(clause[:claim.start()]))
            timeframe = re.sub(r"\s", "", frames[-1].group()) if frames else primary
            if timeframe and timeframe != "1M":
                timeframe = timeframe.lower()
            available = levels
            if timeframe != primary:
                available = context.get("data", {}).get("levels", []) if timeframe == secondary else []
            try:
                mentioned = {Decimal(m.group().replace(",", "")) for m in _PRICE.finditer(segment)}
            except InvalidOperation as exc:
                raise ValueError("AI position reason has an invalid price") from exc
            kind = "support" if claim.group().casefold() in {"支撐", "support"} else "resistance"
            matches = [zone for zone in available if
                zone.get("kind") == kind and zone.get("zone_state", "active") == "active"
                and Decimal(str(zone["low"])) in mentioned
                and Decimal(str(zone["high"])) in mentioned
            ]
            key = (timeframe, kind)
            if matches:
                cited.setdefault(key, []).extend(matches)
            elif not (re.search(r"上界|下界|上緣|下緣|高價|低價|\b(?:upper|lower)\s+(?:bound|edge)\b",
                                segment, re.IGNORECASE) and any(
                    Decimal(str(z["low"])) in mentioned or Decimal(str(z["high"])) in mentioned
                    for z in cited.get(key, []))):
                raise ValueError("AI position level claim lacks a verified price zone")
