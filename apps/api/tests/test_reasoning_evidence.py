import pytest

from trade_helper.reasoning_evidence import validate_reason_numbers


def trace():
    return [{'tool': 'technical_snapshot', 'result': {'timeframes': {
        '1h': {'metrics': {'ma20': '60123.456'}, 'indicators': {'rsi': {'value': '42.1234'}}},
        '4h': {'metrics': {'ma20': '61123.456'}, 'indicators': {'rsi': {'value': '48.8'}}},
    }}}, {'tool': 'support_resistance', 'result': {'levels': [{'low': '60000', 'high': '60300'}]}}]


def test_verified_values_and_chinese_adjacent_labels_are_allowed():
    validate_reason_numbers('現價60,120靠近支撐60,000～60,300；1H的RSI14為42.12。4H MA20為61,123.46。',
                            trace(), {'price': '60120'})


@pytest.mark.parametrize('text', ['現價99999', '預估勝率60%', '4H RSI為42.12', '4H的RSI14為42.12', '1H MA20為61123.46'])
def test_fabrication_probability_and_wrong_timeframe_rejected(text):
    with pytest.raises(ValueError):
        validate_reason_numbers(text, trace(), {'price': '60120', 'count': 60})


def test_timestamps_do_not_become_price_evidence():
    with pytest.raises(ValueError):
        validate_reason_numbers('支撐為123456789', [{'result': {'timestamp': 123456789}}])


def test_verified_source_clock_can_explain_a_crossing_without_becoming_a_price():
    runs = [{'tool': 'support_resistance', 'result': {
        'break_evidence': [{'at': '2026-09-30T12:59:59.999000+00:00', 'close': '106'},
                           {'at': '2026-09-30T13:59:59.999000+00:00', 'close': '107'}]}}]
    validate_reason_numbers('12:59及13:59兩根收盘為106、107。', runs)
    with pytest.raises(ValueError, match='validated numeric'):
        validate_reason_numbers('支撐價格為59。', runs)
    with pytest.raises(ValueError, match='unverified time'):
        validate_reason_numbers('14:59已收盤。', runs)
    with pytest.raises(ValueError, match='unverified time'):
        validate_reason_numbers('14:59已收盤。', runs, extra={'entry_plan': {'observed_at': '2026-09-30T14:59:00+00:00'}})


def test_called_additional_indicator_keeps_its_timeframe_and_period():
    runs = trace() + [{'tool': 'rsi', 'parameters': {'timeframe': '4h', 'period': 7},
                      'result': {'value': '30.12'}}]
    validate_reason_numbers('4H RSI7為30.12', runs)
    with pytest.raises(ValueError):
        validate_reason_numbers('1H RSI7為30.12', runs)


def test_validated_agent_plan_is_allowed_without_python_candidate():
    validate_reason_numbers('建議止損59900、目標61500', trace(),
                            extra={'entry_plan': {'stop_loss': '59900', 'take_profit': '61500'}})


def test_decline_percentage_can_use_verified_negative_magnitude_only():
    evidence = [{'tool': 'derivatives_context', 'result': {'change_pct': '-0.21'}}]
    validate_reason_numbers('OI 二十四小時下降0.21%。', evidence)
    for text in ('OI 二十四小時增加0.21%。', '價格是0.21。', 'OI下降0.22%。'):
        with pytest.raises(ValueError, match='validated numeric'):
            validate_reason_numbers(text, evidence)


@pytest.mark.parametrize("timeframe,label", [("12h", "12H"), ("1d", "1D"),
                                            ("3d", "3D"), ("1w", "1W"), ("1M", "1M")])
def test_larger_frame_labels_are_not_prices_and_keep_their_own_indicator(timeframe, label):
    runs = trace()
    runs[0]["result"]["timeframes"][timeframe] = {
        "metrics": {"ma20": "65001.789"}, "indicators": {"rsi": {"value": "31.2345"}}}
    validate_reason_numbers(f"{label}的RSI14為31.23，{label} MA20為65,001.79。", runs)
    with pytest.raises(ValueError, match="wrong indicator or timeframe"):
        validate_reason_numbers(f"{label} RSI14為42.12。", runs)
    with pytest.raises(ValueError, match="wrong indicator or timeframe"):
        validate_reason_numbers("1H RSI14為31.23。", runs)


def test_monthly_indicator_uses_full_snapshot_before_repeated_background_summary():
    runs = trace()
    runs[0]["result"]["timeframes"]["1M"] = {
        "metrics": {"ma20": "65001.789"}, "indicators": {"rsi": {"value": "31.2345"}}}
    runs[0]["result"]["higher_timeframe_context"] = {"timeframes": {"1M": {
        "metrics": {"ma20": None}, "indicators": {"rsi": {"status": "unavailable"}}}}}
    validate_reason_numbers("1M RSI14為31.23。", runs)
