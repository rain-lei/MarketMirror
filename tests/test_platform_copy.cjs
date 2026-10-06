const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {parametersFromExperiment} = require('../design/strategy-state.js');
const source=fs.readFileSync('design/app.js','utf8');
const copy=source.split('\n').find(line=>line.startsWith('  function useSample('));
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
