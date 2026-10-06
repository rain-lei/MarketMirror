const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {BatchState,parseSeeds,renderDetail} = require('../design/batch-view.js');

function record(id='batch') {
  const keys=['aggressive','conservative','institutional'];
  const scenarios=['neutral','positive','negative','uncertain'];
  return {id,title:'种子对照',created_at:'2026-10-06T00:00:00Z',status:'partial',
    seeds:[7],sessions:18,duration:6,cash:1000000,strategy_parameters_sha256:'abc123',
    strategy_parameters:Object.fromEntries(keys.map(key=>[key,{text_sensitivity:.5,base_weight:.3,risk_budget:.01}])),
    progress:{total:4,completed:3,failed:1,pending:0,running:0,interrupted:0},
    summary:scenarios.flatMap(scenario=>keys.map(role=>({scenario,role,planned:1,completed:scenario==='positive'?0:1,
      mean_pp:scenario==='positive'?null:0,min_pp:scenario==='positive'?null:0,max_pp:scenario==='positive'?null:0}))),
    records:scenarios.map(scenario=>({scenario,seed:7,attempts:1,status:scenario==='positive'?'failed':'completed',
      experiment_id:scenario,error:scenario==='positive'?{type:'RuntimeError',message:'运行失败'}:null,
      requested:100,accepted:100,filled:0,role_return_difference_pp:Object.fromEntries(keys.map(k=>[k,0]))}))};
}

test('batch selection ignores late response from another batch or an older refresh', () => {
  const state=new BatchState();
  const first=state.begin('first'), second=state.begin('second');
  assert.equal(state.accept(first,record('first')),false);
  assert.equal(state.fail(first,'old error'),false);
  assert.equal(state.selectedId,'second');
  assert.equal(state.detail,null);
  assert.equal(state.accept(second,record('second')),true);
  const older=state.begin('second'), newer=state.begin('second');
  assert.equal(state.accept(older,{...record('second'),status:'completed'}),false);
  assert.equal(state.accept(newer,record('second')),true);
  assert.equal(state.detail.status,'partial');
});

test('failed refresh retains known data with an explicit error; wrong batch id is rejected', () => {
  const state=new BatchState();
  state.accept(state.begin('batch'),record());
  const ticket=state.begin('batch');
  state.fail(ticket,'读取失败');
  assert.equal(state.detail.progress.completed,3);
  assert.equal(state.loading,false);
  assert.match(renderDetail(state.detail,state),/保留上次读取的数据/);
  assert.throws(()=>state.accept(state.begin('batch'),record('other')),/编号不匹配/);
});

test('incomplete results are blank while genuine zero effects remain numeric', () => {
  const value=record();
  const html=renderDetail(value);
  const failedRow=html.match(/<tr><td><strong>正向<\/strong>[\s\S]*?<\/tr>/)[0];
  assert.equal((failedRow.match(/<td class="number">—<\/td>/g)||[]).length,4);
  assert.doesNotMatch(failedRow,/查看决策|0\.000000/);
  const neutralRow=html.match(/<tr><td><strong>中性<\/strong>[\s\S]*?<\/tr>/)[0];
  assert.match(neutralRow,/\+0\.000000/);
  assert.match(neutralRow,/data-batch-id="batch"/);
  assert.match(html,/重试未完成项/);
  assert.equal((html.match(/class="batch-range-chart"/g)||[]).length,3);
  assert.doesNotMatch(html,/NaN|undefined/);
});

test('seed entry accepts separators and rejects ambiguous or duplicated seeds', () => {
  assert.deepEqual(parseSeeds('0，7, 19\n999999'),[0,7,19,999999]);
  for(const text of ['', '7,7', '01,1', '-1', '1e2', '0x10', '2.5', '1000000', '0,1,2,3,4,5,6,7,8,9,10'])
    assert.throws(()=>parseSeeds(text));
});

const source=fs.readFileSync('design/app.js','utf8');
const openFunction=source.slice(source.indexOf('  async function openBatchExperiment('),source.indexOf('  const activeRuns'));

test('opening a batch uses its immutable archived result, not the latest single run', async () => {
  const calls=[];
  const context=vm.createContext({state:{page:'batches',selected:0},batchWorkspace:{selectedId:'batch'},batchOpenVersion:0,
    experiments:[{id:'experiment',backendResult:{provenance:{result_id:'latest'}}}],batchArchivedExperiment:null,
    toast(){},navigate(page){context.state.page=page;},
    async apiJson(path){calls.push(path);return path.includes('/results/')?{generated_at:'old-time',provenance:{result_id:'archived',run_attempts:1}}:{id:'experiment',result_id:'latest',last_run_at:'new-time',run_status:'failed',run_error:{message:'new run error'},run_attempts:4};}});
  vm.runInContext(openFunction,context);
  await context.openBatchExperiment('batch','experiment');
  assert.equal(context.batchArchivedExperiment.backendResult.provenance.result_id,'archived');
  assert.equal(context.batchArchivedExperiment.result_id,'archived');
  assert.equal(context.batchArchivedExperiment.last_run_at,'old-time');
  assert.equal(context.batchArchivedExperiment.run_attempts,1);
  assert.equal(context.batchArchivedExperiment.run_error,null);
  assert.equal(context.state.page,'analysis');
  assert.deepEqual(calls,['/api/platform/experiments/experiment','/api/platform/batches/batch/results/experiment']);
});

test('a late archive response cannot open an older selection or navigate after leaving', async () => {
  const pending=new Map();
  const context=vm.createContext({state:{page:'batches',selected:0},batchWorkspace:{selectedId:'batch'},batchOpenVersion:0,
    experiments:[],batchArchivedExperiment:null,toast(){},navigate(page){context.state.page=page;},
    apiJson(path){return new Promise(resolve=>pending.set(path,resolve));}});
  vm.runInContext(openFunction,context);
  const first=context.openBatchExperiment('batch','first'), second=context.openBatchExperiment('batch','second');
  function resolve(id){pending.get('/api/platform/experiments/'+id)({id});pending.get('/api/platform/batches/batch/results/'+id)({provenance:{result_id:id}});}
  resolve('first');await first;
  assert.equal(context.batchArchivedExperiment,null);
  resolve('second');await second;
  assert.equal(context.batchArchivedExperiment.id,'second');
  context.state.page='batches';
  const later=context.openBatchExperiment('batch','later');context.state.page='agents';resolve('later');await later;
  assert.equal(context.state.page,'agents');
  assert.equal(context.batchArchivedExperiment.id,'second');
});
