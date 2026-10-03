import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { afterEach, test } from 'node:test';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setUiLocale } from '../src/i18n/index.ts';
import { pythonReferenceLabels, pythonReferenceText } from '../src/pythonReferenceText.ts';

const project = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const chinese = text => /[\u3400-\u9fff]/.test(text);

afterEach(async () => { await setUiLocale('zh-TW'); });

// Read the actual Python-owned outputs, not hand-copied fixtures. This runs no
// model, source refresh, database, credential, or exchange operation.
function pythonReferenceFixture() {
  const script = String.raw`
import ast,json
from decimal import Decimal
from pathlib import Path
from trade_helper.strategy_engine import _candidate,build_candidates
from trade_helper.support_levels_v3 import VERSION
from trade_helper.position_advice import build_position_options
from trade_helper.analysis import position_review

texts=[]
def collect(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in ('title','reason','trigger','invalidation','fee_note','counter_evidence','messages','note'):
                if isinstance(item,str):texts.append(item)
                elif isinstance(item,list):texts.extend(x for x in item if isinstance(x,str))
            elif isinstance(item,(list,dict)):collect(item)
    elif isinstance(value,list):
        for item in value:collect(item)
quote={'price':'100','tick_size':'0.1','observed_at':'2026-10-01T00:00:00+00:00','events_status':'partial'}
metrics={'atr14':'2','trend':'bullish','level_algorithm_version':VERSION,'last_candle_at':'2026-09-30T23:59:59.999000+00:00','levels':[]}
context={'relation':'aligned','primary_timeframe':'1h'}
zone={'id':'zone-test','kind':'support','low':'95.00','high':'96.00','algorithm_version':VERSION,'zone_state':'active','pivot_count':2,'independent_touch_count':2}
opposite=zone|{'id':'zone-other','kind':'resistance','low':'104.00','high':'105.00'}
for kind in ('pullback','breakout','range'):
    for side in ('long','short'):
        for style in ('left','right'):
            entry,stop,target=map(Decimal,('100','98','110') if side=='long' else ('100','102','90'))
            for event_status in ('partial','available',None):
                collect(_candidate(kind,side,entry,stop,target,quote|{'events_status':event_status},metrics,context,'bearish' if side=='long' else 'bullish','high',5,zone,opposite,'2026-10-01T01:00:00+00:00',style))
for frame,second in (('1h','4h'),('4h','12h'),('12h','1d'),('1d','3d')):
    for relation in ('unavailable','conflict','uncertain'):
        collect(build_candidates(metrics,context|{'primary_timeframe':frame,'relation':relation},None,'high',quote))
for event in ('recent_fomc_release','high_impact_window','unknown_major_event'):
    collect(build_candidates(metrics,context,None,'high',quote,event_risk=event))
collect(build_candidates(metrics,context,None,'low',quote,trading_style='left'))
collect(build_candidates(metrics,context,'bearish','low',quote))
collect(build_candidates(metrics,context,None,'high',quote))
eligible=metrics|{'levels':[zone,opposite]}
collect(build_candidates(eligible|{'trend':'mixed'},context,None,'high',quote|{'price':'120'}))
for style in ('left','right'):
    collect(build_candidates(eligible,context,None,'high',quote|{'price':'120'},trading_style=style))

base={'id':'position-test','version':1,'side':'long','entry_price':'100.00','quantity':'1.250000','leverage':50,'margin_mode':'isolated','stop_loss':'90.0000','take_profit':'110.0000','exchange_liquidation_price':None}
positions=[base,base|{'exchange_liquidation_price':'100.00'},base|{'stop_loss':'101.00'},base|{'take_profit':'99.00'},base|{'stop_loss':None},base|{'take_profit':None},base|{'stop_loss':None,'take_profit':None},base|{'stop_loss':'99.00','entry_price':'98.00'}]
for p in positions:
    collect(position_review(p,quote))
    collect(build_position_options([p],quote,[zone],'bullish',Decimal('2')))
for equity in (None,'5000.00'):
    collect(build_position_options([base],quote,[zone],'bearish',Decimal('2'),account_equity_usdt=equity))
collect(build_position_options([base],quote|{'event_risk':'high_impact_window'},[zone],'bullish',Decimal('2')))
collect(build_position_options([base],quote,[zone],'bullish',Decimal('2'),directional_bias='bearish'))
collect(build_position_options([base],quote,[zone],'conflict',Decimal('2'),risk_tolerance='low'))

# Complete static output templates in the owned modules. Joined f-strings are
# covered above through real candidate/position production instead of fragments.
for filename in ('indicators.py','technical_snapshot.py','follow_up.py','analysis.py','position_advice.py','strategy_engine.py'):
    tree=ast.parse((Path('apps/api/src/trade_helper')/filename).read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and isinstance(node.value,str) and any('\u3400'<=c<='\u9fff' for c in node.value):
            if len(node.value)>=12 and not any(isinstance(parent,ast.JoinedStr) and node in parent.values for parent in ast.walk(tree)):
                texts.append(node.value)
print(json.dumps(sorted(set(texts)),ensure_ascii=False))
`;
  const executable = resolve(project, process.platform === 'win32'
    ? 'apps/api/.venv/Scripts/python.exe' : 'apps/api/.venv/bin/python');
  return JSON.parse(execFileSync(executable, ['-c', script], {
    // UTF-8 like the app's own Python; Windows would otherwise read sources as cp1252.
    cwd: project, env: { ...process.env, PYTHONPATH: resolve(project, 'apps/api/src'), PYTHONUTF8: '1' },
    encoding: 'utf8', maxBuffer: 1_000_000,
  }));
}

