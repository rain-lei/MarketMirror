const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {StrategyWorkspace,StrategyParameterControls:controls}=require('../design/strategy-state.js');
const fields=[{key:'text_sensitivity',label:'文本敏感度',scale:1,step:.05,hint:'信息响应系数'},
  {key:'base_weight',label:'基础股票权重',scale:100,step:1,hint:'组合股票权重'},
  {key:'risk_budget',label:'风险预算',scale:100,step:.1,hint:'模型波动约束'}];
const bounds={text_sensitivity:[0,2],base_weight:[0,.55],risk_budget:[.001,.08]};

function parameter(scope='workspace',key='text_sensitivity'){
  const attributes={'aria-invalid':'false'},nodes=new Map(),style={},classes=new Set();
  const control={classList:{toggle(name,on){on?classes.add(name):classes.delete(name);}},style:{setProperty(k,v){style[k]=v;}},querySelector(selector){return nodes.get(selector);}};
  const input={value:'0.9',dataset:{strategyScope:scope,strategyRole:'aggressive',strategyKey:key},setAttribute(k,v){attributes[k]=v;},getAttribute:k=>attributes[k],closest:()=>control,focus(){this.focused=true;}};
  const range={...input,dataset:{...input.dataset,parameterStep:String(fields.find(f=>f.key===key).step)},value:'0.9',min:'0',max:key==='risk_budget'?'8':'2',matches:()=>true};
  nodes.set('[data-parameter-number]',input);nodes.set('[data-parameter-range]',range);
  nodes.set('[data-parameter-label]',{});nodes.set('[data-parameter-error]',{});
  return {input,range,control,nodes,style,classes,attributes};
}

test('percentage display converts to internal weights without quantizing precise inputs',()=>{
  assert.equal(controls.parseDisplay('0.83',fields[2],bounds.risk_budget),.0083);
  assert.equal(controls.parseDisplay('33.3333',fields[1],bounds.base_weight),.333333);
  assert.equal(controls.parseDisplay('0',fields[0],bounds.text_sensitivity),0);
  assert.equal(controls.display(.29,fields[1]),'29');
  assert.equal(controls.format(.333333,fields[1]),'33.3333%');
  assert.equal(controls.format(1e-12,fields[0]),'1.00e-12');
  for(const missing of [null,undefined,NaN,Infinity,'0'])assert.equal(controls.format(missing,fields[0]),'未存档');
  assert.equal(controls.parseDisplay('0.1',fields[2],bounds.risk_budget),.001);
  assert.equal(controls.parseDisplay('8',fields[2],bounds.risk_budget),.08);
  for(const raw of ['', ' ', 'NaN', 'Infinity', '-0.1','8.01'])assert.throws(()=>controls.parseDisplay(raw,fields[2],bounds.risk_budget));
  assert.equal(controls.fill(.001,bounds.risk_budget),0);assert.equal(controls.fill(.08,bounds.risk_budget),100);
});

test('rendered controls expose exact numeric values, actual bounds and fractional slider positions',()=>{
  const html=controls.render({field:fields[2],role:'aggressive',scope:'experiment',value:.0083,bounds:bounds.risk_budget});
  assert.match(html,/type="number"/);assert.match(html,/精确输入风险预算/);
  assert.match(html,/min="0.1" max="8" step="any" value="0.83"/);
  assert.match(html,/data-parameter-step="0.1"/);assert.match(html,/0.83%/);assert.match(html,/0.10%/);assert.match(html,/8.00%/);
  assert.match(html,/data-strategy-scope="experiment"/);assert.doesNotMatch(html,/NaN|undefined/);
  const escaped=controls.render({field:{...fields[0],label:'<script>x</script>',hint:'<img src=x>'},role:'conservative',scope:'workspace',value:.9,bounds:[0,2],disabled:true});
  assert.doesNotMatch(escaped,/<script>|<img/);assert.match(escaped,/&lt;script&gt;/);assert.match(escaped,/disabled/);
});

function context(scope='workspace',key='text_sensitivity'){
  const p=parameter(scope,key),saved={aggressive:{text_sensitivity:.9,base_weight:.2,risk_budget:.008}},workspace=new StrategyWorkspace();
  workspace.load({parameters:saved,defaults:saved,limits:{aggressive:bounds},fixed_rules:{},updated_at:null});
  let persisted=0;const elements={save:{disabled:false},next:{disabled:false},status:{textContent:''}},messages=[];
  const document={querySelector(selector){if(selector==='[data-action="save-roles"]')return elements.save;if(selector==='[data-action="next"]')return elements.next;
    return selector.includes(`data-strategy-scope="${scope}"`)&&p.attributes['aria-invalid']==='true'?p.input:null;},getElementById:()=>elements.status};
  const c=vm.createContext({strategyFields:fields,strategyWorkspace:workspace,StrategyParameterControls:controls,document,
    state:{draft:{strategy_parameters:structuredClone(saved)}},persistDraft(){persisted++;},toast(message){messages.push(message);}});
  const source=fs.readFileSync('design/app.js','utf8');
  vm.runInContext(source.slice(source.indexOf('  function strategyInputValid('),source.indexOf('  function draftStrategyPanel(){')),c);
  return {...p,c,workspace,elements,messages,get persisted(){return persisted;}};
}

