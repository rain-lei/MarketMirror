const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {execFileSync}=require('node:child_process'),{webcrypto}=require('node:crypto');
const api=require('../design/decision-view.js').ExperimentComparison;
const app=fs.readFileSync('design/app.js','utf8');
let cached;
function fixtures(){
  if(!cached){
    const script=`import copy,json,tempfile
from pathlib import Path
from design.server import PlatformStore
from design.strategy_config import defaults
p=dict(title='策略基准',source='人工测试原文：共同市场与文本输入保持一致，仅调整激进型敏感度。',type='政策消息',published_at='2026-10-07T09:00',signal=.6,uncertainty=.2,duration=3,sessions=18,seed=7,cash=1000000,strategy_parameters=defaults(),market_assumptions=dict(scope='public',market=-.1,volatility=.03))
with tempfile.TemporaryDirectory() as tmp:
 s=PlatformStore(Path(tmp));out={}
 for i,key in enumerate(('left','right','seed','market','legacy')):
  q=copy.deepcopy(p)
  if key=='right': q['strategy_parameters']['aggressive']['text_sensitivity']=1.2
  if key=='seed': q['seed']=19
  if key=='market': q['market_assumptions']['market']=.2
  if key=='legacy': del q['market_assumptions']
  r=s.create(q,format(i+1,'032x'));s.run(r['id']);out[key]=s.export(r['id'])['experiment']
 print(json.dumps(out,ensure_ascii=False))`;
    cached=JSON.parse(execFileSync('python',['-B','-X','utf8','-c',script],{cwd:path.resolve(__dirname,'..'),encoding:'utf8',maxBuffer:50*1024*1024}));
  }
  return structuredClone(cached);
}
function independent(record,group,role){
  const p=record.backendResult.paths[group],names=Object.keys(p.participant_specs).filter(name=>p.participant_specs[name].parameters?.role===role);
  const orders=p.trace.flatMap(day=>Object.values(day.portfolio_auction.asset_calls).flatMap(call=>call.orders)).filter(o=>names.includes(o.owner));
  const accounts=names.map(n=>p.summary.accounts[n]);
  const final=accounts.reduce((s,a)=>s+Object.values(a.wallets).reduce((v,x)=>v+x,0)+['A','B','C'].reduce((v,k)=>v+a.shares[k]*p.summary.final_prices_minor[k],0),0);
  const initial=accounts.reduce((s,a)=>s+a.initial_wealth_minor,0);
  return {requested:orders.reduce((s,o)=>s+o.quantity,0),accepted:orders.reduce((s,o)=>s+o.accepted_quantity,0),filled:orders.reduce((s,o)=>s+o.filled_quantity,0),initial,final,return_pp:(final/initial-1)*100};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const next=()=>new Promise(setImmediate);

test('role returns and execution totals match independently read accounts and all daily order ledgers',()=>{
  const {left,right}=fixtures(),c=api.compare(left,right);
  assert.equal(c.conditions_match,true);assert.deepEqual(c.differences,[]);
  assert.deepEqual(c.parameters,[{role:'aggressive',key:'text_sensitivity',left:.9,right:1.2}]);
  for(const group of ['with_message','baseline'])for(const role of ['aggressive','conservative','institutional']){
    assert.deepEqual(c.left_metrics[group][role],independent(left,group,role));assert.deepEqual(c.right_metrics[group][role],independent(right,group,role));
  }
  assert.deepEqual(c.left_metrics.baseline,c.right_metrics.baseline);
  assert.notDeepEqual(c.left_metrics.with_message,c.right_metrics.with_message);
});

test('different seed, market condition or original text cannot be presented as a parameter-only comparison',()=>{
  const {left,seed,market}=fixtures();
  assert.equal(api.compare(left,seed).conditions_match,false);assert.ok(api.compare(left,seed).differences.some(d=>d.key==='seed'));
  assert.equal(api.compare(left,market).conditions_match,false);assert.ok(api.compare(left,market).differences.some(d=>d.key==='market'));
  const changed=structuredClone(left);changed.id='f'.repeat(32);changed.backendResult.provenance.experiment_id=changed.id;changed.source+='新原文';
  assert.ok(api.compare(left,changed).differences.some(d=>d.key==='source'));
  const state=new api.ComparisonState();state.select('left',left.id);state.select('right',market.id);assert.equal(state.accept(state.begin(),left,market),true);
  assert.match(api.renderBody(state),/条件有差异/);assert.match(api.renderBody(state),/不能只归因于策略参数/);
});

test('source verification recomputes UTF-8 hashes and refuses altered text or unavailable crypto',async()=>{
  const {left,right}=fixtures();await api.verifySources([left,right],webcrypto);
  right.source+='篡改';await assert.rejects(()=>api.verifySources([left,right],webcrypto),/内容与保存的标识/);
  await assert.rejects(()=>api.verifySources([left],{}),/无法核验/);
});

test('missing accounts, nonfinite decisions, truncated traces or forged execution summaries remain unavailable',()=>{
  const original=fixtures().left;
  const mutations=[r=>delete r.backendResult.paths.baseline.participant_specs.aggressive_00,
    r=>r.backendResult.paths.with_message.trace.pop(),
    r=>r.backendResult.paths.baseline.trace[0].decisions.aggressive_00.nav_minor=NaN,
    r=>delete r.backendResult.paths.with_message.trace[0].decisions.conservative_00,
    r=>r.backendResult.paths.with_message.trace[0].decisions.institutional_00.desired_weights.A=Infinity,
    r=>r.backendResult.paths.with_message.summary.strategy_filled++,
    r=>r.backendResult.paths.with_message.summary.accounts.aggressive_00.final_wealth_minor++,
    r=>r.backendResult.paths.with_message.summary.role_wealth_multiple.conservative=.99,
    r=>r.backendResult.paths.with_message.trace[4].observations.B.text_signal=0,
    r=>r.backendResult.paths.baseline.trace[0].covariance.A.A=0];
  for(const mutate of mutations){const r=structuredClone(original);mutate(r);assert.throws(()=>api.validateRecord(r));}
});

test('unknown order ownership, missing fills and impossible accepted quantities are rejected',()=>{
  const original=fixtures().left;
  for(const mutate of [o=>o.owner='unknown',o=>delete o.filled_quantity,o=>o.accepted_quantity=o.quantity+100]){
    const r=structuredClone(original),order=r.backendResult.paths.baseline.trace[0].portfolio_auction.asset_calls.A.orders[0];mutate(order);assert.throws(()=>api.validateRecord(r));
  }
});

test('record versions and strategy or analysis bindings must match the selected run',()=>{
  const original=fixtures().left;
  for(const mutate of [r=>r.backendResult.provenance.result_id='e'.repeat(32),r=>r.backendResult.provenance.strategy_parameters_sha256='d'.repeat(64),
    r=>r.backendResult.mechanism_config_sha256='c'.repeat(64),r=>r.strategy_parameters.aggressive.text_sensitivity=1.4,r=>r.run_status='running',
    r=>r.analysis_sha256='d'.repeat(64),r=>r.backendResult.audit.passed=false,r=>delete r.published_at]){
    const r=structuredClone(original);mutate(r);assert.throws(()=>api.validateRecord(r));
  }
});

test('legacy default assumptions remain compatible with an explicit default when both runs are complete',()=>{
  const {legacy}=fixtures(),explicit=structuredClone(legacy);explicit.id='c'.repeat(32);explicit.backendResult.provenance.experiment_id=explicit.id;
  explicit.market_assumptions={scope:'A',market:0,volatility:.01};explicit.backendResult.market_assumptions=structuredClone(explicit.market_assumptions);
  explicit.backendResult.assumptions.volatility_floor=.01;explicit.backendResult.assumptions.common_market_offset=0;
  assert.equal(api.compare(legacy,explicit).conditions_match,true);
});

test('changing selections invalidates ready snapshots and ignores both late successes and late failures',()=>{
  const {left,right,seed}=fixtures(),s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);const old=s.begin();
  s.select('right',seed.id);const current=s.begin();assert.equal(s.accept(old,left,right),false);assert.equal(s.fail(old,'old error'),false);
  assert.equal(s.accept(current,left,seed),true);assert.equal(s.status,'ready');
  s.select('right','');assert.equal(s.status,'idle');assert.equal(s.comparison,null);assert.equal(api.exportSnapshot(s),null);
  assert.equal(s.select('right','not-an-id'),false);s.select('right',left.id);assert.equal(s.begin(),null);assert.match(s.error,/不同的实验/);
});

