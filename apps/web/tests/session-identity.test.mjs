import assert from 'node:assert/strict';
import { test } from 'node:test';
import { sessionAccount } from '../src/sessionIdentity.ts';

test('local workspaces never show their internal owner id', () => {
  assert.equal(sessionAccount(null), null);
  assert.equal(sessionAccount({ mode: 'local', user_id: 'local-demo' }), null);
  assert.equal(sessionAccount({ mode: 'local', user_id: 'local-demo', email: 'me@gmail.com' }), null);
});

test('remote sessions show the signed-in name with its email', () => {
  assert.deepEqual(
    sessionAccount({ mode: 'remote', user_id: 'u1', email: 'me@gmail.com', display_name: 'Hsin Chuan' }),
    { initial: 'H', name: 'Hsin Chuan', detail: 'me@gmail.com' });
  assert.deepEqual(
    sessionAccount({ mode: 'remote', user_id: 'u1', email: 'me@gmail.com' }),
    { initial: 'M', name: 'me@gmail.com', detail: null });
  assert.equal(sessionAccount({ mode: 'remote', user_id: 'u1', email: ' ', display_name: '' }), null);
  assert.equal(sessionAccount({ mode: 'remote', user_id: 'google-oauth2|123' }), null);
  assert.equal(sessionAccount({ mode: 'remote', user_id: 'u1', display_name: '黃欣傳' }).initial, '黃');
});