test('empty and out of range workspace input cannot become zero or overwrite the last valid draft',()=>{
  const f=context();f.input.value='';f.c.handleStrategyInput(f.input);
  assert.equal(f.workspace.draft.aggressive.text_sensitivity,.9);assert.equal(f.elements.save.disabled,true);
  assert.equal(f.classes.has('invalid'),true);assert.equal(f.c.strategyInputValid('workspace'),false);assert.equal(f.input.focused,true);
  f.input.value='3';f.c.handleStrategyInput(f.input);assert.equal(f.workspace.draft.aggressive.text_sensitivity,.9);
  f.input.value='0';f.c.handleStrategyInput(f.input);assert.equal(f.workspace.draft.aggressive.text_sensitivity,0);
  assert.equal(f.elements.save.disabled,false);assert.equal(f.attributes['aria-invalid'],'false');assert.equal(f.style['--parameter-fill'],'0%');
  assert.equal(f.workspace.parameters.aggressive.text_sensitivity,.9);assert.equal(f.workspace.dirty,true);
});

test('experiment numeric edits persist only valid internal values, and the range can clear an invalid entry',()=>{
  const f=context('experiment','risk_budget');f.input.value='0.83';f.c.handleStrategyInput(f.input);
  assert.equal(f.c.state.draft.strategy_parameters.aggressive.risk_budget,.0083);assert.equal(f.range.value,'0.83');
  assert.equal(f.persisted,1);assert.equal(f.workspace.snapshot().aggressive.risk_budget,.008);
  f.input.value='';f.c.handleStrategyInput(f.input);assert.equal(f.elements.next.disabled,true);assert.equal(f.persisted,1);
  assert.equal(f.c.state.draft.strategy_parameters.aggressive.risk_budget,.0083);
  f.range.value='0.93';f.c.handleStrategyInput(f.range);
  assert.equal(f.input.value,'0.93');assert.ok(Math.abs(f.c.state.draft.strategy_parameters.aggressive.risk_budget-.0093)<1e-15);
  assert.equal(f.elements.next.disabled,false);assert.equal(f.persisted,2);assert.equal(f.c.strategyInputValid('experiment'),true);
});

test('keyboard nudges retain a precise starting point and stop at bounds without duplicate binding',()=>{
  const f=parameter('experiment','risk_budget'),handlers=[];f.range.value='0.83';f.range.min='0.1';let updates=0;
  f.range.dispatchEvent=event=>{assert.equal(event.type,'input');assert.equal(event.bubbles,true);updates++;};
  const root={addEventListener(type,handler){assert.equal(type,'keydown');handlers.push(handler);}};
  controls.bind(root);controls.bind(root);assert.equal(handlers.length,1);
  let prevented=0;const send=(key,shiftKey=false)=>handlers[0]({target:f.range,key,shiftKey,preventDefault(){prevented++;}});
  send('ArrowRight');assert.equal(f.range.value,'0.93');send('ArrowLeft',true);assert.equal(f.range.value,'0.1');
  f.range.value='7.95';send('ArrowRight');assert.equal(f.range.value,'8');
  send('Home');assert.equal(updates,3);assert.equal(prevented,3);
});

test('workspace saving rejects in-flight edits and the save operation independently guards invalid fields',async()=>{
  const f=context();f.workspace.saving=true;f.input.value='1.5';f.c.handleStrategyInput(f.input);
  assert.equal(f.workspace.draft.aggressive.text_sensitivity,.9);assert.equal(f.elements.save.disabled,true);
  f.workspace.saving=false;f.input.value='';f.c.handleStrategyInput(f.input);
  const source=fs.readFileSync('design/app.js','utf8'),method=source.slice(source.indexOf('  async function saveStrategies(){'),source.indexOf('  function experimentStrategyPanel('));
  let calls=0;f.c.apiJson=async()=>{calls++;};f.c.render=()=>{};
  vm.runInContext(method,f.c);await f.c.saveStrategies();assert.equal(calls,0);
});
