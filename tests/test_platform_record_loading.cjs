const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('design/app.js', 'utf8');
const hydrate = source.slice(source.indexOf('  async function hydrateExperiments()'), source.indexOf('\n', source.indexOf('  async function hydrateExperiments()')));
test('one unreadable experiment does not hide healthy records and retry recovers it', async () => {
  let broken = true;
  const context = vm.createContext({
    experiments: [{id:'example'}], state: {page:'experiments',selected:0},
    rememberedExperimentId:'good', experimentLoadError:'', render(){},
    current(){return this.experiments[0]},
    async apiJson(url){
      if(url === '/api/platform/experiments') return [{id:'bad'},{id:'good'}];
      if(url.includes('/bad') && broken) throw new Error('unreadable');
      if(url.endsWith('/result')) return {mode:'synthetic_market'};
      return {id:url.split('/').pop(), run_status:'completed',signal:0,sessions:18};
    }
  });
  vm.runInContext(hydrate,context);
  await context.hydrateExperiments();
  assert.equal(context.experiments.length,2);
  assert.equal(context.experiments[context.state.selected].id,'good');
  assert.match(context.experimentLoadError,/bad/);
  broken=false;
  await context.hydrateExperiments();
  assert.equal(context.experiments.length,3);
  assert.equal(context.experimentLoadError,'');
  assert.equal(context.experiments[context.state.selected].id,'good');
});