test('captured snapshots and comparison exports cannot be altered through the original records or returned export',()=>{
  const {left,right}=fixtures(),s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);assert.equal(s.accept(s.begin(),left,right),true);
  const out=api.exportSnapshot(s);left.title='changed';right.strategy_parameters.aggressive.text_sensitivity=2;
  assert.notEqual(out.left.title,left.title);assert.equal(out.right.strategy_parameters.aggressive.text_sensitivity,1.2);
  out.right.backendResult.paths.with_message.trace.length=0;assert.equal(s.right.backendResult.paths.with_message.trace.length,18);
  assert.equal(out.interpretation,'descriptive_simulation_comparison');assert.equal(out.schema_version,'platform-comparison-v1');
});

test('role cards, input differences and version details are escaped and retain actual zero or small nonzero metrics',()=>{
  const {left,right}=fixtures();right.title='<img src=x onerror=alert(1)>';
  const s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);s.accept(s.begin(),left,right);s.group='baseline';
  const html=api.renderBody(s);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img src=x/);assert.equal((html.match(/收益变化 · 比较 − 基准/g)||[]).length,3);
  assert.match(html,/同一步目标仓位 · 第 5 步/);assert.match(html,/>\+0\.000000</);assert.match(html,/实际成交/);
  s.group='with_message';const active=api.renderBody(s);assert.match(active,/结果版本/);assert.match(active,/价格、成交与后续仓位存在反馈/);
});

