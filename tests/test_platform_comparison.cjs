const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {execFileSync}=require('node:child_process');
const view=require('../design/decision-view.js');
let cached;
function result(){
  if(!cached)cached=JSON.parse(execFileSync('python',['-B','-X','utf8','-c',
    'import json; from design.engine import run_market; print(json.dumps(run_market(dict(cash=1000000,seed=7,sessions=9,duration=3,signal=.6,uncertainty=.2))))'],
    {cwd:path.resolve(__dirname,'..'),encoding:'utf8',maxBuffer:30*1024*1024}));
  return cached;
}
function independentlyRead(path,asset,step,role){
  const day=path.trace[step-1],names=Object.keys(day.decisions).filter(name=>path.participant_specs[name].parameters.role===role);
  const decisions=names.map(name=>day.decisions[name]),nav=decisions.reduce((sum,d)=>sum+d.nav_minor,0);
  const weighted=key=>decisions.reduce((sum,d)=>sum+d[key][asset]*d.nav_minor,0)/nav;
  const orders=day.portfolio_auction.asset_calls[asset].orders.filter(order=>names.includes(order.owner));
  return {before:weighted('current_weights'),target:weighted('desired_weights'),belief:weighted('beliefs'),
    requested:orders.reduce((sum,o)=>sum+o.quantity,0),accepted:orders.reduce((sum,o)=>sum+o.accepted_quantity,0),filled:orders.reduce((sum,o)=>sum+o.filled_quantity,0)};
}

test('paired decisions match independently read NAV weights and raw order ledgers',()=>{
  const r=result();
  for(const asset of ['A','B','C'])for(const step of [4,5,8])for(const row of view.buildComparison(r,asset,step).roles){
    const a=independentlyRead(r.paths.with_message,asset,step,row.role),b=independentlyRead(r.paths.baseline,asset,step,row.role);
    for(const key of Object.keys(a)){
      assert.ok(Math.abs(row.selected[key]-a[key])<1e-12);
      assert.ok(Math.abs(row.baseline[key]-b[key])<1e-12);
      assert.ok(Math.abs(row.delta[key]-(a[key]-b[key]))<1e-12);
    }
    if(step===4)assert.equal(Object.values(row.delta).every(value=>value===0),true);
  }
});

test('missing role provenance leaves aggregates and differences unavailable while real empty orders remain zero',()=>{
  const r=structuredClone(result());
  delete r.paths.with_message.participant_specs.aggressive_00;
  const unknown=view.buildComparison(r,'A',5);
  assert.equal(unknown.roles.every(row=>!row.selected.coverageComplete&&Object.values(row.delta).every(value=>value===null)),true);
  assert.equal(unknown.roles.every(row=>row.selected.orders===null),true);
  const empty=structuredClone(result());empty.paths.with_message.trace[4].portfolio_auction.asset_calls.A.orders=[];
  assert.equal(view.buildComparison(empty,'A',5).roles.every(row=>row.selected.requested===0&&row.selected.filled===0),true);
  delete empty.paths.with_message.participant_specs;
  assert.equal(view.buildComparison(empty,'A',5).roles.every(row=>row.selected.requested===null),true);
  const missingDecision=structuredClone(result());delete missingDecision.paths.with_message.trace[4].decisions.aggressive_00;
  assert.equal(view.buildComparison(missingDecision,'A',5).roles.every(row=>row.selected.target===null&&row.selected.requested===null),true);
  missingDecision.paths.with_message.trace[4].decisions.aggressive_00=null;
  assert.equal(view.buildComparison(missingDecision,'A',5).roles.every(row=>row.selected.target===null),true);
});

test('small nonzero values stay visible, true zero stays zero and missing values are never formatted as zero',()=>{
  assert.equal(view.formatNumber(.00043875001096704835,6,true),'+0.000439');
  assert.equal(view.formatNumber(-.00013125000328795267,6,true),'-0.000131');
  assert.equal(view.formatNumber(3e-9,6,true),'+3.00e-9');
  assert.equal(view.formatNumber(-3e-9,6,true),'-3.00e-9');
  assert.equal(view.formatNumber(0,6,true),'+0.000000');
  for(const missing of [null,undefined,NaN,Infinity,'0'])assert.equal(view.formatNumber(missing),'未存档');
});

