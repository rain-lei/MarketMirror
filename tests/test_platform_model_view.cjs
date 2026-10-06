const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {ConnectionState,render}=require('../design/model-view.js');
const source=fs.readFileSync('design/app.js','utf8');
const methods=source.slice(source.indexOf('  function updateModelSettings(){'),source.indexOf('  function caseDetail(){'));
function configuration(){return {model:'DeepSeek-V4-Flash-0731-W8A8',base_url:'http://aigw.dlut.edu.cn/v1',prompt_version:'v3',credential_status:'configured',checking:false,last_check:null};}
function checked(){return {...configuration(),last_check:{status:'passed',message:'结构通过',facts_count:0,checked_at:'2026-10-06T00:00:00Z',elapsed_ms:1500}};}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function context(){
  const panel={innerHTML:''},c=vm.createContext({dialog:{open:true,dataset:{section:'model'}},document:{getElementById:()=>panel},modelSettings:new ConnectionState(),MarketModelView:{render},modal(){},apiJson:async()=>configuration()});
  vm.runInContext(methods,c);return {c,panel};
}
test('late readiness responses cannot overwrite a newer configuration or an explicit check',()=>{
  const state=new ConnectionState(),old=state.beginLoad(),next=state.beginLoad();
  assert.equal(state.accept(old,configuration()),false);assert.equal(state.accept(next,configuration()),true);
  const check=state.beginCheck();assert.equal(state.beginCheck(),null);assert.equal(state.beginLoad(),null);
  assert.equal(state.accept(next,configuration()),false);assert.equal(state.accept(check,checked()),true);
  assert.equal(state.configuration.last_check.status,'passed');
});
test('missing credentials and a server-side pending check disable new checks',()=>{
  for(const value of [{...configuration(),credential_status:'missing'},{...configuration(),checking:true}]){
    const state=new ConnectionState();state.accept(state.beginLoad(),value);
    assert.equal(state.beginCheck(),null);assert.match(render(state),/data-action="check-model" disabled/);
  }
});
test('zero facts are a valid completed check while errors and missing credentials stay explicit',()=>{
  const state=new ConnectionState();state.accept(state.beginLoad(),checked());
  assert.match(render(state),/最近检测通过/);assert.match(render(state),/提取 0 条事实/);
  assert.match(render(state),/不代表事实判断或市场预测已验证/);
  state.accept(state.beginLoad(),{...configuration(),credential_status:'missing'});
  assert.doesNotMatch(render(state),/最近检测通过/);assert.match(render(state),/尚未保存本机凭据/);
  state.fail(state.beginLoad(),'读取失败');assert.match(render(state),/保留上次读取的配置/);
  state.accept(state.beginLoad(),{...configuration(),check_result:{status:'failed',message:'网关未接受凭据'}});
  assert.match(render(state),/最近检测未通过/);assert.match(render(state),/网关未接受凭据/);
});
test('opening or refreshing settings only reads configuration and never sends draft text',async()=>{
  const {c}=context(),calls=[];c.apiJson=async(path,options)=>{calls.push({path,options});return configuration();};
  await c.loadModelConfiguration();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/platform/model');assert.equal(calls[0].options,undefined);
});
test('double checking sends one empty request and a late result cannot overwrite a different modal',async()=>{
  const {c,panel}=context(),response=deferred(),calls=[];
  c.modelSettings.accept(c.modelSettings.beginLoad(),configuration());
  c.apiJson=(path,options)=>{calls.push({path,options});return response.promise;};
  const first=c.checkModelConnection();await c.checkModelConnection();
  assert.equal(calls.length,1);assert.equal(calls[0].options.body,'{}');assert.equal(calls[0].path,'/api/platform/model/check');
  assert.match(panel.innerHTML,/正在检测/);c.dialog.dataset.section='help';panel.innerHTML='别的对话框';
  response.resolve(checked());await first;assert.equal(panel.innerHTML,'别的对话框');assert.equal(c.modelSettings.configuration.last_check.status,'passed');
});
test('closing and reopening while checking does not issue another remote request',async()=>{
  const {c}=context(),response=deferred();let calls=0;
  c.modelSettings.accept(c.modelSettings.beginLoad(),configuration());c.apiJson=()=>{calls++;return response.promise;};
  const first=c.checkModelConnection();c.dialog.open=false;await c.loadModelConfiguration();c.dialog.open=true;await c.loadModelConfiguration();
  assert.equal(calls,1);response.resolve(checked());await first;assert.equal(c.modelSettings.checking,false);
});
test('lost check responses keep known data and offer refresh without inventing success',async()=>{
  const {c,panel}=context();c.modelSettings.accept(c.modelSettings.beginLoad(),configuration());
  c.apiJson=async()=>{throw new Error('network loss');};await c.checkModelConnection();
  assert.equal(c.modelSettings.checking,false);assert.match(panel.innerHTML,/检测结果未确认/);assert.doesNotMatch(panel.innerHTML,/最近检测通过/);
  c.apiJson=async()=>checked();await c.loadModelConfiguration();assert.match(panel.innerHTML,/最近检测通过/);
});
test('malformed readiness responses never advertise a usable configuration',async()=>{
  const {c,panel}=context();c.apiJson=async()=>({...configuration(),checking:'false'});
  await c.loadModelConfiguration();assert.equal(c.modelSettings.configuration,null);
  assert.match(panel.innerHTML,/配置响应不完整/);assert.doesNotMatch(panel.innerHTML,/本机凭据可用/);
});