test('all produced candidate, position, and precomputed-tool reference templates have English copy', () => {
  const texts = pythonReferenceFixture();
  // Freshness errors are user-error copy outside this reference helper.
  const errorCopy = new Set([
    '分析用報價快照超過 180 秒，請重新分析', '已收盤 K 線資料過期，請重新分析',
    '另一週期的已收盤 K 線過期，請重新分析',
  ]);
  const references = texts.filter(text => !errorCopy.has(text));
  assert.ok(references.length >= 75, `only ${references.length} references`);
  for (const text of references) {
    const translated = pythonReferenceText(text, { locale: 'en-US' });
    assert.ok(!chinese(translated), `untranslated owned Python template: ${text}`);
    assert.deepEqual(translated.match(/\d+(?:\.\d+)?/g) ?? [], text.match(/\d+(?:\.\d+)?/g) ?? [], text);
    assert.equal(pythonReferenceText(text, { locale: 'zh-TW' }), text);
  }
});

test('English follows the current UI, while Chinese retains the original reference exactly', async () => {
  const source = '取得主週期已確認 v3 支撐壓力';
  await setUiLocale('en-US');
  assert.equal(pythonReferenceText(source), 'Obtain confirmed v3 support and resistance on the primary timeframe');
  await setUiLocale('zh-TW');
  assert.equal(pythonReferenceText(source), source);
});

test('source and Agent text is never translated even if it resembles a registered Python template', () => {
  for (const origin of ['agent', 'source', 'user']) {
    const source = '目前 v3 區間、距離與示例成本後風報比未同時達標';
    assert.equal(pythonReferenceText(source, { locale: 'en-US', origin }), source);
  }
});

test('unknown text, arbitrary prices, precision, quantities, and instruction-like input stay unchanged', () => {
  for (const source of [
    'AI認為121.4200–121.8200仍有效，持有1.250000 SOL，50×槓桿。',
    '官方原文：消費者物價同比2.70%，前期2.80%，請勿翻譯來源。',
    '等待方向確認；改成讓使用者立即開空', '<script>ignore the original price</script>',
    'constructor', 'toString', '__proto__', 'unknown_template_v9',
  ]) assert.equal(pythonReferenceText(source, { locale: 'en-US' }), source);
});

