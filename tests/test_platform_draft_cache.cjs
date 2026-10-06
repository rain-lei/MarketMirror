const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {DraftCache,matchesRecord,KEY}=require('../design/draft-cache.js');
const {SourceAnalysisBinding}=require('../design/analysis-state.js');
const {StrategyParameterControls}=require('../design/strategy-state.js');
const source=fs.readFileSync('design/app.js','utf8');
const cacheFunctions=source.slice(source.indexOf('  function hasDraftContent(){'),source.indexOf('  function updateModelSettings(){'));
const submissionFunctions=source.slice(source.indexOf('  function draftPayload('),source.indexOf('  async function hydrateExperiments(){'));
const preview=source.slice(source.indexOf('  async function previewRun(){'),source.indexOf('  function savedPriceChart('));
function storage(){const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};}
function draft(title='刷新前的草稿'){
  return {title,source:'公司回复：项目仍在办理审批，未获得正式批复，投产时间不确定。',type:'公司问答',published:'2026-10-06T09:00',
    signal:0,uncertainty:0,duration:3,sessions:1,seed:0,cash:10000,strategy_parameters:Object.fromEntries(['aggressive','conservative','institutional'].map(role=>[role,{text_sensitivity:.5,base_weight:.2,risk_budget:.01}]))};
}
function context(memory=storage(),extra={}){
  const elements=new Map();for(const id of ['draft-cache-status','draft-submission-panel','draft-submit-control','form-error','confirm-demo'])elements.set(id,{textContent:'',innerHTML:'',checked:true,classList:{toggle(){}}});
  let serial=0;const calls=[],c=vm.createContext({state:{page:'new',wizard:3,selected:0,draft:draft()},experiments:[],experimentsLoaded:true,
    draftCache:new DraftCache(memory),draftAnalysisRecovery:null,analysisBinding:new SourceAnalysisBinding(draft().source),
    draftSubmissions:new WeakMap(),draftConfirmations:new WeakMap(),activeRuns:new Set(),navigationVersion:0,experimentLoadVersion:0,experimentLoading:false,
    MarketDraftCache:{matchesRecord},copyParameters:x=>JSON.parse(JSON.stringify(x)),document:{getElementById:id=>elements.get(id)||null},
    crypto:{randomUUID:()=>String(++serial).padStart(32,'0')},icon:()=>'',esc:String,saveDraft(){},toast(){},render(){},async hydrateExperiments(){},batchArchivedExperiment:null,
    async apiJson(path,options){calls.push({path,options});throw new Error('unexpected request');},...extra});
  c.current=()=>c.experiments[c.state.selected];c.navigate=page=>{c.state.page=page;c.navigationVersion++;};
  vm.runInContext(cacheFunctions+submissionFunctions+preview,c);
  c.updateDraftAnalysisPanel=()=>c.updateDraftSubmission();
  return {c,elements,calls,memory};
}
function frozenRecord(id,payload,status='not_started'){return {id,...payload,title:payload.title.trim(),run_status:status,custom:true};}
function result(id){return {mode:'synthetic_market',generated_at:'2026-10-06T00:00:00Z',provenance:{experiment_id:id,result_id:'run-'+id,run_attempts:1}};}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
const turn=()=>new Promise(setImmediate);

test('numeric select edits and form saves preserve numbers in the draft cache',()=>{
  const {c}=context(),select={type:'select-one',value:'9',dataset:{draft:'duration'}};
  const method=source.slice(source.indexOf('  function updateDraftField(e){'),source.indexOf("  document.addEventListener('input',updateDraftField)"));
  const save=source.split('\n').find(line=>line.startsWith('  function saveDraft(){'));
  c.document.querySelectorAll=()=>[select];vm.runInContext(method+save,c);
  c.updateDraftField({target:select});assert.equal(c.draftCache.error,'');assert.equal(c.draftCache.read().draft.duration,9);
  select.value='3';c.saveDraft();assert.equal(c.draftCache.read().draft.duration,3);
  assert.equal(c.draftCache.read().draft.seed,0);assert.equal(c.draftCache.read().draft.uncertainty,0);
});

