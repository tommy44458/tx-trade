import assert from 'node:assert/strict';
import test from 'node:test';
import { levelLadder } from '../src/levelLadder.ts';

const zones = [
  { id: 's1', low: '82000', high: '82500' },
  { id: 'r1', low: '86000', high: '86500' },
  { id: 'r0', low: '85000', high: '85300' },
];
const order = (rows) => rows.map((row) => row.kind === 'now' ? `now:${row.price}` : row.level.id);

test('zones run from the highest price down, with the current price marked between them', () => {
  assert.deepEqual(order(levelLadder(zones, '85400')), ['r1', 'now:85400', 'r0', 's1']);
  assert.deepEqual(order(levelLadder(zones, '90000')), ['now:90000', 'r1', 'r0', 's1']);
  assert.deepEqual(order(levelLadder(zones, '80000')), ['r1', 'r0', 's1', 'now:80000']);
});

test('a price inside a zone, or no price, adds no separate marker', () => {
  assert.deepEqual(order(levelLadder(zones, '85100')), ['r1', 'r0', 's1']);
  assert.deepEqual(order(levelLadder(zones, null)), ['r1', 'r0', 's1']);
  assert.deepEqual(order(levelLadder(zones, 'n/a')), ['r1', 'r0', 's1']);
  assert.equal(zones[0].id, 's1');
});
