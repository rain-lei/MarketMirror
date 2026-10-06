const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {PreviewState,renderControls,renderRole}=require('../design/strategy-preview.js');

const parameters={aggressive:{text_sensitivity:.9,base_weight:.3,risk_budget:.04},conservative:{text_sensitivity:.25,base_weight:.2,risk_budget:.008},institutional:{text_sensitivity:.5,base_weight:.35,risk_budget:.016}};
function response(input){
  const decision={total_target_weight:.3,planned_turnover:.15,beliefs:{A:.54,B:0,C:0},current_weights:{A:.05,B:.05,C:.05},desired_weights:{A:.1,B:.1,C:.1},order_weight_changes:{A:.05,B:.05,C:.05},reasons:['portfolio_risk','portfolio_allocation'],terms:Object.fromEntries(['A','B','C'].map(a=>[a,{market:0,text:a==='A'?.54:0,uncertainty:0}]))};
  return {mode:'strategy_decision_preview',schema_version:'fixed-state-decision-v1',input:structuredClone(input),assumptions:{identical_state_before:true,fills_simulated:false,llm_called:false},roles:Object.fromEntries(Object.keys(parameters).map(role=>[role,{baseline:structuredClone(decision),with_message:structuredClone(decision)}]))};
}

test('empty and invalid scenario fields block preview and never become a zero signal',()=>{
  const s=new PreviewState();assert.ok(s.edit('signal',''));assert.equal(s.scenario.signal,.6);assert.equal(s.status,'invalid');assert.equal(s.begin(parameters),null);
  assert.match(renderControls(s),/value="" aria-invalid="true"/);
  assert.equal(s.edit('signal','0'),'');assert.equal(s.scenario.signal,0);assert.equal(s.status,'idle');
  assert.equal(s.edit('volatility','0.83'),'');assert.equal(s.scenario.volatility,.0083);
  assert.ok(s.edit('volatility','51'));assert.equal(s.scenario.volatility,.0083);
  assert.ok(s.edit('scope','<img>'));assert.ok(s.edit('history','unknown'));
});

test('editing invalidates an in-flight response, including errors from old requests',()=>{
  const s=new PreviewState(),old=s.begin(parameters);s.edit('signal','-0.6');const current=s.begin(parameters);
  assert.equal(s.accept(old,response(old.input)),false);assert.equal(s.fail(old,'old failure'),false);assert.equal(s.status,'loading');
  assert.equal(s.accept(current,response(current.input)),true);assert.equal(s.status,'ready');
  s.invalidate('修正策略参数');assert.equal(s.result,null);assert.equal(s.begin(parameters),null);assert.doesNotMatch(renderRole(s,'aggressive'),/30.00%/);
});

test('the submitted strategy is an independent copy and reordered input keys remain compatible',()=>{
  const s=new PreviewState(),p=structuredClone(parameters),ticket=s.begin(p);p.aggressive.text_sensitivity=2;
  assert.equal(ticket.input.parameters.aggressive.text_sensitivity,.9);
  const value=response(ticket.input);value.input={scenario:value.input.scenario,parameters:value.input.parameters};
  assert.equal(s.accept(ticket,value),true);
});

test('mismatched inputs, missing decisions and nonfinite fields fail without zero-filled cards',()=>{
  for(const mutate of [v=>{v.input.scenario.signal=-1;},v=>{delete v.roles.institutional;},v=>{v.roles.aggressive.with_message.desired_weights.A=NaN;},v=>{v.assumptions.fills_simulated=true;}]){
    const s=new PreviewState(),ticket=s.begin(parameters),v=response(ticket.input);mutate(v);
    assert.equal(s.accept(ticket,v),false);assert.equal(s.status,'error');assert.equal(s.result,null);assert.match(renderRole(s,'aggressive'),/缺少决策字段/);
  }
});

test('presets define synthetic inputs and reset prior errors without modifying strategy profiles',()=>{
  const s=new PreviewState(),before=structuredClone(parameters);s.edit('uncertainty','');assert.equal(s.preset('risk'),true);
  assert.equal(s.scenario.volatility,.3);assert.equal(s.scenario.history,'first');assert.equal(s.scenario.scope,'A');assert.deepEqual(s.errors,{});
  assert.equal(s.preset('unknown'),false);assert.deepEqual(parameters,before);
  assert.match(renderControls(s),/两条件使用相同价格/);assert.match(renderControls(s),/此前连续负向 2 步/);
});

test('rendered roles distinguish targets, planned changes, constraints and unexecuted fills',()=>{
  const s=new PreviewState(),ticket=s.begin(parameters),v=response(ticket.input);v.roles.conservative.with_message.order_weight_changes={A:0,B:0,C:0};v.roles.conservative.with_message.planned_turnover=0;v.roles.conservative.with_message.reasons=['confirmation_or_rebalance_wait'];s.accept(ticket,v);
  const html=renderRole(s,'conservative');assert.match(html,/等待/);assert.match(html,/组合目标股票权重/);assert.match(html,/30.00%/);assert.match(html,/拟调仓幅度/);assert.match(html,/等待连续确认或再平衡/);assert.match(html,/实际订单和成交需运行完整实验/);
  assert.doesNotMatch(html,/成交股数|NaN|undefined/);
  const bad=response(ticket.input);bad.roles.aggressive.with_message.reasons=['<img src=x onerror=bad()>'];s.accept(ticket,bad);assert.doesNotMatch(renderRole(s,'aggressive'),/<img/);assert.match(renderRole(s,'aggressive'),/&lt;img/);
});

test('the live app uses the backend preview, invalidates parameter edits and serves its module',()=>{
  const source=fs.readFileSync('design/app.js','utf8'),index=fs.readFileSync('design/index.html','utf8');
  assert.match(source,/apiJson\('\/api\/platform\/strategy-preview'/);assert.match(source,/data-preview-role/);assert.match(source,/scheduleStrategyPreview\(invalid\?/);
  assert.match(source,/strategyPreview.status==='idle'\)scheduleStrategyPreview/);assert.match(index,/strategy-preview.js/);
});
