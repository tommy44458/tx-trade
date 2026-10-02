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


def test_a_report_missing_its_final_brace_is_still_read():
    complete = json.dumps({"agent_stance": "wait", "strategy": "Wait for a retest {of 84,700}.",
                           "position_decisions": {"pos_1": {"decision": "hold", "reason": "Trend intact."}}})
    report = read_model_report(complete[:-1], [])
    assert report["agent_stance"] == "wait"
    assert report["strategy"] == "Wait for a retest {of 84,700}."
    assert report["position_decisions"] == {"pos_1": {"decision": "hold", "reason": "Trend intact."}}


def test_trailing_prose_after_the_object_is_ignored():
    report = read_model_report('{"agent_stance": "long", "market": "Up."}\nHope this helps!', [])
    assert (report["agent_stance"], report["market"]) == ("long", "Up.")


def test_text_cut_off_inside_a_string_is_not_guessed():
    raw = '{"agent_stance": "short", "strategy": "Sell the rall'
    report = read_model_report(raw, [])
    assert report["agent_stance"] is None and report["strategy"] == raw


def test_fields_swallowed_by_an_unclosed_object_are_restored():
    # The model forgot to close direction_assessment, so later fields nested inside it.
    raw = ('{"agent_stance":"wait","direction_assessment":{"long":{"verdict":"conditional","reason":"Needs a retest."},'
           '"short":{"verdict":"unsuitable","reason":"Trend is up."},'
           '"strategy":"Hold and trail the stop.","position_decisions":{"pos_1":{"decision":"hold","reason":"Intact."}}}')
    report = read_model_report(raw, [])
    assert report["strategy"] == "Hold and trail the stop."
    assert report["position_decisions"] == {"pos_1": {"decision": "hold", "reason": "Intact."}}
    assert set(report["direction_assessment"]) == {"long", "short"}


def test_text_fields_inside_a_made_up_wrapper_are_read():
    raw = json.dumps({"agent_stance": "long", "string_fields": {"market": "Up.", "strategy": "Buy dips."}})
    report = read_model_report(raw, [])
    assert (report["market"], report["strategy"]) == ("Up.", "Buy dips.")


def test_a_well_formed_report_is_unchanged():
    raw = json.dumps({"market": "Top.", "direction_assessment": {"long": {"verdict": "reasonable", "reason": "OK."},
                                                                 "market": "nested"}})
    assert read_model_report(raw, [])["market"] == "Top."
