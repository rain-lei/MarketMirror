const test = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const {buildStep, describeOrder} = require('../design/decision-view.js');

let cached;
function result() {
  if (!cached) {
    const code = [
      'import json',
      'from design.engine import run_market',
      "print(json.dumps(run_market(dict(cash=1000000, seed=7, sessions=9, duration=3, signal=.6, uncertainty=.2)), ensure_ascii=False))",
    ].join('; ');
    cached = JSON.parse(execFileSync('python', ['-B', '-X', 'utf8', '-c', code], {
      cwd: path.resolve(__dirname, '..'), maxBuffer: 30 * 1024 * 1024, encoding: 'utf8',
    }));
  }
  return cached;
}

test('decision view follows the selected archived observation and group', () => {
  const r = result();
  for (const group of ['with_message', 'baseline']) {
    const before = buildStep(r, group, 'A', 4);
    const visible = buildStep(r, group, 'A', 5);
    assert.equal(before.observation.text_signal, 0);
    assert.equal(before.observation.text_uncertainty, 0);
    assert.equal(visible.observation.text_signal, group === 'with_message' ? .6 : 0);
    assert.equal(visible.observation.text_uncertainty, group === 'with_message' ? .2 : 0);
    assert.equal(before.roles.length, 3);
    assert.equal(before.roles.every(role => role.accounts.length === 4), true);
  }
});

test('decision view reconstructs weighted components and preserves constraints', () => {
  const r = result();
  const view = buildStep(r, 'with_message', 'A', 5);
  for (const role of view.roles) {
    const accounts = role.accounts;
    const nav = accounts.reduce((sum, account) => sum + account.nav, 0);
    const weighted = key => accounts.reduce((sum, account) => sum + account[key] * account.nav, 0) / nav;
    assert.ok(Math.abs(role.belief - weighted('belief')) < 1e-12);
    assert.ok(role.terms);
    assert.ok(Math.abs(role.terms.market + role.terms.text + role.terms.uncertainty - role.belief) < 1e-9);
    assert.ok(role.rules.some(rule => rule.code === 'portfolio_risk'));
    assert.equal(role.constraints.some(item => item.code === 'portfolio_risk'), false);
  }
  // The role with a longer confirmation interval demonstrates that target and
  // actual order change are separate archived values, not UI recomputation.
  const hasDifferentPlan = view.roles.some(role => role.accounts.some(account =>
    Math.abs(account.target - account.before - account.orderChange) > 1e-12));
  assert.equal(hasDifferentPlan, true);
});

test('decision view totals only the selected role orders from the auction ledger', () => {
  const r = result();
  const pathData = r.paths.with_message;
  const day = pathData.trace[4];
  const view = buildStep(r, 'with_message', 'A', 5);
  for (const role of view.roles) {
    const names = new Set(role.accounts.map(account => account.name));
    const orders = day.portfolio_auction.asset_calls.A.orders.filter(order => names.has(order.owner));
    assert.deepEqual(role.orders, orders);
    const sum = key => orders.reduce((total, order) => total + order[key], 0);
    assert.equal(role.requested, sum('quantity'));
    assert.equal(role.accepted, sum('accepted_quantity'));
    assert.equal(role.filled, sum('filled_quantity'));
    assert.equal(role.buys, orders.filter(order => order.side === 'buy').reduce((n, order) => n + order.quantity, 0));
    assert.equal(role.sells, orders.filter(order => order.side === 'sell').reduce((n, order) => n + order.quantity, 0));
  }
  assert.match(describeOrder('unmatched_day_order_expired'), /未撮合/);
  assert.match(describeOrder('future_engine_reason'), /未识别/);
});

test('missing archived provenance stays missing instead of becoming zero', () => {
  const r = result();
  const missingObservation = structuredClone(r);
  delete missingObservation.paths.with_message.trace[4].observations.A;
  const observationView = buildStep(missingObservation, 'with_message', 'A', 5);
  assert.equal(observationView.observation, null);
  assert.equal(observationView.roles.every(role => role.terms === null), true);

  const missingProfile = structuredClone(r);
  const first = missingProfile.paths.with_message.participant_specs.aggressive_00;
  first.profile = null;
  const profileView = buildStep(missingProfile, 'with_message', 'A', 5);
  assert.equal(profileView.roles.find(role => role.role === 'aggressive').accounts[0].terms, null);
});

test('invalid or absent archived steps are rejected', () => {
  const r = result();
  assert.throws(() => buildStep(r, 'with_message', 'D', 5), /无效/);
  assert.throws(() => buildStep(r, 'with_message', 'A', 99), /不存在/);
});
