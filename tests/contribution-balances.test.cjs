const assert = require('node:assert/strict');
const { test } = require('node:test');
const { calculate } = require('../static/js/contribution-balances.js');

test('the example shows a running balance after every account entry', () => {
  const state = calculate('3000.00', ['1700.00'], ['600.00', '200.00', '500.00']);
  assert.equal(state.available, 130000);
  assert.equal(state.allocated, 130000);
  assert.deepEqual(state.rowBalances, [70000, 50000, 0]);
  assert.equal(state.remaining, 0);
});

test('editing and removing rows immediately restores the available balance', () => {
  assert.equal(calculate('3000', ['1700'], ['600', '200']).remaining, 50000);
  assert.equal(calculate('3000', ['1700'], ['300', '200', '500']).remaining, 30000);
  assert.equal(calculate('3000', ['1700'], ['200', '500']).remaining, 60000);
  assert.equal(calculate('3000', ['1700'], []).remaining, 130000);
});

test('over-allocation is reported and each input receives its available maximum', () => {
  const state = calculate('3000', ['1700'], ['600', '200', '501']);
  assert.equal(state.remaining, -100);
  assert.deepEqual(state.limits, [59900, 19900, 50000]);
  assert.deepEqual(state.rowBalances, [70000, 50000, -100]);
});

test('cent calculations stay exact, including blank rows and large amounts', () => {
  assert.equal(calculate('0.30', ['0.10'], ['0.10', '0.10', '']).remaining, 0);
  assert.equal(
    calculate('9999999999.99', ['9999999999.97'], ['.01', '0.01']).remaining,
    0,
  );
  assert.equal(calculate('1700', ['1700'], ['']).remaining, 0);
});

test('invalid money never produces a misleading remaining balance', () => {
  for (const invalid of ['NaN', 'Infinity', '-1', '1.001', '1e3', '10000000000']) {
    const state = calculate('3000', ['1700'], [invalid]);
    assert.equal(state.valid, false, invalid);
    assert.equal(state.remaining, null);
  }
});