test('paired view shows all three roles, correct condition labels and path-state limits',()=>{
  const html=view.renderComparison(result(),{asset:'B',step:5,activeLabel:'关键词条件',baselineLabel:'无文本参考'});
  assert.equal((html.match(/data-comparison-role=/g)||[]).length,3);
  assert.match(html,/资产 B · 第 5 步/);assert.match(html,/关键词条件与无文本参考/);
  assert.match(html,/请求股数/);assert.match(html,/接受股数/);assert.match(html,/成交股数/);
  assert.match(html,/按各路径的决策前净资产加权/);assert.match(html,/不是固定状态的纯文本效应/);
  assert.doesNotMatch(html,/NaN|undefined/);
  const incomplete=structuredClone(result());delete incomplete.paths.baseline.participant_specs;
  const missing=view.renderComparison(incomplete,{step:5});
  assert.match(missing,/部分账户的角色信息缺失/);assert.match(missing,/未存档/);
  const escaped=view.renderComparison(result(),{activeLabel:'<script>bad()</script>'});
  assert.doesNotMatch(escaped,/<script>/);assert.match(escaped,/&lt;script&gt;/);
});

const source=fs.readFileSync('design/app.js','utf8');
test('market results include the paired view and preserve the archived return difference precision',()=>{
  const r=result(),record={title:'对照实验',id:'example',source:'明确的合成消息',signal:.6,uncertainty:.2,duration:3,seed:7,backendResult:r};
  const c=vm.createContext({state:{asset:'A',step:5,decisionGroup:'with_message'},current:()=>record,
    MarketDecisionView:view,batchArchivedExperiment:null,esc:String,icon:()=>'',newButton:()=>'',
    format:n=>n.toLocaleString('zh-CN'),signed:n=>view.formatNumber(n,6,true),heading:()=>'',
    experimentStrategyPanel:()=>'',experimentEvidence:()=>'',savedPriceChart:()=>'',marketStepCards:()=>''});
  const method=source.slice(source.indexOf('  function marketAnalysis(){'),source.indexOf('  function savedAnalysis(){'));
  vm.runInContext(method,c);const html=c.marketAnalysis();
  assert.match(html,/id="market-comparison"/);assert.equal((html.match(/data-comparison-role=/g)||[]).length,3);
  for(const role of ['aggressive','conservative','institutional']){
    const difference=(r.paths.with_message.summary.role_wealth_multiple[role]-r.paths.baseline.summary.role_wealth_multiple[role])*100;
    assert.ok(html.includes(view.formatNumber(difference,6,true)+' pp'));
  }
});

test('market and source-case sliders update the comparison to the newly selected step',()=>{
  const r=result(),elements=new Map(),handlers=[];
  const c=vm.createContext({state:{asset:'A',step:4,decisionGroup:'with_message'},current:()=>({backendResult:r}),
    caseWorkspace:{detail:{result:r},asset:'B',step:4,group:'baseline'},MarketDecisionView:view,
    MarketCaseView:{renderClock:()=>''},marketStepCards:()=>'',
    document:{addEventListener(type,handler){handlers.push(handler);},getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',innerHTML:''});return elements.get(id);}}});
  for(const prefix of ["  document.addEventListener('input',e=>{if(e.target.id==='case-step'", "  document.addEventListener('input',e=>{if(e.target.id==='draft-source')"]){
    const line=source.split('\n').find(value=>value.startsWith(prefix));assert.ok(line);vm.runInContext(line,c);
  }
  for(const handler of handlers)handler({target:{id:'market-step',value:'5',dataset:{}}});
  assert.match(elements.get('market-comparison').innerHTML,/资产 A · 第 5 步/);
  for(const handler of handlers)handler({target:{id:'case-step',value:'8',dataset:{}}});
  assert.match(elements.get('case-comparison').innerHTML,/资产 B · 第 8 步/);
  assert.match(elements.get('case-comparison').innerHTML,/所选条件与无文本参考/);
});

test('a source-case wrapper supplies the real paired paths and archive labels',()=>{
  const r=result(),c=vm.createContext({caseWorkspace:{detail:{result:r},asset:'C',step:5,group:'baseline'},
    MarketDecisionView:view,MarketCaseView:{renderDetail:(bundle,state,parts)=>parts},savedPriceChart:()=>'',marketStepCards:()=>''});
  const method=source.slice(source.indexOf('  function caseDetail(){'),source.indexOf('  function casesPage(){'));
  vm.runInContext(method,c);const parts=c.caseDetail();
  assert.match(parts.comparison,/资产 C · 第 5 步/);assert.match(parts.comparison,/所选条件与无文本参考/);
});
