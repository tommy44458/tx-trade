import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  LEVERAGE_PRESETS,
  MAX_LEVERAGE,
  MIN_LEVERAGE,
  validateLeverageInput,
} from '../src/leverage.ts';

test('presets and custom integer values share the API range of 1–125', () => {
  assert.equal(MIN_LEVERAGE, 1);
  assert.equal(MAX_LEVERAGE, 125);
  for (const value of [...LEVERAGE_PRESETS, 7, 17, 27, 42, 109]) {
    assert.deepEqual(validateLeverageInput(String(value)), {value, error: null});
  }
  assert.ok(LEVERAGE_PRESETS.includes(100));
  assert.ok(LEVERAGE_PRESETS.includes(125));
});

test('blank, partially edited decimals and out-of-range input have no fallback value', () => {
  for (const value of ['', ' ', '0', '126', '137', '-1', '5.', '.5', '5.5', '1e2', 'NaN', 'Infinity', '999999999999999999']) {
    const result = validateLeverageInput(value);
    assert.equal(result.value, null, value);
    assert.ok(result.error, value);
  }
});

test('pasted whitespace and leading zeros keep an exact integer when committed', () => {
  assert.deepEqual(validateLeverageInput(' 042 '), {value: 42, error: null});
  assert.deepEqual(validateLeverageInput('00125'), {value: 125, error: null});
  assert.equal(validateLeverageInput('51', 1, 50).value, null);
});