test('editing an experiment strategy immediately saves the new value without changing other roles',()=>{
  const {c,elements}=context(),handlers=[],id='strategy-experiment-aggressive-text_sensitivity';
  elements.set(id+'-value',{textContent:''});c.document.addEventListener=(_,handler)=>handlers.push(handler);
  c.strategyFields=[{key:'text_sensitivity',scale:1}];c.strategyWorkspace={validate:(_role,_key,value)=>value,limits:{aggressive:{text_sensitivity:[0,2]}}};
  c.StrategyParameterControls={...StrategyParameterControls,sync(){},showError(_input,message){throw new Error(message);}};c.document.querySelector=()=>null;
  vm.runInContext(source.slice(source.indexOf('  function handleStrategyInput('),source.indexOf('  function draftStrategyPanel(){')),c);
  const handler=source.split('\n').find(line=>line.startsWith("  document.addEventListener('input',e=>{if(e.target.id==='draft-source')"));
  vm.runInContext(handler,c);handlers[0]({target:{id,value:'0.65',dataset:{strategyScope:'experiment',strategyRole:'aggressive',strategyKey:'text_sensitivity'}}});
  const saved=c.draftCache.read();assert.equal(saved.draft.strategy_parameters.aggressive.text_sensitivity,.65);
  assert.equal(saved.draft.strategy_parameters.conservative.text_sensitivity,.5);assert.equal(saved.draft.strategy_parameters.institutional.text_sensitivity,.5);
});

