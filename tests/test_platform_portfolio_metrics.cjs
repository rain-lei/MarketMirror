const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const metrics=require('../design/portfolio-metrics.js');

// Explicit arithmetic examples, not research observations or simulated returns.
function fixture(){
  const roles={a1:'aggressive',a2:'aggressive',c:'conservative',i:'institutional'};
  const specs=Object.fromEntries(Object.entries(roles).map(([name,role])=>[name,{kind:'strategy',parameters:{role,risk_budget:.004,max_weight:.5}}]));
  specs.background={kind:'background'};
  const assets=['A','B','C'],wealths=[{a1:12000,a2:10000,c:10000,i:10000},{a1:10000,a2:12000,c:10000,i:10000},{a1:6000,a2:12000,c:10000,i:10000}];
  const trace=wealths.map((wealths,index)=>{
    const accounts=Object.fromEntries(Object.entries(wealths).map(([name,nav])=>{
      const shares=name==='a2'?10:name==='a1'&&index===2?60:0;
      return [name,{wallets:{shared:nav-shares*100},shares:{A:shares,B:0,C:0}}];
    }));
    const order=(owner,q,a,f,fee)=>({owner,quantity:q,accepted_quantity:a,filled_quantity:f,fee_minor:fee});
    const orders=index===0?[order('a1',100,80,40,2),order('a2',20,10,0,0),order('background',100,100,100,999)]:index===1?[order('a1',200,100,50,3)]:[];
    return {covariance:Object.fromEntries(assets.map(a=>[a,Object.fromEntries(assets.map(b=>[b,a===b?.0001:0]))])),
      decisions:Object.fromEntries(Object.keys(roles).map(n=>[n,{asset_weight_cap:.4}])),portfolio_auction:{accounts,
        asset_calls:Object.fromEntries(assets.map(a=>[a,{price_after_minor:100,orders:a==='A'?orders:[]}]))}};
  });
  const accounts=Object.fromEntries(Object.entries(roles).map(([name,role])=>[name,{kind:'strategy',role,initial_wealth_minor:10000,final_wealth_minor:wealths.at(-1)[name]}]));
  accounts.background={kind:'background',role:null};
  const path={participant_specs:specs,summary:{assets,sessions:3,accounts},trace};
  return {provenance:{result_id:'a'.repeat(32)},paths:{with_message:path,baseline:structuredClone(path)}};
}

test('aggregate portfolio drawdown includes initial inventory and is not an average of account drawdowns',()=>{
  const source=fixture(),before=JSON.stringify(source),model=metrics.buildOverview(source),a=model.paths.with_message[0];
  assert.equal(a.initialWealthMinor,20000);
  assert.deepEqual(a.wealthSeries.map(p=>p.wealthMinor),[20000,22000,22000,18000]);
  assert.ok(Math.abs(a.returnFraction-(-.1))<1e-14);
  assert.ok(Math.abs(a.maxDrawdown-2/11)<1e-14);
  assert.notEqual(a.maxDrawdown,.25); // Mean of two individual drawdowns would be 25%.
  assert.equal(a.feesMinor,5); // Background fee 999 is not a role expense.
  assert.deepEqual([a.requested,a.accepted,a.filled],[320,190,90]);
  assert.equal(a.requestedFillFraction,90/320);assert.equal(a.acceptedFillFraction,90/190);
  assert.equal(a.issues.length,0);assert.equal(model.sourceResultId,'a'.repeat(32));
  assert.equal(JSON.stringify(source),before);
});

test('closing exceedance counts account steps separately from distinct steps and keeps the affected accounts',()=>{
  const a=metrics.buildOverview(fixture()).paths.with_message[0];
  for(const diagnostic of [a.riskExceedance,a.concentrationExceedance]){
    assert.equal(diagnostic.accountSteps,1);assert.equal(diagnostic.totalAccountSteps,6);
    assert.equal(diagnostic.stepCount,1);assert.equal(diagnostic.totalSteps,3);
    assert.deepEqual(diagnostic.breaches,[{step:3,accounts:['a1']}]);
  }
  const html=metrics.renderOverview(fixture());
  assert.match(html,/data-risk-step="3" data-risk-group="with_message"/);
  assert.match(html,/data-risk-step="3" data-risk-group="baseline"/);
  assert.match(html,/1 \/ 6/);assert.match(html,/1 \/ 3 步有超限/);
  assert.match(html,/账本审计通过不代表没有超限/);
});

test('an empty but complete order book means no orders, not a fabricated fill percentage',()=>{
  const r=fixture(),row=metrics.buildOverview(r).paths.with_message[1];
  assert.deepEqual([row.requested,row.accepted,row.filled,row.feesMinor],[0,0,0,0]);
  assert.equal(row.requestedFillFraction,null);assert.equal(row.acceptedFillFraction,null);
  assert.equal(row.maxDrawdown,0);assert.equal(row.riskExceedance.accountSteps,0);
  const html=metrics.renderOverview(r);
  assert.match(html,/<th scope="row">成交 \/ 请求<\/th><td>未下单<\/td>/);
  assert.doesNotMatch(html,/NaN|undefined|Infinity/);
});

