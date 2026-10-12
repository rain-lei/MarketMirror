const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {parametersFromExperiment} = require('../design/strategy-state.js');
const source=fs.readFileSync('design/app.js','utf8');
const copy=source.split('\n').find(line=>line.startsWith('  function useSample('));
const clicks=source.slice(source.indexOf("  document.addEventListener('click',async e=>{"),source.indexOf('  function updateDraftField(e){'));
const {SourceAnalysisBinding}=require('../design/analysis-state.js');

function draftActions(){
  let click;const routes=[],error={textContent:''},parameters={aggressive:{text_sensitivity:.9}};
  const context=vm.createContext({
    state:{wizard:3,draft:{title:'之前的消息',source:'原消息的完整公司回复原文，用于检查下一份草稿是否继承来源时间。',published:'2020-01-23T10:00',signal:1}},
    draftCache:{clear(){}},draftAnalysisRecovery:{id:'old'},analysisBinding:new SourceAnalysisBinding('old'),
    strategyWorkspace:{parameters,snapshot:()=>JSON.parse(JSON.stringify(parameters))},
    document:{addEventListener(type,fn){if(type==='click')click=fn;},getElementById:()=>error},
    navigate:page=>routes.push(page),saveDraft(){},render(){},
  });
  vm.runInContext(clicks,context);
  return {context,routes,error,parameters,dispatch:action=>click({target:{closest:selector=>selector==='[data-action]'?{dataset:{action}}:null}})};
}
test('copy preserves zero values, horizon, publication time and independent strategy snapshot',()=>{
  const original={title:'copy',source:'source',type:'公司问答',seed:0,signal:0,uncertainty:0,duration:3,cash:10000,sessions:1,published_at:'2026-10-06T09:00',strategy_parameters:{aggressive:{text_sensitivity:.9}}};
  let boundSource=null,route=null;
  const context=vm.createContext({experiments:[original],state:{draft:{sessions:60,signal:.6,published:'old'},wizard:3},parametersFromExperiment,
    strategyWorkspace:{parameters:null},analysisBinding:{reset(source){boundSource=source;}},navigate(value){route=value;}});
  vm.runInContext(copy,context);
  context.useSample(0);
  for(const key of ['seed','signal','uncertainty','duration','cash','sessions'])assert.equal(context.state.draft[key],original[key]);
  assert.equal(context.state.draft.published,original.published_at);
  context.state.draft.strategy_parameters.aggressive.text_sensitivity=1.5;
  assert.equal(original.strategy_parameters.aggressive.text_sensitivity,.9);
  assert.equal(boundSource,original.source);
  assert.equal(route,'new');
  assert.equal(context.state.wizard,1);
});

test('a source without publication metadata cannot inherit the previous source date',()=>{
  const template={title:'虚构消息',source:'示例公司的审批进度仍不确定，未获得正式批复。',type:'公司问答',seed:7,kind:'uncertain'};
  const context=vm.createContext({experiments:[template],state:{draft:{published:'2020-01-23T10:00'},wizard:3},parametersFromExperiment,
    strategyWorkspace:{parameters:null},analysisBinding:{reset(){}},navigate(){}});
  vm.runInContext(copy,context);context.useSample(0);
  assert.equal(context.state.draft.published,'');
  assert.equal(context.state.draft.source,template.source);
});

test('new messages reset source time while keeping independent workspace strategy defaults',async()=>{
  const {context,routes,parameters,dispatch}=draftActions();
  context.analysisBinding.begin('old');
  await dispatch('new');
  assert.equal(context.state.draft.published,'');
  assert.equal(context.state.draft.source,'');
  assert.equal(context.state.draft.title,'');
  assert.equal(context.state.wizard,1);
  assert.equal(context.analysisBinding.pending,false);
  assert.equal(context.draftAnalysisRecovery,null);
  assert.deepEqual(routes,['new']);
  context.state.draft.strategy_parameters.aggressive.text_sensitivity=1.8;
  assert.equal(parameters.aggressive.text_sensitivity,.9);
});

test('initial and newly created drafts require an explicit source time before advancing',async()=>{
  const init=source.split('\n').find(line=>line.startsWith('  const state ='));
  const initial=vm.createContext({});vm.runInContext(init+'globalThis.draft=state.draft;',initial);
  assert.equal(initial.draft.published,'');
  const {context,error,dispatch}=draftActions();await dispatch('new');
  context.state.draft.title='审批信息';context.state.draft.source='公司的项目仍在办理审批，尚未获正式批复，投产时间不确定。';
  await dispatch('next');
  assert.equal(context.state.wizard,1);assert.match(error.textContent,/发布时间/);
  context.state.draft.published='2020-01-23T10:00';await dispatch('next');
  assert.equal(context.state.wizard,2);assert.equal(context.state.draft.published,'2020-01-23T10:00');
});
