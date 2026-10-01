from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_helper.agent import _final_reasoning, fallback_analysis
from trade_helper.analysis import build_report
from trade_helper.report_contract import validate_report

from .test_analysis import sample_candles
from .test_follow_up import detailed_explanation


def test_agent_plan_prices_in_plain_reason_pass_full_report():
    import json

    request = {'market_id': 'binance:perp:BTCUSDT', 'timeframe': '1h', 'risk_tolerance': 'high', 'leverage': 5}
    candles = sample_candles(recent=True)
    quote = {'price': candles[-1]['close'], 'tick_size': '0.1', 'snapshot_hash': 'a' * 64,
             'observed_at': datetime.now(UTC).isoformat()}
    base = fallback_analysis(request, candles, quote)
    trace = base['tool_trace']
    candidate = next(item['result'] for item in trace if item['tool'] == 'strategy_candidates')
    entry = Decimal(quote['price'])
    plan = {'action': 'open_now', 'side': 'short', 'entry_price': str(entry),
            'stop_loss': str(entry + 2), 'take_profit': str(entry - 5),
            'reason': '现價結構支持開空。', 'trigger': '以現價進場。',
            'invalidation': '向上突破原結構。', 'basis_level_ids': []}
    raw = {'market': f'現價為{entry}。', 'levels': '確認區間只供阻力參考。',
           'strategy': f'現在考慮開空，建議止損{entry + 2}、目標{entry - 5}。',
           'strategy_decision': 'wait', 'evidence_tools': ['support_resistance', 'strategy_candidates'],
           **detailed_explanation(candidate), 'agent_stance': 'short', 'entry_decision': plan}
    reasoning = _final_reasoning(json.dumps(raw), trace, require_detail=True, quote=quote)
    market_null = _final_reasoning(json.dumps(raw | {'position_decisions': None}), trace,
                                   require_detail=True, quote=quote)
    assert market_null['position_decisions'] == {}
    with pytest.raises(ValueError, match='cover selected'):
        _final_reasoning(json.dumps(raw | {'position_decisions': {'made-up': {}}}), trace,
                         require_detail=True, quote=quote)
    base.update(mode='openai_assisted', agent_stance='short', reasoning=reasoning, position_decisions={})
    report = build_report(request, candles, quote, [], base)
    report['reasoning']['market'] = '現價為99999。'
    with pytest.raises(ValueError, match='validated numeric'):
        validate_report(report)
