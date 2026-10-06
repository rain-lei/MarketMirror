const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('design/app.js','utf8');
const hydrate=source.slice(source.indexOf('  async function hydrateExperiments(){'),source.indexOf('  async function previewRun(){'));
const rowsFunction=source.slice(source.indexOf('  function experimentRows(){'),source.indexOf('  function experimentsPage(){'));
const analysisFunction=source.slice(source.indexOf('  function analysis() {'),source.indexOf('  function experimentRows(){'));
const example={id:'example',title:'虚构消息模板',type:'政策消息',seed:7};
function snapshot(id,mode='synthetic_market'){
  return {experiment:{id,title:id,type:'政策消息',source:'消息原文',signal:0,uncertainty:0,sessions:1,
    seed:7,run_status:'completed',result_id:'version-'+id,backendResult:{mode,provenance:{experiment_id:id,result_id:'version-'+id}}}};
}
function context(options={}){
  const value=vm.createContext({experiments:[{...example}],state:{page:'experiments',selected:0,search:'',experimentView:'saved'},
    rememberedExperimentId:null,experimentLoadError:'',experimentLoadVersion:0,experimentLoading:true,experimentsLoaded:false,
    render(){},esc:String,icon:()=>'',heading:(a,b)=>b,newButton:()=>'',batchArchivedExperiment:null,...options});
  value.current=()=>value.experiments[value.state.selected];
  vm.runInContext(hydrate+rowsFunction+analysisFunction,value);
  return value;
}

test('one unreadable snapshot does not hide healthy records and retry recovers it',async()=>{
  let broken=true;const calls=[];
  const c=context({rememberedExperimentId:'good',async apiJson(url){
    calls.push(url);if(url==='/api/platform/experiments')return [{id:'bad'},{id:'good'}];
    if(url.includes('/bad/')&&broken)throw new Error('unreadable');
    return snapshot(url.split('/').at(-2));
  }});
  await c.hydrateExperiments();
  assert.equal(c.experiments.length,2);assert.equal(c.current().id,'good');
  assert.match(c.experimentLoadError,/bad/);assert.equal(c.current().kind,'neutral');
  assert.equal(c.current().sessions,1);assert.equal(c.current().signal,0);
  assert.ok(calls.every(url=>url==='/api/platform/experiments'||url.endsWith('/export')));
  broken=false;await c.hydrateExperiments();
  assert.equal(c.experiments.length,3);assert.equal(c.experimentLoadError,'');assert.equal(c.current().id,'good');
});

test('first visit selects a completed market run and keeps templates out of the saved table',async()=>{
  const c=context({async apiJson(url){return url==='/api/platform/experiments'?[{id:'preview'},{id:'market'}]:snapshot(url.split('/').at(-2),url.includes('/preview/')?'platform_preview':'synthetic_market');}});
  await c.hydrateExperiments();assert.equal(c.current().id,'market');
  const saved=c.experimentRows();assert.match(saved,/market/);assert.doesNotMatch(saved,/虚构消息模板/);
  c.rememberedExperimentId='example';c.state.selected=0;await c.hydrateExperiments();assert.equal(c.current().id,'market');
  c.rememberedExperimentId='preview';await c.hydrateExperiments();assert.equal(c.current().id,'preview');
  c.state.experimentView='templates';const templates=c.experimentRows();
  assert.match(templates,/示例模板 · 虚构文本/);assert.match(templates,/data-use=/);
  assert.doesNotMatch(templates,/data-open=|已完成/);
});

test('snapshot reads are bounded even for a large workspace',async()=>{
  let active=0,maximum=0;
  const c=context({async apiJson(url){
    if(url==='/api/platform/experiments')return Array.from({length:13},(_,i)=>({id:'run'+i}));
    active++;maximum=Math.max(maximum,active);
    await new Promise(resolve=>setTimeout(resolve,5));active--;
    return snapshot(url.split('/').at(-2));
  }});
  await c.hydrateExperiments();assert.equal(maximum,4);assert.equal(c.experiments.filter(e=>e.custom).length,13);
  assert.equal(c.experimentLoadError,'');
});

test('late list responses cannot erase a newer snapshot selection or loading status',async()=>{
  let resolveFirst,lists=0;
  const c=context({apiJson(url){if(url==='/api/platform/experiments')return ++lists===1?new Promise(resolve=>resolveFirst=resolve):Promise.resolve([{id:'new'}]);return Promise.resolve(snapshot('new'));}});
  const old=c.hydrateExperiments();await c.hydrateExperiments();resolveFirst([]);await old;
  assert.equal(c.current().id,'new');assert.equal(c.experimentLoading,false);assert.equal(c.experimentLoadError,'');
});

