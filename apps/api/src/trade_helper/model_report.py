"""Read Agent output for display without rejecting its strategy or evidence claims."""

import json
from decimal import Decimal, DecimalException

from .prompts.contracts import validate_locale
from .risk import costed_risk


def read_model_report(raw: str, trace: list[dict], *, output_locale: str = "zh-TW") -> dict:
    validate_locale(output_locale)
    text = raw.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    def sentence(key):
        item = value.get(key)
        return item if isinstance(item, str) else ''
    result = {key: sentence(key) for key in
              ('market', 'levels', 'strategy', 'supporting_evidence', 'counter_evidence')}
    if not any(result.values()):
        result['strategy'] = raw or (
            'The model did not return analysis text.' if output_locale == 'en-US'
            else '模型此次未回傳分析文字。'
        )
    result['evidence_tools'] = [name for name in value.get('evidence_tools', []) if isinstance(name, str)] if isinstance(value.get('evidence_tools'), list) else []
    result['strategy_decision'] = value.get('strategy_decision') if value.get('strategy_decision') in ('candidate', 'wait') else 'wait'
    result['agent_stance'] = value.get('agent_stance') if value.get('agent_stance') in ('long', 'short', 'wait') else None
    plan = value.get('entry_decision')
    if isinstance(plan, dict) and plan.get('action') in ('open_now', 'wait_for_entry', 'stand_aside'):
        # Preserve absent values as absent; do not invent prices, triggers or actions.
        result['entry_decision'] = {key: plan.get(key) for key in (
            'action', 'side', 'entry_price', 'stop_loss', 'take_profit', 'trigger', 'invalidation', 'reason')}
        for key in ('reason', 'trigger', 'invalidation'):
            if not isinstance(result['entry_decision'][key], str):
                result['entry_decision'][key] = None
        if result['entry_decision']['side'] not in ('long', 'short'):
            result['entry_decision']['side'] = None
        for key in ('entry_price', 'stop_loss', 'take_profit'):
            try:
                price = Decimal(str(plan.get(key)))
                result['entry_decision'][key] = str(price) if price.is_finite() else None
            except (DecimalException, ValueError, TypeError):
                result['entry_decision'][key] = None
        result['entry_decision']['basis_level_ids'] = plan.get('basis_level_ids') if isinstance(plan.get('basis_level_ids'), list) else []
    else:
        result['entry_decision'] = None
    macro = value.get('macro_outlook')
    result['macro_outlook'] = (macro | {'evidence_ids': macro.get('evidence_ids') if isinstance(macro.get('evidence_ids'), list) else []}
                              if isinstance(macro, dict) and isinstance(macro.get('reason'), str) else None)
    supplied = value.get('position_decisions', {})
    result['position_decisions'] = {key: item for key, item in supplied.items()
        if isinstance(item, dict) and item.get('decision') in ('hold', 'close_now') and isinstance(item.get('reason'), str)} if isinstance(supplied, dict) else {}
    result['position_choices'] = {item['position_id']: item['default_action_id']
        for run in trace if run['tool'] == 'evaluate_positions' for item in run['result']['positions']}
    return result


def entry_cost_reference(plan: dict | None, quote: dict, leverage: int) -> dict | None:
    """Optional arithmetic only; unavailable estimates never block the report."""
    if not plan or plan.get('action') == 'stand_aside' or plan.get('side') not in ('long', 'short'):
        return None
    try:
        entry, stop, target = (Decimal(str(plan.get(key))) for key in ('entry_price', 'stop_loss', 'take_profit'))
        if not all(price.is_finite() and price > 0 for price in (entry, stop, target)):
            return None
        if not (stop < entry < target if plan['side'] == 'long' else target < entry < stop):
            return None
        return costed_risk(plan['side'], entry, stop, target, leverage, quote)
    except (DecimalException, ValueError, TypeError, KeyError, ZeroDivisionError):
        return None