test('actual async app loader reads exports, verifies sources and cannot replace a newer selection',async()=>{
  const {left,right,seed}=fixtures(),old=deferred(),s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);
  const c=vm.createContext({comparisonWorkspace:s,state:{page:'compare'},MarketExperimentComparison:{...api,verifySources:r=>api.verifySources(r,webcrypto)},render(){},
    async apiJson(url){if(url.includes(right.id))return old.promise;return {experiment:url.includes(seed.id)?seed:left};}});
  vm.runInContext(app.slice(app.indexOf('  async function loadExperimentComparison(){'),app.indexOf('  function includeInComparison(')),c);
  const pending=c.loadExperimentComparison();await next();s.select('right',seed.id);await c.loadExperimentComparison();assert.equal(s.right.id,seed.id);
  old.resolve({experiment:right});await pending;assert.equal(s.right.id,seed.id);assert.equal(s.status,'ready');
  s.select('right',right.id);const bad=structuredClone(right);bad.source+='changed';c.apiJson=async url=>({experiment:url.includes(right.id)?bad:left});
  await c.loadExperimentComparison();assert.equal(s.status,'error');assert.equal(s.comparison,null);assert.match(s.error,/原文内容/);
});

test('opening details preserves the exact compared version and a strategy variant keeps source and conditions',()=>{
  const {left,right}=fixtures(),s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);s.accept(s.begin(),left,right);s.step=7;s.asset='B';s.group='baseline';
  let copied;const c=vm.createContext({comparisonWorkspace:s,state:{page:'compare',wizard:1},batchArchivedExperiment:null,navigate(page){c.state.page=page;},
    useSample(index,record){copied=record;c.state.draft={title:record.title};},persistDraft(){},render(){},toast(){}});
  vm.runInContext(app.slice(app.indexOf('  function createStrategyVariant('),app.indexOf('  function sourcesPage(){')),c);
  c.openComparisonSnapshot(right.id);assert.equal(c.batchArchivedExperiment.result_id,right.result_id);assert.equal(c.batchArchivedExperiment.comparison_archive.result_id,right.result_id);
  assert.equal(c.state.step,7);assert.equal(c.state.asset,'B');assert.equal(c.state.decisionGroup,'baseline');assert.equal(c.state.page,'analysis');
  c.createStrategyVariant(left);assert.equal(copied.source,left.source);assert.equal(copied.seed,left.seed);assert.equal(c.state.wizard,2);
  assert.equal(c.state.draft.title,left.title+' · 策略调整');assert.notEqual(c.state.draft.title,left.title);
  assert.equal(c.batchArchivedExperiment,null);
});

test('multi-digit step editing keeps the input alive and invalid values preserve the last actual comparison',()=>{
  const {left,right}=fixtures(),s=new api.ComparisonState();s.select('left',left.id);s.select('right',right.id);s.accept(s.begin(),left,right);
  const error={textContent:''},heading={textContent:''},cards={innerHTML:''},attributes={},input={value:'',dataset:{comparisonStep:''},setAttribute(k,v){attributes[k]=v;}};
  const c=vm.createContext({comparisonWorkspace:s,MarketExperimentComparison:api,document:{getElementById(id){assert.equal(id,'comparison-step-error');return error;},
    querySelector(selector){return selector.includes(' h2')?heading:cards;}}});
  vm.runInContext(app.slice(app.indexOf('  function updateComparisonStep('),app.indexOf("  document.addEventListener('input',updateComparisonStep)")),c);
  c.updateComparisonStep({target:input});assert.equal(s.step,5);assert.equal(attributes['aria-invalid'],'true');
  input.value='1';c.updateComparisonStep({target:input});assert.equal(s.step,1);
  input.value='12';c.updateComparisonStep({target:input});assert.equal(s.step,12);assert.equal(input.value,'12');
  assert.equal(heading.textContent,'同一步目标仓位 · 第 12 步');assert.equal(cards.innerHTML,api.renderStepCards(s));assert.equal(error.textContent,'');
  const saved=cards.innerHTML;
  for(const value of ['','19','1.5','-1']){input.value=value;c.updateComparisonStep({target:input});assert.equal(s.step,12);assert.equal(cards.innerHTML,saved);assert.match(error.textContent,/仍为第 12 步/);}
  input.value='18';c.updateComparisonStep({target:input});assert.equal(s.step,18);assert.equal(attributes['aria-invalid'],'false');assert.equal(error.textContent,'');
});