test('creating a new experiment invalidates an older list load and preserves the new local record',async()=>{
  let resolveList;
  const c=context({state:{page:'new',selected:0,draft:{source:'新的消息原文'}},
    analysisBinding:{analysisId:null},copyParameters:()=>null,
    apiJson(url,options){
      if(url==='/api/platform/experiments')return options?Promise.resolve({...snapshot('created').experiment,backendResult:undefined}):new Promise(resolve=>resolveList=resolve);
      return Promise.resolve(snapshot('created').experiment.backendResult);
    }});
  const persist=source.split('\n').find(line=>line.startsWith('  async function persistAndRun()'));
  vm.runInContext(persist,c);
  const old=c.hydrateExperiments();await c.persistAndRun();resolveList([]);await old;
  assert.equal(c.experiments[0].id,'created');assert.equal(c.experimentsLoaded,true);assert.equal(c.experimentLoading,false);
});

test('a failed refresh preserves the selected known result and a later success clears the error',async()=>{
  let fail=false;
  const c=context({rememberedExperimentId:'selected',async apiJson(url){if(url==='/api/platform/experiments')return [{id:'selected'}];if(fail)throw new Error('fetch failed');return snapshot('selected');}});
  await c.hydrateExperiments();const known=c.current().backendResult;
  fail=true;await c.hydrateExperiments();assert.equal(c.current().backendResult,known);assert.match(c.experimentLoadError,/selected/);
  fail=false;await c.hydrateExperiments();assert.equal(c.experimentLoadError,'');
});

test('records removed from the authoritative list do not remain as ghost experiments',async()=>{
  let id='old';const c=context({async apiJson(url){return url==='/api/platform/experiments'?[{id}]:snapshot(id);}});
  await c.hydrateExperiments();c.rememberedExperimentId='old';id='new';await c.hydrateExperiments();
  assert.equal(c.current().id,'new');assert.equal(c.experiments.some(e=>e.id==='old'),false);
});

test('mismatched result identity, version or source never becomes a completed loaded record',async()=>{
  for(const mutate of [s=>s.experiment.id='wrong',s=>s.experiment.backendResult.provenance.result_id='other',
    s=>{s.experiment.source_sha256='a';s.experiment.backendResult.provenance.source_sha256='b';},s=>s.experiment.backendResult=[]]){
    const c=context({async apiJson(url){if(url==='/api/platform/experiments')return [{id:'run'}];const s=snapshot('run');mutate(s);return s;}});
    await c.hydrateExperiments();assert.equal(c.experiments.filter(e=>e.custom).length,0);assert.match(c.experimentLoadError,/run/);
  }
});

test('missing results and old results without provenance keep their actual status',async()=>{
  const missing=snapshot('missing');missing.experiment.backendResult=null;
  const legacy=snapshot('legacy');delete legacy.experiment.backendResult.provenance;
  const c=context({async apiJson(url){return url==='/api/platform/experiments'?[{id:'missing'},{id:'legacy'}]:url.includes('/missing/')?missing:legacy;}});
  await c.hydrateExperiments();assert.equal(c.current().id,'legacy');assert.equal(c.experiments.find(e=>e.id==='missing').customResultMissing,true);
  assert.match(c.experimentRows(),/结果缺失 · 请刷新/);
});

test('corrupt rows expose no actions or fabricated parameters while healthy rows remain openable',()=>{
  const c=context({experiments:[{id:'broken',title:'损坏',type:'未知',corrupt:true,custom:true,time:'记录损坏'}]});
  const broken=c.experimentRows();assert.equal((broken.match(/<button/g)||[]).length,0);assert.doesNotMatch(broken,/undefined|18 步|信号/);
  c.experiments=[{id:'healthy',title:'正常',type:'政策消息',custom:true,signal:0,uncertainty:0,sessions:18,seed:7,time:'今天'}];
  const healthy=c.experimentRows();assert.equal((healthy.match(/<button\b/g)||[]).length,2);assert.doesNotMatch(healthy,/<button[^>]*<button/);
});

test('the analysis page shows loading or recovery guidance and never substitutes template charts',()=>{
  const c=context();assert.match(c.analysis(),/正在读取本机实验/);assert.doesNotMatch(c.analysis(),/12,800|示例账户收益|price-chart/);
  c.experimentsLoaded=true;assert.match(c.analysis(),/选择一个已保存实验/);
  c.experiments=[{id:'broken',corrupt:true,custom:true}];assert.match(c.analysis(),/实验记录需要恢复/);
  assert.doesNotMatch(c.analysis(),/retry-run|data-action="export"|undefined/);
});
