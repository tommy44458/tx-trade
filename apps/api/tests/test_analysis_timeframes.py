import pytest
from pydantic import ValidationError

from trade_helper import local_settings
from trade_helper.models import AnalysisRequest


@pytest.mark.parametrize('timeframe', ['1h', '4h', '12h', '1d'])
@pytest.mark.parametrize('kind', ['market', 'positions'])
def test_market_and_position_requests_accept_each_main_timeframe(timeframe, kind):
    request = AnalysisRequest(
        kind=kind, market_id='binance:perp:BTCUSDT', timeframe=timeframe,
        position_ids=['position_1'] if kind == 'positions' else [],
    )
    assert request.model_dump()['timeframe'] == timeframe


@pytest.mark.parametrize('timeframe', ['3d', '1w', '1M', '1m', '30d', '12H', None])
def test_auxiliary_and_minute_frames_cannot_be_submitted_as_main(timeframe):
    with pytest.raises(ValidationError):
        AnalysisRequest(market_id='binance:perp:BTCUSDT', timeframe=timeframe)
    with pytest.raises(ValidationError):
        local_settings.TradingPreferencesUpdate(timeframe=timeframe)


@pytest.mark.parametrize('timeframe', ['12h', '1d'])
def test_new_timeframe_preference_persists_without_replacing_other_settings(timeframe):
    local_settings.patch_preferences({
        'favorite_market_ids': ['binance:perp:SOLUSDT'],
        'trading_preferences': {'timeframe': timeframe, 'leverage': 73,
                                'risk_tolerance': 'high', 'trading_style': 'left'},
    })
    saved = local_settings.trading_preferences()
    assert saved['timeframe'] == timeframe
    assert saved['leverage'] == 73
    assert saved['risk_tolerance'] == 'high'
    assert saved['trading_style'] == 'left'
    assert local_settings.favorite_market_ids() == ['binance:perp:SOLUSDT']