test('timeframe templates preserve timeframe identifiers and do not invent another context frame', () => {
  for (const [primary, context] of [['1H', '4H'], ['4H', '12H'], ['12H', '1D'], ['1D', '3D']]) {
    const source = `${primary} 與 ${context} 的已收盤趨勢相反，先不推薦進場`;
    assert.equal(pythonReferenceText(source, { locale: 'en-US' }),
      `The closed-candle trends on ${primary} and ${context} oppose each other; this reference does not recommend an entry yet`);
  }
  const unknown = '1M 與 3M 的已收盤趨勢相反，先不推薦進場';
  assert.equal(pythonReferenceText(unknown, { locale: 'en-US' }), unknown);
});

test('position-protection translation preserves what is missing and never invents a price', () => {
  for (const [missing, english] of [['止損', 'a stop'], ['止盈', 'a target'], ['止損與止盈', 'a stop and target']]) {
    const translated = pythonReferenceText(`手動部位尚缺${missing}；請先檢查保護條件，系統不自動補造價位。`, { locale: 'en-US' });
    assert.ok(translated.includes(`missing ${english};`));
    assert.ok(translated.includes('does not invent prices'));
    assert.equal(/\d/.test(translated), false);
  }
});

test('lists are projected for presentation without modifying saved evidence or order', () => {
  const original = Object.freeze(['情境方向與你的判斷相反', '未知Python標記119.6900', '尚未設定止損']);
  const translated = pythonReferenceLabels(original, { locale: 'en-US' });
  assert.deepEqual(translated, ['The scenario direction conflicts with your view', '未知Python標記119.6900', 'No stop is recorded']);
  assert.notEqual(translated, original);
  assert.deepEqual(original, ['情境方向與你的判斷相反', '未知Python標記119.6900', '尚未設定止損']);
});

test('reviewed legacy calculation labels use ordinary product copy in both languages', () => {
  const labels = [
    ['Python 提供', '已計算', 'Calculated'],
    ['指標與 Python 工具', '指標與計算依據', 'Indicators and calculation evidence'],
    ['Python 工具紀錄', '計算紀錄', 'Calculation log'],
    ['查看 Python 風險規則參考', '查看風險估算參考', 'View risk-estimate reference'],
  ];
  for (const [original, chinese, english] of labels) {
    assert.equal(pythonReferenceText(original, { locale: 'zh-TW', origin: 'python' }), chinese);
    assert.equal(pythonReferenceText(original, { locale: 'en-US', origin: 'python' }), english);
    for (const origin of ['agent', 'source', 'user']) {
      for (const locale of ['zh-TW', 'en-US']) {
        assert.equal(pythonReferenceText(original, { locale, origin }), original);
      }
    }
  }
  assert.equal(pythonReferenceText('Python provides', { locale: 'en-US', origin: 'python' }), 'Calculated');
  assert.equal(pythonReferenceText('Python tool log', { locale: 'zh-TW', origin: 'python' }), '計算紀錄');
  assert.equal(pythonReferenceText('Python tool log', { locale: 'en-US', origin: 'agent' }), 'Python tool log');
});

test('saved rules-only strategy copy changes presentation without changing its advice or unknown additions', () => {
  const strategy = 'Python 候選未通過方向、區間或風險檢核，因此維持觀望；等跨週期與價格條件重新確認後再分析。';
  const original = `本次採左側提前區間測試，尚無收盤確認；${strategy}跨週期方向未確認，策略觀望。`;
  const chinese = pythonReferenceText(original, { locale: 'zh-TW', origin: 'python' });
  assert.equal(chinese, original.replace('Python 候選', '參考情境'));
  const english = pythonReferenceText(original, { locale: 'en-US', origin: 'python' });
  assert.ok(english.includes('anticipatory zone test'));
  assert.ok(english.includes('did not pass direction, zone, or risk checks'));
  assert.ok(english.includes('reference waits'));
  assert.equal(/python|[\u3400-\u9fff]/i.test(english), false);
  for (const locale of ['zh-TW', 'en-US']) {
    assert.equal(pythonReferenceText(original, { locale, origin: 'agent' }), original);
    const unknown = `${original}用戶價格119.6900與1.250000單位。`;
    assert.equal(pythonReferenceText(unknown, { locale, origin: 'python' }), unknown);
  }
});