test('missing holdings, covariance, caps and orders invalidate only metrics that need them',()=>{
  const positions=fixture();delete positions.paths.with_message.trace[1].portfolio_auction.accounts.a1;
  const a=metrics.buildOverview(positions).paths.with_message[0];
  for(const key of ['returnFraction','maxDrawdown','wealthSeries','riskExceedance','concentrationExceedance'])assert.equal(a[key],null);
  assert.equal(a.feesMinor,5);assert.equal(a.filled,90);
  const covariance=fixture();delete covariance.paths.with_message.trace[1].covariance;
  const c=metrics.buildOverview(covariance).paths.with_message[0];
  assert.equal(c.riskExceedance,null);assert.ok(c.wealthSeries);assert.equal(c.concentrationExceedance.accountSteps,1);
  const cap=fixture();delete cap.paths.with_message.trace[1].decisions.a1.asset_weight_cap;
  const d=metrics.buildOverview(cap).paths.with_message[0];
  assert.equal(d.concentrationExceedance,null);assert.equal(d.riskExceedance.accountSteps,1);
  const orders=fixture();delete orders.paths.with_message.trace[1].portfolio_auction.asset_calls.B.orders;
  const e=metrics.buildOverview(orders).paths.with_message[0];
  for(const key of ['requested','accepted','filled','feesMinor','requestedFillFraction'])assert.equal(e[key],null);
  assert.ok(e.wealthSeries);
  assert.match(metrics.renderOverview(orders),/部分指标无法核对/);
});

test('truncated traces, incomplete role rosters and inconsistent final wealth do not produce plausible zero metrics',()=>{
  const truncated=fixture();truncated.paths.with_message.trace.pop();
  const rows=metrics.buildOverview(truncated).paths.with_message;
  assert.equal(rows.every(row=>row.returnFraction===null&&row.filled===null&&row.riskExceedance===null),true);
  const missing=fixture();delete missing.paths.with_message.participant_specs.a1;
  assert.equal(metrics.buildOverview(missing).paths.with_message.every(row=>row.accountCount===null),true);
  const wrong=fixture();wrong.paths.with_message.summary.accounts.a1.final_wealth_minor=6001;
  const a=metrics.buildOverview(wrong).paths.with_message[0];
  assert.equal(a.returnFraction,null);assert.equal(a.maxDrawdown,null);assert.match(a.issues.join(''),/不一致/);
  const invalid=fixture();invalid.paths.with_message.trace[0].portfolio_auction.asset_calls.A.orders[0].filled_quantity=81;
  const b=metrics.buildOverview(invalid).paths.with_message[0];
  assert.equal(b.requested,null);assert.equal(b.requestedFillFraction,null);
});

test('invalid covariance is unavailable rather than zero risk, while singular negative correlation remains valid',()=>{
  const invalidMatrices=[
    [[-.0001,0,0],[0,.0001,0],[0,0,.0001]],
    [[.0001,.00005,0],[0,.0001,0],[0,0,.0001]],
    [[.0001,-.0002,0],[-.0002,.0001,0],[0,0,.0001]]
  ];
  const covariance=values=>Object.fromEntries(['A','B','C'].map((a,i)=>[a,Object.fromEntries(['A','B','C'].map((b,j)=>[b,values[i][j]]))]));
  for(const values of invalidMatrices){
    const r=fixture();r.paths.with_message.trace[0].covariance=covariance(values);
    for(const row of metrics.buildOverview(r).paths.with_message){
      assert.equal(row.riskExceedance,null);assert.ok(row.wealthSeries);assert.notEqual(row.concentrationExceedance,null);
      assert.match(row.issues.join(''),/风险/);
    }
  }
  const r=fixture();for(const day of r.paths.with_message.trace)day.covariance=covariance([[.0001,-.0001,0],[-.0001,.0001,0],[0,0,.0001]]);
  const a=metrics.buildOverview(r).paths.with_message[0];
  assert.equal(a.riskExceedance.accountSteps,1);assert.equal(a.riskExceedance.totalAccountSteps,6);
  assert.deepEqual(a.issues,[]);
});

test('presentation escapes labels and preserves tiny nonzero changes and source identifiers',()=>{
  const r=fixture();r.paths.with_message.summary.trace_sha256='b'.repeat(64);
  const html=metrics.renderOverview(r,{activeLabel:'<img onerror="bad">'});
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);assert.match(html,/风险 \/ 总仓位超限/);
  assert.equal(metrics.buildOverview(r).sourceTraceSha256.with_message,'b'.repeat(64));
  const huge=fixture();
  for(const path of Object.values(huge.paths)){
    for(const summary of Object.values(path.summary.accounts))if(summary.kind==='strategy')summary.initial_wealth_minor=1e12;
    for(const day of path.trace)for(const [name,account] of Object.entries(day.portfolio_auction.accounts)){
      account.wallets.shared=1e12-Object.values(account.shares).reduce((sum,v)=>sum+v*100,0);
      path.summary.accounts[name].final_wealth_minor=1e12;
    }
  }
  huge.paths.with_message.trace.at(-1).portfolio_auction.accounts.a1.wallets.shared+=1;
  huge.paths.with_message.summary.accounts.a1.final_wealth_minor+=1;
  assert.match(metrics.renderOverview(huge),/收益差 \+\d\.\d{2}e-\d+ pp/);
});

