const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const Scenario=require('../design/experiment-scenario.js');
const {DraftCache,matchesRecord}=require('../design/draft-cache.js');
const {parametersFromExperiment}=require('../design/strategy-state.js');
const app=fs.readFileSync('design/app.js','utf8');
const parameters=Object.fromEntries(['aggressive','conservative','institutional'].map(role=>[role,{text_sensitivity:.5,base_weight:.2,risk_budget:.01}]));
function preview(){return {status:'ready',result:{input:{parameters:structuredClone(parameters),scenario:{signal:.35791,uncertainty:.14567,market:-.37,volatility:.0283,scope:'public',history:'positive'}},mechanism_config_sha256:'a'.repeat(64)}};}
function draft(){return {title:'预览实验',source:'人工假设情景，用于验证市场参数与策略配置完整保留。',type:'政策消息',published:'2026-10-07T09:00',signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000,strategy_parameters:structuredClone(parameters)};}
function storage(){const m=new Map();return {getItem:k=>m.get(k)??null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)};}
function payload(d){const c=vm.createContext({state:{draft:d},analysisBinding:{analysisId:null},copyParameters:structuredClone});vm.runInContext(app.slice(app.indexOf('  function draftPayload('),app.indexOf('  function submissionMatches(')),c);return c.draftPayload();}

test('handoff retains all economic inputs and the artificial history as a separate reference',()=>{
  const p=preview(),config=Scenario.fromPreview(p);
  assert.equal(config.signal,.35791);assert.equal(config.uncertainty,.14567);
  assert.deepEqual(config.market_assumptions,{scope:'public',market:-.37,volatility:.0283});
  assert.equal(config.preview_reference.scenario.history,'positive');
  assert.deepEqual(config.strategy_parameters,parameters);
  config.strategy_parameters.aggressive.text_sensitivity=1.7;config.preview_reference.scenario.history='negative';
  assert.equal(p.result.input.parameters.aggressive.text_sensitivity,.5);assert.equal(p.result.input.scenario.history,'positive');
  for(const status of ['idle','loading','invalid','error'])assert.equal(Scenario.fromPreview({...p,status}),null);
});

test('nested inputs are frozen in the submission and restored from the draft cache',()=>{
  const d={...draft(),...Scenario.fromPreview(preview())},original=payload(d),signature=JSON.stringify(original),cache=new DraftCache(storage());
  assert.equal(cache.write({draft:d,wizard:3,submission:{id:'b'.repeat(32),payload:original,signature}}),true);
  const loaded=cache.read();assert.equal(loaded.submission.signature,signature);assert.deepEqual(loaded.draft.market_assumptions,d.market_assumptions);
  d.market_assumptions.market=1;d.preview_reference.scenario.history='negative';
  assert.equal(original.market_assumptions.market,-.37);assert.equal(original.preview_reference.scenario.history,'positive');
  assert.equal(loaded.submission.payload.market_assumptions.market,-.37);
});

test('record identity includes scope, market offset, floor and the source preview',()=>{
  const d={...draft(),...Scenario.fromPreview(preview())},p=payload(d),record={...structuredClone(p),title:p.title.trim()};
  assert.equal(matchesRecord(p,record),true);
  for(const [key,value] of [['scope','A'],['market',0],['volatility',.03]]){
    const wrong=structuredClone(record);wrong.market_assumptions[key]=value;assert.equal(matchesRecord(p,wrong),false);
  }
  const wrong=structuredClone(record);wrong.preview_reference.scenario.history='first';assert.equal(matchesRecord(p,wrong),false);
  delete wrong.preview_reference;assert.equal(matchesRecord(p,wrong),false);
  const old=payload(draft());assert.equal(Object.hasOwn(old,'market_assumptions'),false);assert.equal(Object.hasOwn(old,'preview_reference'),false);
  assert.equal(matchesRecord(old,{...old,title:old.title.trim()}),true);
  assert.equal(matchesRecord(old,{...old,title:old.title.trim(),market_assumptions:Scenario.defaults}),false);
});

