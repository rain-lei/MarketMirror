const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const view=require('../design/observed-view.js');

function bundle(id='observed_test'){
  const roles=['aggressive','conservative','institutional'];
  const fact={kind:'approval_uncertainty',status:'uncertain',claim:'审批仍不确定。',evidence:[{source:'reply',quote:'审批不确定',start:1,end:6}]};
  const parameters=roles.map(role=>({name:role,role,initial_cash:100000}));
  const row=role=>({role,decision:{action:'sell',target_weight:.1,requested_shares:-20,reason_codes:['uncertainty_stress']},
    filled_shares:-20,closing_weight:.11,fees_paid:2,closing_wealth:99000});
  const paths=Object.fromEntries(['no_text','keywords','reviewed_llm'].map(mode=>[mode,{trace:Array.from({length:3},(_,i)=>({
    trade_date:`2000-01-0${i+3}`,signal_cutoff_date:`2000-01-0${i+1}`,execution_reference_date:`2000-01-0${i+2}`,
    source_visible:i>=1,source_active:i===1,text_signal:0,text_uncertainty:mode==='no_text'?0:.6,
    observed_return:.01,agents:Object.fromEntries(roles.map(role=>[role,row(role)]))}))}]));
  const explanations=Object.fromEntries(Object.entries(paths).map(([mode,path])=>[mode,path.trace.map(d=>({agents:Object.fromEntries(roles.map(role=>[role,{
    combined_score:-.1,market_contribution:.02,text_direction_contribution:0,uncertainty_contribution:-.12,
    text_evidence_used:mode!=='no_text'&&d.source_active?fact.evidence:[]}]))}))]));
  return {schema:'platform-observed-experiment-v1',id,label:'测试历史归档',data_kind:'observed_return_price_taking_replay',
    case:{stock_code:'300294',available_at:'2000-01-02T23:59:59.999999+08:00',segments:[{source:'reply',text:'😀审批不确定。'}]},
    paths,explanations,parameters,sessions:3,information_duration_steps:1,fee_rate:.001,
    verification:{agent_decisions:27},comparison:roles.flatMap(role=>['no_text','keywords','reviewed_llm'].map(condition=>({role,condition,
      return_pct:-1,delta_return_pp:condition==='no_text'?0:-1e-8,max_drawdown_pct:1,trades:2,fees:4}))),
    model:{model:'DeepSeek-V4-Flash-0731-W8A8',finished_at_utc:'2000-01-01T00:00:00+00:00',normalized:{facts:[fact]},raw_response:'{"facts":[]}'},
    review:{reviewed_record:{facts:[fact]},checks:[{fact_index:0,notes:'测试回复原文支持。'}]}};
}

test('stale selection, refresh and missing-library responses cannot replace the active archive',()=>{
  const s=new view.ObservedState(),old=s.begin('old'),latest=s.begin('new');
  assert.equal(s.accept(old,bundle('old')),false);assert.equal(s.fail(old,'stale'),false);
  assert.equal(s.accept(latest,bundle('new')),true);assert.equal(s.step,2);
  s.step=3;assert.equal(s.accept(s.begin('new'),bundle('new')),true);assert.equal(s.step,3);
  const pending=s.begin('new');s.clear();assert.equal(s.accept(pending,bundle('new')),false);assert.equal(s.detail,null);
});

test('response identity and source type are checked before rendering',()=>{
  const s=new view.ObservedState();assert.throws(()=>s.accept(s.begin('expected'),bundle('wrong')),/不一致/);
  const wrong=bundle();wrong.data_kind='synthetic';assert.throws(()=>s.accept(s.begin(wrong.id),wrong),/不一致/);
  const short=bundle();short.paths.keywords.trace.pop();assert.throws(()=>s.accept(s.begin(short.id),short),/不一致/);
});

test('uncertainty-only decisions display zero direction, source quote and normalized unit labels',()=>{
  const s=new view.ObservedState(),b=bundle();s.step=2;
  const html=view.renderDecisions(b,s);
  assert.match(html,/文本 \+0.0000/);assert.match(html,/不确定性 -0.1200/);
  assert.match(html,/审批不确定/);assert.match(html,/data-observed-receipt="aggressive"/);
  assert.match(html,/请求单位/);assert.doesNotMatch(html,/请求股数|成交股数/);
  s.condition='no_text';assert.doesNotMatch(view.renderDecisions(b,s),/data-observed-receipt/);
  assert.match(view.renderClock(b,s),/无文本条件不使用原文/);
});

test('source quote lookup respects Unicode code points and escapes untrusted content',()=>{
  const b=bundle(),quote=b.review.reviewed_record.facts[0].evidence[0];
  assert.match(view.renderSource(b,quote),/😀<mark id="observed-source-mark">审批不确定<\/mark>。/);
  assert.doesNotMatch(view.renderSource(b,{...quote,start:0}),/<mark/);
  b.case.segments[0].text='<script>alert(1)</script>';
  assert.match(view.renderSource(b,null),/&lt;script&gt;/);assert.doesNotMatch(view.renderSource(b,null),/<script>/);
});

test('plots, all conditions and raw responses retain exact small values and archive boundaries',()=>{
  const s=new view.ObservedState(),b=bundle();s.step=2;
  const html=view.renderDetail(b,s);
  assert.match(html,/viewBox="0 0 365 205"/);assert.match(html,/-1.00e-8 pp/);
  assert.match(html,/原始返回/);assert.match(html,/复核后 LLM/);
  assert.match(html,/export/);assert.match(html,/涨跌停排队/);assert.doesNotMatch(html,/NaN|undefined/);
  assert.match(html,/data-replay="observed-step"/);assert.match(html,/消息进入 · 第 2 日/);
  assert.match(html,/消息作用区间 · 第 2 日/);
  assert.match(html,/aria-valuetext="第 2 日 · 2000-01-04 · 消息作用中"/);
  assert.equal(view.number(null),'未存档');assert.equal(view.number(0),'0.0000');
  b.model.raw_response='<img src=x onerror=alert(1)>';
  assert.doesNotMatch(view.renderFacts(b,s),/<img/);
});

test('a failed refresh keeps the known result and explicitly marks the stale verification',()=>{
  const s=new view.ObservedState();s.accept(s.begin('observed_test'),bundle());
  s.fail(s.begin('observed_test'),'核验失败');
  assert.match(view.renderDetail(s.detail,s),/保留上次已读取归档，当前核验状态未更新/);
  const missing=view.renderLibrary({available:false,experiments:[],message:'尚无归档'},s);
  assert.match(missing,/尚无归档/);assert.doesNotMatch(missing,/data-observed-select/);
});

const source=fs.readFileSync('design/app.js','utf8');
const functions=source.slice(source.indexOf('  async function loadObservedExperiment('),source.indexOf('  function newFromCase(){'));
test('missing catalog invalidates a pending detail request without creating a fallback',async()=>{
  let resolveDetail;const workspace=new view.ObservedState();
  const context=vm.createContext({observedWorkspace:workspace,observedCatalogVersion:0,observedCatalogLoading:false,observedCatalogError:'',
    updateObservedPanels(){},apiJson(path){return path.endsWith('/observed-experiments')?Promise.resolve({available:false,experiments:[]}):
      new Promise(resolve=>{resolveDetail=resolve;});}});
  vm.runInContext(functions,context);const pending=context.loadObservedExperiment('observed_test');
  await context.hydrateObservedExperiments();resolveDetail(bundle());await pending;
  assert.equal(workspace.detail,null);assert.equal(workspace.loading,false);assert.equal(workspace.selectedId,null);
});
