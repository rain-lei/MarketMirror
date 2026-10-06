const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('design/app.js','utf8');
const helpers=source.slice(source.indexOf('  function draftPayload('),source.indexOf('  async function hydrateExperiments(){'));
const preview=source.slice(source.indexOf('  async function previewRun(){'),source.indexOf('  function savedPriceChart('));
const retry=source.slice(source.indexOf('  async function retryRun(){'),source.indexOf('  function draftPayload('));
const navigate=source.split('\n').find(line=>line.startsWith('  function navigate('));
const input=source.slice(source.indexOf('  function updateDraftField('),source.indexOf("  document.addEventListener('input',updateDraftField)"));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function draft(title='原实验'){
  return {title,source:'公司回复：项目正在办理审批，尚未获得正式批复，投产时间不确定。',type:'公司问答',
    published:'2026-10-05T09:00',signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000,
    strategy_parameters:{aggressive:{text_sensitivity:.9}}};
}
function record(options){return {id:options.headers['Idempotency-Key'],...JSON.parse(options.body),run_status:'not_started',run_attempts:0};}
function result(id){return {mode:'synthetic_market',generated_at:'2026-10-06T00:00:00Z',audit:{passed:true},
  provenance:{experiment_id:id,result_id:'version-'+id,run_attempts:1}};}
function context(options={}){
  const elements=new Map(),renders=[],notices=[],calls=[];let serial=0;
  for(const name of ['confirm-demo','form-error','draft-submission-panel','draft-submit-control'])elements.set(name,{checked:true,textContent:'',innerHTML:''});
  const c=vm.createContext({experiments:[{id:'existing',custom:true,title:'已有实验',signal:0}],
    state:{page:'new',wizard:3,selected:0,draft:draft()},analysisBinding:{pending:false,analysisId:null,setSource(){}},
    draftSubmissions:new WeakMap(),draftConfirmations:new WeakMap(),activeRuns:new Set(),navigationVersion:0,
    experimentLoadVersion:0,experimentLoading:false,experimentsLoaded:true,batchArchivedExperiment:null,
    crypto:{randomUUID:()=>String(++serial).padStart(32,'0')},copyParameters:x=>JSON.parse(JSON.stringify(x)),
    icon:()=>'',esc:String,saveDraft(){},rememberExperiment(){},location:{hash:''},window:{scrollTo(){}},
    document:{getElementById:id=>c.state.page==='new'?elements.get(id)||null:null,
      querySelector:()=>({classList:{remove(){}}})},
    render(){renders.push(c.state.page);},toast(message){notices.push(message);},async hydrateExperiments(){},
    async apiJson(url,request){calls.push({url,request});return request.headers?record(request):result(url.split('/').at(-2));},...options});
  c.current=()=>c.batchArchivedExperiment||c.experiments[c.state.selected];
  vm.runInContext(helpers+preview+retry+navigate+input,c);
  return {c,elements,renders,notices,calls};
}
const turn=()=>new Promise(setImmediate);

test('the original visible confirmation opens its completed result with consistent provenance',async()=>{
  const {c,calls}=context();await c.previewRun();
  assert.equal(c.state.page,'analysis');assert.equal(c.current().run_status,'completed');
  assert.equal(c.current().result_id,c.current().backendResult.provenance.result_id);
  assert.equal(c.current().run_attempts,1);assert.equal(c.activeRuns.size,0);
  assert.equal(calls.filter(x=>x.url==='/api/platform/experiments').length,1);
});

test('double clicks and rerendered controls cannot submit the same pending draft twice',async()=>{
  const create=deferred(),{c,calls}=context();
  c.apiJson=(url,options)=>{calls.push({url,options});return url==='/api/platform/experiments'?create.promise:Promise.resolve(result(url.split('/').at(-2)));};
  const first=c.previewRun();await c.previewRun();
  assert.equal(calls.length,1);assert.match(c.draftSubmitControl(),/disabled/);assert.match(c.draftSubmitControl(),/正在保存/);
  create.resolve(record(calls[0].options));await first;
  assert.equal(calls.filter(x=>x.url==='/api/platform/experiments').length,1);
});

