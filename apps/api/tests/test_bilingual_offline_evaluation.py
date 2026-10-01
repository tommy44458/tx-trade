import pytest

from trade_helper.position_reasoning import validate_position_reason
from trade_helper.reasoning_evidence import validate_reason_numbers


@pytest.mark.parametrize('reason', [
    '現價反彈自 1H 支撐 118.26–118.60。',
    'Price is rebounding from 1H support at $118.26–$118.60.',
])
def test_offline_level_check_recognizes_both_languages(reason):
    levels = [{'kind': 'support', 'low': '118.26', 'high': '118.60', 'timeframe': '1h'}]
    validate_position_reason(reason, levels, primary_timeframe='1h')


def test_english_offline_level_check_rejects_an_unverified_zone():
    with pytest.raises(ValueError, match='verified price zone'):
        validate_position_reason('Price is rebounding from 1H support at 117–118.',
            [{'kind': 'support', 'low': '118.26', 'high': '118.60'}], primary_timeframe='1h')


@pytest.mark.parametrize('reason', ['OI 下跌 0.21%。', 'OI is down 0.21%.', 'OI declined by 0.21%.'])
def test_offline_decline_words_use_the_same_signed_value(reason):
    validate_reason_numbers(reason, [{'tool': 'open_interest', 'result': {'change_pct': '-0.21'}}])


def test_offline_english_indicator_claim_cannot_swap_timeframes():
    trace = [{'tool': 'technical_snapshot', 'result': {'timeframes': {
        '1h': {'metrics': {'ma20': '100'}}, '4h': {'metrics': {'ma20': '95'}},
    }}}]
    validate_reason_numbers('1H MA20 is 100.', trace)
    with pytest.raises(ValueError):
        validate_reason_numbers('1H MA20 is 95.', trace)


def test_offline_english_win_rate_cannot_reuse_a_verified_rsi_as_a_probability():
    with pytest.raises(ValueError, match='probability'):
        validate_reason_numbers('The win rate is 70%.', [{'tool': 'rsi', 'result': {'value': '70'}}])