test('real engine outputs agree with saved role wealth and independent engine exceedance counters',()=>{
  const script=[
    'import json',
    'from design.engine import run_market',
    "print(json.dumps(run_market(dict(cash=1000000,seed=7,sessions=9,duration=3,signal=-.6,uncertainty=.8,market_assumptions=dict(scope='public',market=-.2,volatility=.1)))))",
  ].join(';');
  const r=JSON.parse(execFileSync('python',['-B','-X','utf8','-c',script],{encoding:'utf8',maxBuffer:30*1024*1024}));
  const report=metrics.buildOverview(r);
  let breached=0;
  for(const group of ['with_message','baseline'])for(const row of report.paths[group]){
    const path=r.paths[group],accounts=Object.values(path.summary.accounts).filter(a=>a.role===row.role);
    assert.equal(row.issues.length,0);
    assert.equal(row.returnFraction,path.summary.role_wealth_multiple[row.role]-1);
    assert.equal(row.riskExceedance.accountSteps,accounts.reduce((sum,a)=>sum+a.risk_breach_sessions,0));
    assert.equal(row.concentrationExceedance.accountSteps,accounts.reduce((sum,a)=>sum+a.concentration_breach_sessions,0));
    breached+=row.riskExceedance.accountSteps;
    const orders=path.trace.flatMap(d=>Object.values(d.portfolio_auction.asset_calls).flatMap(c=>c.orders))
      .filter(o=>path.participant_specs[o.owner].parameters?.role===row.role);
    assert.equal(row.feesMinor,orders.reduce((sum,o)=>sum+o.fee_minor,0));
    assert.equal(row.filled,orders.reduce((sum,o)=>sum+o.filled_quantity,0));
  }
  assert.ok(breached>0);assert.equal((metrics.renderOverview(r).match(/data-risk-role=/g)||[]).length,3);
});

const app=fs.readFileSync('design/app.js','utf8');
test('risk navigation selects the archived group and step while preserving the asset and guarding invalid steps',()=>{
  let renders=0,caseRenders=0,focuses=0;
  const c=vm.createContext({state:{page:'analysis',asset:'C',step:1,decisionGroup:'with_message'},current:()=>({backendResult:fixture()}),
    caseWorkspace:{detail:{result:fixture()},asset:'B',step:1,group:'with_message'},render(){renders++;},updateCasePanels(){caseRenders++;},
    document:{getElementById(){return {closest:()=>({scrollIntoView(){}}),focus(){focuses++;}};}}});
  vm.runInContext(app.slice(app.indexOf('  function jumpToRiskStep('),app.indexOf('  function sourcesPage(){')),c);
  c.jumpToRiskStep(3,'baseline');assert.equal(c.state.step,3);assert.equal(c.state.decisionGroup,'baseline');assert.equal(c.state.asset,'C');
  c.jumpToRiskStep(4,'baseline');c.jumpToRiskStep(1,'wrong');assert.equal(renders,1);
  c.state.page='cases';c.jumpToRiskStep(2,'baseline');assert.equal(c.caseWorkspace.step,2);assert.equal(c.caseWorkspace.group,'baseline');assert.equal(c.caseWorkspace.asset,'B');
  assert.equal(caseRenders,1);assert.equal(focuses,2);
});

test('metric export captures the current saved version without fetching, rerunning or associating a model',()=>{
  const record={id:'record',title:'saved',backendResult:fixture()};let download;
  const c=vm.createContext({state:{page:'analysis'},current:()=>record,MarketPortfolioMetrics:metrics,
    caseWorkspace:{detail:null},showDownloadFiles(...args){download=args;}});
  vm.runInContext(app.slice(app.indexOf('  function openRiskOverviewDownloads(){'),app.indexOf('  function jumpToRiskStep(')),c);
  c.openRiskOverviewDownloads();const snapshot=JSON.parse(download[2][0].content);
  assert.equal(snapshot.source.experiment_id,'record');assert.equal(snapshot.metrics.sourceResultId,'a'.repeat(32));
  assert.equal(snapshot.metrics.paths.with_message[0].riskExceedance.breaches[0].accounts[0],'a1');
  c.state.page='cases';c.caseWorkspace.detail={case:{case_id:'case-source'},seed:7,mode:'keywords',result:fixture()};c.openRiskOverviewDownloads();
  const caseSnapshot=JSON.parse(download[2][0].content);
  assert.deepEqual(caseSnapshot.source,{case_id:'case-source',seed:7,mode:'keywords'});
});