test('leaving and returning during a run preserves page, selected record and current edits',async()=>{
  const create=deferred(),run=deferred(),{c}=context();let saved;
  c.apiJson=(url,options)=>{if(url==='/api/platform/experiments'){saved=record(options);return create.promise;}return run.promise;};
  const task=c.previewRun();c.navigate('cases');create.resolve(saved);await turn();
  assert.equal(c.current().id,'existing');assert.equal(c.state.page,'cases');
  c.navigate('new');c.state.draft.title='编辑中的草稿';run.resolve(result(saved.id));await task;
  assert.equal(c.state.page,'new');assert.equal(c.state.draft.title,'编辑中的草稿');assert.equal(c.current().id,'existing');
  assert.ok(c.experiments.find(e=>e.id===saved.id).backendResult);assert.doesNotMatch(c.draftSubmitControl(),/draft-result/);
});

test('a late old run cannot take over a second draft or its newer completed selection',async()=>{
  const firstRun=deferred(),{c}=context();let firstRecord;
  c.apiJson=async(url,options)=>{
    if(url==='/api/platform/experiments'){const value=record(options);if(!firstRecord)firstRecord=value;return value;}
    const id=url.split('/').at(-2);return id===firstRecord.id?firstRun.promise:result(id);
  };
  const first=c.previewRun();await turn();const originalDraft=c.state.draft;
  c.state.draft=draft('第二份草稿');c.navigationVersion++;await c.previewRun();const newer=c.current().id;
  firstRun.resolve(result(firstRecord.id));await first;
  assert.equal(c.state.page,'analysis');assert.equal(c.current().id,newer);assert.notEqual(newer,firstRecord.id);
  assert.equal(c.state.draft.title,'第二份草稿');assert.equal(c.draftSubmissions.get(originalDraft).status,'completed');
  assert.equal(c.experiments.filter(e=>e.backendResult).length,2);
});

test('source, model association and nested strategy values are frozen before saving',async()=>{
  const create=deferred(),{c}=context();let body,saved;
  c.analysisBinding.analysisId='analysis-original';
  c.apiJson=(url,options)=>{if(url==='/api/platform/experiments'){body=options.body;saved=record(options);return create.promise;}return Promise.resolve(result(saved.id));};
  const task=c.previewRun();c.state.draft.source='另一份原文';c.state.draft.strategy_parameters.aggressive.text_sensitivity=1.6;c.analysisBinding.analysisId='analysis-new';
  create.resolve(saved);await task;
  const payload=JSON.parse(body);assert.equal(payload.source,draft().source);assert.equal(payload.analysis_id,'analysis-original');
  assert.equal(payload.strategy_parameters.aggressive.text_sensitivity,.9);assert.equal(c.state.page,'new');
});

test('an error after leaving the form is handled and the saved failure can be recovered',async()=>{
  const run=deferred(),{c}=context();let saved,refreshes=0;
  c.apiJson=async(url,options)=>url==='/api/platform/experiments'?(saved=record(options)):run.promise;
  c.hydrateExperiments=async()=>{refreshes++;c.upsertExperiment({...saved,run_status:'failed',run_error:{message:'已确认失败'}});};
  const task=c.previewRun();await turn();c.navigate('agents');run.reject(new Error('connection lost'));await task;
  assert.equal(c.state.page,'agents');assert.equal(refreshes,1);assert.equal(c.activeRuns.size,0);
  c.navigate('new');assert.match(c.draftSubmitControl(),/draft-result/);assert.match(c.draftSubmissionPanel(),/运行失败/);
  await c.previewRun();assert.equal(c.state.page,'analysis');assert.equal(c.current().id,saved.id);
});

test('lost save responses reuse the same submission id and original payload on retry',async()=>{
  const {c}=context(),requests=[];let stored;
  c.apiJson=async(url,options)=>{
    if(url==='/api/platform/experiments'){requests.push(options);if(!stored){stored=record(options);throw new Error('reply lost');}return stored;}
    return result(stored.id);
  };
  await c.previewRun();assert.match(c.draftSubmitControl(),/重试保存并运行/);assert.equal(c.state.page,'new');
  await c.previewRun();assert.equal(requests.length,2);
  assert.equal(requests[0].headers['Idempotency-Key'],requests[1].headers['Idempotency-Key']);
  assert.equal(requests[0].body,requests[1].body);assert.equal(c.experiments.filter(e=>e.id===stored.id).length,1);
});

test('a lost run response uses confirmed server success and never creates or runs it again',async()=>{
  const {c}=context();let saved,posts=0;
  c.apiJson=async(url,options)=>{posts++;if(url==='/api/platform/experiments')return saved=record(options);throw new Error('result reply lost');};
  c.hydrateExperiments=async()=>c.completeExperiment(saved,result(saved.id));
  await c.previewRun();assert.equal(c.state.page,'new');assert.match(c.draftSubmitControl(),/查看已保存结果/);
  await c.previewRun();assert.equal(posts,2);assert.equal(c.current().id,saved.id);assert.equal(c.state.page,'analysis');
});

