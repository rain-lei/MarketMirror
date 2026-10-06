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
  assert.equal(context.experiments.find(e=>e.id==='good').kind,'neutral');
  broken=false;
  await context.hydrateExperiments();
  assert.equal(context.experiments.length,3);
  assert.equal(context.experimentLoadError,'');
  assert.equal(context.experiments[context.state.selected].id,'good');
});

test('corrupt rows have no buttons or fabricated parameters; healthy rows remain openable', () => {
  const line = source.split('\n').find(line => line.startsWith('  function experimentRows()'));
  const context = vm.createContext({experiments:[{id:'broken',title:'损坏',type:'未知',corrupt:true,custom:true,time:'记录损坏'}],state:{search:''},esc:String,icon:()=>''});
  vm.runInContext(line,context);
  const broken=context.experimentRows();
  assert.equal((broken.match(/<button/g)||[]).length,0);
  assert.doesNotMatch(broken,/undefined|18 步|信号/);
  context.experiments=[{id:'healthy',title:'正常',type:'政策消息',sessions:18,seed:7,time:'今天'}];
  const healthy=context.experimentRows();
  assert.equal((healthy.match(/<button\b/g)||[]).length,2);
  assert.equal((healthy.match(/<\/button>/g)||[]).length,2);
  assert.doesNotMatch(healthy,/<button[^>]*<button/);
});

test('remembered corrupt experiment renders recovery guidance without running normal analysis', () => {
  const body=source.slice(source.indexOf('  function analysis() {'),source.indexOf('    const e=current(),v=values()'))+'\n}';
  const context=vm.createContext({current:()=>({corrupt:true,id:'broken',custom:true}),esc:String});
  vm.runInContext(body,context);
  const html=context.analysis();
  assert.match(html,/实验记录需要恢复/);
  assert.match(html,/broken/);
  assert.doesNotMatch(html,/retry-run|data-action="export"|undefined/);
});
