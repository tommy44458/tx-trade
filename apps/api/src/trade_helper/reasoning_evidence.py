"""Offline checks of bilingual numeric explanations against frozen evidence."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_TIMEFRAME_LABEL = r"(?:(?:12|[14])\s*[Hh]|[13]\s*[Dd]|1\s*[Ww]|(?-i:1\s*M))"
_LABELS = re.compile(r"(?<![A-Za-z0-9])(?:(?:MA|EMA|ATR|RSI)\s*\d+|" +
                     _TIMEFRAME_LABEL + r"|v\d+)(?![A-Za-z0-9])", re.IGNORECASE)
_CLOCK = re.compile(r"(?<![A-Za-z0-9_.])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?![0-9_.])")
_DECLINE_PERCENT = re.compile(
    r"(?:下跌|回落|跌幅|下降|減少|降低|減幅|下滑|\b(?:down|fell|fallen|declined?|dropped?|decreased?)\b)"
    r"\s*(?:約|為|是|達|by|about|approximately)?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_SKIP_KEYS = {"id", "position_id", "market_id", "version", "method", "source_time", "source_url",
              "market_snapshot_sha256", "reason", "title", "created_at", "confirmed_at",
              "as_of", "source", "parameters", "open_time", "close_time", "timestamp", "observations"}


def _numbers(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in _SKIP_KEYS and not key.endswith(("_at", "_time", "_version", "_hash")):
                yield from _numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)
    elif not isinstance(value, bool) and isinstance(value, (int, float, Decimal, str)):
        try:
            number = Decimal(str(value))
            if number.is_finite():
                yield number
        except InvalidOperation:
            pass


def _verified_clocks(value) -> set[str]:
    """Recognize source clock strings without treating timestamp digits as prices."""
    clocks = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (key.endswith(("_at", "_time")) or key in {"at", "as_of", "timestamp"}):
                try:
                    observed = datetime.fromisoformat(item)
                except ValueError:
                    continue
                if observed.tzinfo is not None:
                    clocks.update({observed.strftime("%H:%M"), observed.strftime("%H:%M:%S")})
            elif isinstance(item, (dict, list)):
                clocks.update(_verified_clocks(item))
    elif isinstance(value, list):
        for item in value:
            clocks.update(_verified_clocks(item))
    return clocks


def validate_reason_numbers(text: str, trace: list[dict], quote: dict | None = None,
                            extra: dict | None = None) -> None:
    """Validate supplied values and ordinary rounding; never invent a win probability."""
    allowed = set()
    for run in trace:
        if run.get('tool') == 'support_resistance':
            levels = run.get('result', {}).get('levels', [])
            allowed.update(Decimal(sum(level.get('kind') == kind for level in levels))
                           for kind in ('support', 'resistance'))
    for number in _numbers({"quote": quote or {}, "tools": [x.get("result", {}) for x in trace],
                            "extra": extra or {}}):
        allowed.add(number)
        for places in range(9):
            allowed.add(round(number, places))
        allowed.add(Decimal(format(number, '.12g')))
    stripped = _LABELS.sub('', text)
    clocks = _verified_clocks({"quote": quote or {}, "tools": [x.get("result", {}) for x in trace]})
    def checked_clock(match):
        normalized = ':'.join(f'{int(part):02}' for part in match.group().split(':'))
        if normalized not in clocks:
            raise ValueError("AI explanation references an unverified time")
        return ''
    stripped = _CLOCK.sub(checked_clock, stripped)
    decline_spans = {m.start(1) for m in _DECLINE_PERCENT.finditer(stripped)}
    for match in _NUMBER.finditer(stripped):
        written = Decimal(match.group().replace(',', ''))
        # "OI 下降 0.21%" expresses the supplied signed value -0.21%.
        # Only a decline percentage can use this form; unrelated positive
        # values and increases still require their own verified evidence.
        if written not in allowed and not (match.start() in decline_spans and -written in allowed):
            raise ValueError("AI explanation must use validated numeric report fields")
    if re.search(r"(?:勝率|反轉機率|獲利機率|\bwin[ -]?rate\b|\breversal probability\b|"
                 r"\bprofit probability\b)[^。；\n]{0,24}\d", text, re.IGNORECASE):
        raise ValueError("AI explanation may not invent a trading probability")
    # Prevent a verified value in one candle interval from being attributed to another.
    snapshots = [x.get("result", {}) for x in trace if x.get("tool") == "technical_snapshot"]
    if not snapshots:
        return
    snapshot = snapshots[-1]
    pattern = r"(" + _TIMEFRAME_LABEL + r")\s*(?:的\s*)?(MA20|MA50|EMA20|EMA50|RSI\d*|ATR\d*)\s*(?:為|是|=|：|:|約|is|at|about)\s*([-+]?\d+(?:,\d{3})*(?:\.\d+)?)"
    for match in re.finditer(pattern, text, re.IGNORECASE):
        frame, label, written = match.groups()
        label = label.upper()
        frame = re.sub(r'\s', '', frame)
        frame = frame if frame == "1M" else frame.lower()
        data = snapshot.get("timeframes", {}).get(frame) or snapshot.get(
            "higher_timeframe_context", {}).get("timeframes", {}).get(frame, {})
        indicators = data.get("indicators", {})
        actuals = []
        if label in {'MA20', 'MA50'}:
            actuals.append(data.get('metrics', {}).get(label.lower()))
        elif label.startswith('EMA'):
            actuals.append(indicators.get('trend_ema', {}).get(label.lower()))
        else:
            tool, key = ('rsi', 'value') if label.startswith('RSI') else ('volatility_atr', 'atr')
            suffix = re.search(r'\d+', label)
            period = int(suffix.group()) if suffix else None
            default = indicators.get(tool, {})
            if period in {None, default.get('period', 14)}:
                actuals.append(default.get(key))
            for run in trace:
                parameters = run.get('parameters', {})
                if (run.get('tool') == tool and parameters.get('timeframe') == frame
                        and period in {None, parameters.get('period')}):
                    actuals.append(run.get('result', {}).get(key))
        variants = set()
        for actual in actuals:
            if actual is not None:
                number = Decimal(str(actual))
                variants.update({number, Decimal(format(number, '.12g')),
                                 *(round(number, n) for n in range(9))})
        if Decimal(written.replace(',', '')) not in variants:
            raise ValueError("AI explanation attributes a number to the wrong indicator or timeframe")