test('draft roundtrip preserves zero values, independent parameters and analysis identity without storing model output',()=>{
  const memory=storage(),cache=new DraftCache(memory),value=draft();
  assert.equal(cache.write({draft:value,wizard:2,analysisId:'a'.repeat(32)}),true);
  value.strategy_parameters.aggressive.text_sensitivity=1.5;
  const recovered=new DraftCache(memory).read();assert.equal(recovered.wizard,2);assert.equal(recovered.analysis_id,'a'.repeat(32));
  for(const key of ['signal','uncertainty','seed'])assert.equal(recovered.draft[key],0);
  assert.equal(recovered.draft.sessions,1);assert.equal(recovered.draft.strategy_parameters.aggressive.text_sensitivity,.5);
  assert.doesNotMatch(memory.getItem(KEY),/raw_response|"facts"/);
});
test('corrupt drafts and changed submission signatures are rejected without destroying the existing cache',()=>{
  const memory=storage();memory.setItem(KEY,'{broken');const broken=new DraftCache(memory);
  assert.equal(broken.read(),null);assert.equal(memory.getItem(KEY),'{broken');assert.match(broken.error,/未能恢复/);
  const {c}=context(memory);c.persistDraft();const cached=JSON.parse(memory.getItem(KEY));
  cached.submission={id:'a'.repeat(32),payload:c.draftPayload(),signature:'different'};memory.setItem(KEY,JSON.stringify(cached));
  assert.equal(new DraftCache(memory).read(),null);assert.ok(memory.getItem(KEY).includes('different'));
});
test('quota failures stay explicit and clearing a draft preserves unrelated session settings',()=>{
  const memory=storage(),cache=new DraftCache(memory);memory.setItem('marketmirror:selected-batch','batch');
  cache.write({draft:draft(),wizard:1});const previous=memory.getItem(KEY);
  memory.setItem=()=>{throw new Error('quota');};assert.equal(cache.write({draft:draft('new'),wizard:2}),false);
  assert.equal(memory.getItem(KEY),previous);assert.match(cache.error,/无法保存/);
  assert.equal(cache.clear(),true);assert.equal(memory.getItem(KEY),null);assert.equal(memory.getItem('marketmirror:selected-batch'),'batch');
});
test('a reload after a lost save response reuses the original id and reads completed output without another run',async()=>{
  const memory=storage(),old=context(memory);let saved,firstBody,firstId;
  old.c.apiJson=async(path,options)=>{firstBody=options.body;firstId=options.headers['Idempotency-Key'];saved=frozenRecord(firstId,JSON.parse(firstBody),'completed');saved.result_id='run-'+firstId;throw new Error('save reply lost');};
  await old.c.previewRun();assert.equal(old.c.state.page,'new');
  const newer=context(memory);newer.c.restoreDraftFromCache();
  assert.equal(newer.c.state.draft.title,draft().title);assert.equal(newer.c.draftSubmissions.get(newer.c.state.draft).id,firstId);
  newer.c.apiJson=async(path,options)=>{
    newer.calls.push({path,options});
    if(options){assert.equal(path,'/api/platform/experiments');assert.equal(options.headers['Idempotency-Key'],firstId);assert.equal(options.body,firstBody);return saved;}
    return {experiment:{...saved,backendResult:result(firstId)}};
  };
  await newer.c.previewRun();assert.equal(newer.c.current().id,firstId);assert.equal(newer.calls.length,2);
  assert.ok(newer.calls.every(call=>!call.path.endsWith('/run')));
});
test('a restored running submission opens its authoritative record without starting another request',async()=>{
  const {c}=context(),payload=c.draftPayload(),id='b'.repeat(32);
  c.draftCache.write({draft:c.state.draft,wizard:3,submission:{id,payload,signature:JSON.stringify(payload)}});
  c.restoreDraftFromCache();c.experiments.push(frozenRecord(id,payload,'running'));c.syncRestoredDraftSubmission();
  assert.match(c.draftSubmitControl(),/查看运行进度/);await c.previewRun();assert.equal(c.state.page,'analysis');assert.equal(c.current().id,id);
});
test('restoring saved analysis only reads the server and does not accept cached facts or call the model',async()=>{
  const {c,calls}=context(),id='a'.repeat(32);c.draftCache.write({draft:c.state.draft,wizard:2,analysisId:id});
  c.apiJson=async(path,options)=>{calls.push({path,options});return {analysis_id:id,source:draft().source,facts:[]};};
  c.restoreDraftFromCache();await turn();assert.equal(c.analysisBinding.analysisId,id);assert.equal(c.state.wizard,2);
  assert.deepEqual(calls.map(call=>call.path),['/api/platform/analyses/'+id]);assert.equal(calls[0].options,undefined);
});
test('a late restored analysis cannot attach to edited text or a newly created draft',async()=>{
  for(const replace of [false,true]){
    const {c}=context(),response=deferred(),id='a'.repeat(32);c.apiJson=()=>response.promise;
    const task=c.restoreDraftAnalysis(id);
    if(replace)c.state.draft=draft('new');
    c.state.draft.source='另一段新的公司原文，与上一份审批回复没有关联。';c.analysisBinding.reset(c.state.draft.source);c.persistDraft();
    response.resolve({analysis_id:id,source:draft().source,facts:[]});await task;
    assert.equal(c.analysisBinding.analysisId,null);assert.equal(c.draftCache.read().analysis_id,null);
  }
});
test('an unreadable restored analysis blocks submission until it is recovered or explicitly detached',async()=>{
  const {c,calls}=context();c.apiJson=async(path,options)=>{calls.push({path,options});throw new Error('analysis missing');};
  await c.restoreDraftAnalysis('a'.repeat(32));assert.equal(c.analysisBinding.restoreError,true);
  await c.previewRun();assert.equal(calls.length,1);assert.ok(calls[0].path.includes('/analyses/'));
  assert.equal(c.draftCache.read().analysis_id,'a'.repeat(32));
  c.analysisBinding.reset(c.state.draft.source);c.persistDraft();assert.equal(c.draftCache.read().analysis_id,null);
});
test('a restored submission never claims a mismatched server record belongs to its frozen input',()=>{
  const {c}=context(),payload=c.draftPayload(),id='a'.repeat(32);
  c.draftCache.write({draft:c.state.draft,wizard:3,submission:{id,payload,signature:JSON.stringify(payload)}});c.restoreDraftFromCache();
  c.experiments.push({...frozenRecord(id,payload,'completed'),source:'different'});c.syncRestoredDraftSubmission();
  const ticket=c.draftSubmissions.get(c.state.draft);assert.equal(ticket.record,null);assert.match(ticket.error,/配置不一致/);
});
test('completion of an older background submission cannot overwrite the newer draft in the cache',async()=>{
  const {c}=context(),run=deferred();let saved;
  c.apiJson=async(path,options)=>path==='/api/platform/experiments'?(saved=frozenRecord(options.headers['Idempotency-Key'],JSON.parse(options.body))):run.promise;
  const task=c.previewRun();await turn();c.state.draft=draft('新的草稿');c.navigationVersion++;c.analysisBinding.reset(c.state.draft.source);c.persistDraft();
  run.resolve(result(saved.id));await task;
  const recovered=c.draftCache.read();assert.equal(recovered.draft.title,'新的草稿');assert.equal(recovered.submission,null);assert.equal(c.state.page,'new');
});
