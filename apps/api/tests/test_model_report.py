import json

from trade_helper.model_report import entry_cost_reference, read_model_report


def test_free_text_and_fenced_json_remain_readable():
    raw = '目前先觀望，價格接近前高，等待反應。'
    result = read_model_report(raw, [])
    assert result['strategy'] == raw
    assert result['agent_stance'] is None and result['entry_decision'] is None
    assert read_model_report('```json\n'+json.dumps({'strategy': raw})+'\n```', [])['strategy'] == raw


def test_missing_fields_are_not_fabricated_and_positions_are_not_semantically_filtered():
    result = read_model_report(json.dumps({'entry_decision': {'action': 'open_now'},
        'position_decisions': {'p1': {'decision': 'hold', 'reason': '從支撐119.2反彈，續抱。'}}}), [])
    assert result['entry_decision']['entry_price'] is None
    assert result['entry_decision']['trigger'] is None
    assert result['position_decisions']['p1']['reason'] == '從支撐119.2反彈，續抱。'
    assert entry_cost_reference(result['entry_decision'], {'price': '120'}, 5) is None


def test_incoherent_cost_inputs_do_not_block_strategy_text():
    result = read_model_report(json.dumps({'strategy': '保留 Agent 建議。', 'entry_decision': {
        'action': 'open_now', 'side': 'short', 'entry_price': 120, 'stop_loss': 110, 'take_profit': 125}}), [])
    assert result['strategy'] == '保留 Agent 建議。'
    assert entry_cost_reference(result['entry_decision'], {'price': '120'}, 5) is None
