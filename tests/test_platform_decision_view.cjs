const test = require('node:test');
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs'), vm = require('node:vm');
const decisionView = require('../design/decision-view.js');
const {buildStep, describeOrder} = decisionView;

let cached;
let boundaryCached;
function boundaryResults() {
  if (!boundaryCached) {
    const code = `import json
from design.engine import run_market
base=dict(cash=1000000,seed=17,sessions=9,duration=6)
floor=run_market(dict(base,signal=-1,uncertainty=1,market_assumptions=dict(scope='public',market=.2,volatility=.03)))
cap=run_market(dict(base,signal=1,uncertainty=0,market_assumptions=dict(scope='public',market=.2,volatility=.5)))
print(json.dumps(dict(floor=floor,cap=cap)))`;
    boundaryCached = JSON.parse(execFileSync('python', ['-B','-X','utf8','-c',code], {
      cwd:path.resolve(__dirname,'..'),maxBuffer:50*1024*1024,encoding:'utf8',
    }));
  }
  return structuredClone(boundaryCached);
}
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

test('actual rule boundaries explain zero and capped targets without altering the market ledger', () => {
  const {floor,cap}=boundaryResults(),before=JSON.stringify({floor,cap});
  const low=buildStep(floor,'with_message','A',5).roles.find(r=>r.role==='aggressive');
  assert.equal(low.target,0);
  assert.ok(low.accounts.every(a=>a.targetBoundary?.rawWeight<0&&a.targetBoundary.boundedWeight===0));
  assert.deepEqual(low.constraints.find(c=>c.code==='target_weight_floor'),{
    code:'target_weight_floor',label:'股票目标已触及 0% 下限，分值变化未必改变仓位',accounts:4,derived:true,
  });
  const high=buildStep(cap,'with_message','C',5).roles.find(r=>r.role==='aggressive');
  assert.ok(high.accounts.every(a=>a.targetBoundary?.rawWeight>a.targetBoundary.cap));
  assert.equal(high.constraints.find(c=>c.code==='target_weight_cap').accounts,4);
  assert.ok(high.accounts.every(a=>Math.abs(a.targetBoundary.boundedWeight-a.targetBoundary.cap)<1e-12));
  assert.equal(JSON.stringify({floor,cap}),before);
});

test('confirmation that retains a position is not described as a final zero target', () => {
  const {floor}=boundaryResults();
  const waiting=buildStep(floor,'with_message','A',5).roles.find(r=>r.role==='conservative');
  assert.ok(waiting.constraints.some(c=>c.code==='confirmation_or_rebalance_wait'));
  assert.ok(waiting.target>0);
  assert.ok(waiting.accounts.every(a=>a.targetBoundary===null));
  assert.equal(waiting.constraints.some(c=>c.code==='target_weight_floor'),false);
  const confirmed=buildStep(floor,'with_message','A',6).roles.find(r=>r.role==='conservative');
  assert.equal(confirmed.target,0);
  assert.equal(confirmed.constraints.find(c=>c.code==='target_weight_floor').accounts,4);
});

test('boundary explanations require all asset contributions, limits and final targets to agree', () => {
  for (const change of [
    (day,spec,d)=>delete day.observations.C,
    (day,spec,d)=>d.beliefs.C+=.1,
    (day,spec,d)=>delete spec.parameters.base_weight,
    (day,spec,d)=>delete d.portfolio_volatility,
    (day,spec,d)=>d.risk_weight_cap=.123,
    (day,spec,d)=>d.desired_weights.B=.1,
    (day,spec,d)=>d.desired_weights.B=1e-15,
  ]) {
    const {floor}=boundaryResults(),day=floor.paths.with_message.trace[4],spec=floor.paths.with_message.participant_specs.aggressive_00;
    change(day,spec,day.decisions.aggressive_00);
    const row=buildStep(floor,'with_message','A',5).roles.find(r=>r.role==='aggressive');
    assert.equal(row.accounts.find(a=>a.name==='aggressive_00').targetBoundary,null);
    assert.ok(row.constraints.every(c=>!c.derived||c.accounts<4));
  }
});

test('actual decision cards expose checked target bounds and keep unverified account values unavailable', () => {
  const {floor}=boundaryResults(),source=fs.readFileSync('design/app.js','utf8');
  const method=source.slice(source.indexOf('  function marketStepCards('),source.indexOf('  function marketAnalysis('));
  const context=vm.createContext({MarketDecisionView:decisionView,roles:[
    {key:'aggressive',name:'激进型',color:'orange'}, {key:'conservative',name:'保守型',color:'blue'},
    {key:'institutional',name:'机构型',color:'purple'}],icon:()=>'',format:String,esc:String});
  vm.runInContext(method,context);
  const html=context.marketStepCards(floor,{decisionGroup:'with_message',asset:'A',step:5});
  assert.match(html,/股票目标已触及 0% 下限/);
  assert.match(html,/目标边界由存档参数与三资产分值核对/);
  assert.match(html,/限幅前计算值/);assert.match(html,/基础权重 \+ 0.4 × 三资产平均分值/);
  assert.doesNotMatch(html,/NaN|undefined/);
  delete floor.paths.with_message.participant_specs.aggressive_00.parameters.base_weight;
  const partial=context.marketStepCards(floor,{decisionGroup:'with_message',asset:'A',step:5});
  assert.match(partial,/<td>未核对<\/td><td>未核对<\/td>/);
});