test('copying a new experiment preserves its assumptions while a legacy copy clears stale extra fields',()=>{
  const original={...payload({...draft(),...Scenario.fromPreview(preview())})},c=vm.createContext({experiments:[original],state:{draft:{...draft(),...Scenario.fromPreview(preview())}},parametersFromExperiment,
    strategyWorkspace:{parameters:null},analysisBinding:{reset(){}},navigate(){}});
  vm.runInContext(app.split('\n').find(line=>line.startsWith('  function useSample(')),c);c.useSample(0);
  assert.equal(c.state.draft.market_assumptions.market,-.37);c.state.draft.market_assumptions.market=1;assert.equal(original.market_assumptions.market,-.37);
  delete original.market_assumptions;delete original.preview_reference;c.useSample(0);
  assert.equal(Object.hasOwn(c.state.draft,'market_assumptions'),false);assert.equal(Object.hasOwn(c.state.draft,'preview_reference'),false);
});

test('empty and malformed assumptions or references cannot replace a valid cached draft',()=>{
  const memory=storage(),cache=new DraftCache(memory),d={...draft(),...Scenario.fromPreview(preview())};
  assert.equal(cache.write({draft:d,wizard:2}),true);
  for(const value of [null,{},[],{...d.market_assumptions,volatility:0},{...d.market_assumptions,market:NaN},{...d.market_assumptions,market:true},{...d.market_assumptions,scope:'D'}]){
    assert.equal(cache.write({draft:{...d,market_assumptions:value},wizard:2}),false);assert.equal(cache.read().draft.market_assumptions.market,-.37);
  }
  assert.equal(cache.write({draft:{...d,preview_reference:{...d.preview_reference,mechanism_config_sha256:'bad'}},wizard:2}),false);
});

test('precise numeric edits update the visual range without losing canonical precision; empty stays invalid',()=>{
  const number={value:'0.35791',dataset:{scenarioNumber:'signal'},attrs:{},setAttribute(k,v){this.attrs[k]=v;}},slider={value:'0',style:{setProperty(k,v){this[k]=v;}}};
  const document={getElementById:()=>number,querySelector:()=>slider};
  assert.equal(Scenario.syncSignalInput(number,document),.35791);assert.equal(slider.value,'0.35791');assert.equal(number.value,'0.35791');
  assert.equal(slider.style['--parameter-fill'],'67.8955%');
  number.value='';assert.ok(Number.isNaN(Scenario.syncSignalInput(number,document)));assert.equal(number.attrs['aria-invalid'],'true');assert.equal(slider.value,'0.35791');
  const moved={value:'-.125',dataset:{signalSlider:'signal'},setAttribute(){}};assert.equal(Scenario.syncSignalInput(moved,document),-.125);assert.equal(number.value,'-.125');
});

test('actual draft input handlers preserve percent units and refresh the frozen payload',()=>{
  const d={...draft(),...Scenario.fromPreview(preview())},c=vm.createContext({state:{page:'new',draft:d},MarketExperimentScenario:Scenario,updateDraftSubmission(){this.calls=(this.calls||0)+1;}});
  vm.runInContext(app.slice(app.indexOf('  function updateDraftField(e){'),app.indexOf("  document.addEventListener('input',updateDraftField)")),c);
  const input={value:'3.5',dataset:{assumption:'volatility'},setAttribute(){}};c.updateDraftField({target:input});assert.equal(d.market_assumptions.volatility,.035);
  assert.equal(payload(d).market_assumptions.volatility,.035);assert.equal(d.preview_reference.scenario.volatility,.0283);
  input.value='';c.updateDraftField({target:input});assert.ok(Number.isNaN(d.market_assumptions.volatility));
});

test('the confirmation and result explain the full-run floor and fresh history, preserving the original preview',()=>{
  const d={...draft(),...Scenario.fromPreview(preview())};
  assert.match(Scenario.renderFields(d),/data-assumption="scope"/);assert.match(Scenario.renderFields(d),/下限/);
  assert.match(Scenario.renderSummary(d),/全部三资产.*-0.37.*2.83%/);
  const text=Scenario.renderReference(d);assert.match(text,/此前连续正向 2 步/);assert.match(text,/完整实验从第 1 步重新积累观察记忆/);
  d.signal=-.6;assert.match(Scenario.renderReference(d),/当前实验已调整/);assert.match(Scenario.renderReference(d),/信号 0.35791/);
});