test('idempotent save retries read an already completed result instead of rerunning it',async()=>{
  const {c}=context(),calls=[];let saved;
  c.apiJson=async(url,options)=>{calls.push(url);if(options){saved={...record(options),run_status:'completed',result_id:'version-'+options.headers['Idempotency-Key']};return saved;}return {experiment:{...saved,backendResult:result(saved.id)}};};
  await c.previewRun();assert.equal(calls.length,2);assert.ok(calls[1].endsWith('/export'));assert.equal(c.current().run_status,'completed');
});

test('a result arriving after a list replacement restores its record with a valid selection',async()=>{
  const run=deferred(),{c}=context();let saved;
  c.apiJson=async(url,options)=>url==='/api/platform/experiments'?(saved=record(options)):run.promise;
  const task=c.previewRun();await turn();c.experiments.splice(0,c.experiments.length);c.state.selected=0;
  run.resolve(result(saved.id));await task;assert.equal(c.current().id,saved.id);assert.equal(c.state.selected,0);assert.equal(c.state.page,'analysis');
});

test('retry completion refreshes saved data without rerendering an unrelated draft',async()=>{
  const run=deferred(),{c,renders}=context();let refreshes=0;
  c.state.page='analysis';c.experiments[0].run_status='failed';c.apiJson=()=>run.promise;
  c.hydrateExperiments=async()=>{refreshes++;};const task=c.retryRun();c.navigate('new');renders.length=0;
  c.state.draft.title='正在输入的实验';run.resolve(result('existing'));await task;
  assert.equal(c.state.page,'new');assert.equal(c.state.draft.title,'正在输入的实验');assert.deepEqual(renders,[]);assert.equal(refreshes,1);
});

test('draft fields and confirmation remain attached only to the current draft',()=>{
  const {c}=context();const owner=c.state.draft;
  c.updateDraftField({target:{id:'draft-title',dataset:{draft:'title'},type:'text',value:'即时编辑'}});
  c.updateDraftField({target:{id:'draft-duration',dataset:{draft:'duration'},type:'select-one',value:'3'}});
  c.updateDraftField({target:{id:'confirm-demo',dataset:{},checked:true}});
  assert.equal(c.state.draft.title,'即时编辑');assert.equal(c.draftPayload().duration,3);assert.equal(c.draftConfirmations.get(owner),true);
  c.state.draft=draft('另一份');assert.equal(c.draftConfirmations.get(c.state.draft),undefined);
});

test('mismatched run responses cannot become completed results',async()=>{
  const {c}=context();let saved;
  c.apiJson=async(url,options)=>url==='/api/platform/experiments'?(saved=record(options)):result('other');
  await c.previewRun();const value=c.experiments.find(e=>e.id===saved.id);
  assert.equal(value.backendResult,null);assert.equal(value.run_status,'unknown');assert.equal(c.state.page,'new');
});

test('wrong source, strategy or model hashes never become accepted results',()=>{
  const {c}=context();
  for(const key of ['source_sha256','strategy_parameters_sha256','analysis_sha256']){
    const value={id:'experiment',[key]:'expected'},reply=result('experiment');reply.provenance[key]='other';
    assert.throws(()=>c.completeExperiment(value,reply),/依据/);
  }
  assert.equal(c.experiments.length,1);
});

test('opening a submission cannot navigate after the user leaves and returns while it loads',async()=>{
  const read=deferred(),{c}=context(),owner=c.state.draft;
  c.draftSubmissions.set(owner,{draft:owner,record:{id:'missing'},payload:draft(),signature:JSON.stringify(c.draftPayload()),status:'completed'});
  c.hydrateExperiments=()=>read.promise;
  const open=c.openSubmittedExperiment();c.navigate('cases');c.navigate('new');c.experiments.push({id:'missing',custom:true});read.resolve();await open;
  assert.equal(c.state.page,'new');assert.equal(c.current().id,'existing');
});

test('a completed record without its result offers recovery instead of claiming a saved result',()=>{
  const {c}=context(),owner=c.state.draft;
  c.draftSubmissions.set(owner,{draft:owner,record:{id:'missing',backendResult:null},payload:draft(),signature:JSON.stringify(c.draftPayload()),status:'completed'});
  assert.match(c.draftSubmitControl(),/查看实验并恢复/);assert.doesNotMatch(c.draftSubmitControl(),/查看已保存结果/);
});
