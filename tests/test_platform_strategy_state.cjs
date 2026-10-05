const {test} = require('node:test');
const assert = require('node:assert/strict');
const {StrategyWorkspace,parametersFromExperiment} = require('../design/strategy-state.js');
const profile = () => ({parameters:{aggressive:{text_sensitivity:.9}},defaults:{aggressive:{text_sensitivity:.9}},limits:{aggressive:{text_sensitivity:[0,2]}},fixed_rules:{},updated_at:null});

test('editing a workspace profile does not mutate a saved profile or experiment snapshot',()=>{
  const state = new StrategyWorkspace();
  state.load(profile());
  const experiment = state.snapshot();
  state.edit('aggressive','text_sensitivity',1.5);
  assert.equal(state.dirty,true);
  assert.equal(state.snapshot().aggressive.text_sensitivity,.9);
  state.load({...profile(),parameters:structuredClone(state.draft)});
  assert.equal(state.snapshot().aggressive.text_sensitivity,1.5);
  assert.equal(experiment.aggressive.text_sensitivity,.9);
  experiment.aggressive.text_sensitivity=0;
  assert.equal(state.snapshot().aggressive.text_sensitivity,1.5);
});
test('reset stays unsaved until the returned server profile is loaded',()=>{
  const state = new StrategyWorkspace();
  state.load({...profile(),parameters:{aggressive:{text_sensitivity:1.5}}});
  state.restoreDefaults();
  assert.equal(state.draft.aggressive.text_sensitivity,.9);
  assert.equal(state.snapshot().aggressive.text_sensitivity,1.5);
  assert.equal(state.dirty,true);
});
test('invalid values and edits during save cannot alter the workspace draft',()=>{
  const state = new StrategyWorkspace();
  assert.throws(()=>state.snapshot());
  state.load(profile());
  for(const value of [NaN,Infinity,-1,3,true,'1'])assert.throws(()=>state.edit('aggressive','text_sensitivity',value));
  state.saving=true;
  assert.throws(()=>state.edit('aggressive','text_sensitivity',1.5));
  assert.throws(()=>state.restoreDefaults());
  assert.equal(state.draft.aggressive.text_sensitivity,.9);
});
test('copying an archived experiment recovers its runtime parameters independently',()=>{
  const specs=Object.fromEntries(['aggressive','conservative','institutional'].map(role=>[role+'_0',
    {kind:'strategy',parameters:{role,text_sensitivity:1.6,base_weight:.2,risk_budget:.008}}]));
  const record={backendResult:{paths:{with_message:{participant_specs:specs}}}};
  const derived=parametersFromExperiment(record);
  assert.equal(derived.aggressive.text_sensitivity,1.6);
  derived.aggressive.text_sensitivity=.9;
  assert.equal(specs.aggressive_0.parameters.text_sensitivity,1.6);
  assert.equal(parametersFromExperiment({}),null);
  record.strategy_parameters={aggressive:{text_sensitivity:1.2}};
  const snapshot=parametersFromExperiment(record);
  snapshot.aggressive.text_sensitivity=0;
  assert.equal(record.strategy_parameters.aggressive.text_sensitivity,1.2);
});
