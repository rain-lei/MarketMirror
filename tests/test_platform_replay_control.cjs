const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const replay=require('../design/decision-view.js').ReplayControl;
const rows=Array.from({length:12},(_,i)=>({date:`2020-07-${String(i+20).padStart(2,'0')}`,visible:i>=5,active:i>=5&&i<8}));

test('source timing is read from the archive and changes correctly at entry and expiry',()=>{
  assert.equal(replay.viewModel(rows,1).status,'消息尚未进入');
  const active=replay.viewModel(rows,6);
  assert.equal(active.entry,6);assert.equal(active.date,'2020-07-25');assert.equal(active.status,'消息作用中');
  assert.deepEqual(active.intervals,[{start:6,end:8}]);
  assert.equal(replay.viewModel(rows,9).status,'消息作用已结束');
  const later=structuredClone(rows);later[5].active=false;
  assert.equal(replay.viewModel(later,6).status,'消息已可见 · 待作用');
  assert.equal(replay.viewModel(rows.map(r=>({...r,active:false})),9).status,'消息已可见 · 无作用输入');
});

test('out of bounds steps, one-day windows and separated information intervals remain finite',()=>{
  assert.equal(replay.viewModel(rows,-30).step,1);assert.equal(replay.viewModel(rows,999).step,12);
  assert.equal(replay.viewModel(rows,NaN).step,1);
  const one=replay.viewModel([{visible:true,active:true}],1);
  assert.equal(one.progress,0);assert.equal(one.previousDisabled,true);assert.equal(one.nextDisabled,true);
  const split=rows.map((r,i)=>({...r,active:[1,2,6].includes(i)}));
  assert.deepEqual(replay.viewModel(split,3).intervals,[{start:2,end:3},{start:7,end:7}]);
  const empty=replay.viewModel([],3);assert.equal(empty.status,'信息时点未存档');assert.equal(empty.entry,null);
});

test('native slider semantics, date ticks and the real source window are exposed with safe text',()=>{
  const html=replay.render({id:'case-step',rows,step:6,title:'<img src=x>',label:'选择决策步'});
  assert.match(html,/type="range" min="1" max="12" step="1" value="6"/);
  assert.match(html,/aria-valuetext="第 6 步 · 2020-07-25 · 消息作用中"/);
  assert.match(html,/消息进入 · 第 6 步/);assert.match(html,/第 6 — 8 步/);assert.match(html,/&lt;img/);
  assert.match(html,/data-replay-progress>当前进度 6 \/ 12 步/);
  assert.match(html,/data-replay-completion>已定位 50%/);
  assert.match(html,/data-replay-current>第 6 步/);
  assert.doesNotMatch(html,/<img|NaN|undefined/);
  const untrusted=replay.render({id:'test-step',rows:[{date:'</script><img src=x onerror=bad()>',visible:true,active:true}]});
  assert.equal((untrusted.match(/<\/script>/g)||[]).length,1);assert.doesNotMatch(untrusted,/<img/);
  assert.throws(()=>replay.render({id:'x" onclick="bad()',rows}),/Invalid/);
  assert.match(replay.render({id:'short-step',rows:rows.slice(0,3)}),/本窗口消息未进入/);
});

function fakeControl(){
  const handlers={input:[],click:[]},elements=new Map(),calls=[];
  const root={addEventListener(type,handler){handlers[type].push(handler);}};
  const control={dataset:{unit:'日'},style:{setProperty(key,value){calls.push({key,value});}},querySelector(selector){return elements.get(selector);}};
  const input={value:'1',max:'12',setAttribute(key,value){this[key]=value;},matches:()=>true,closest:()=>control,
    dispatchEvent(event){assert.equal(event.type,'input');assert.equal(event.bubbles,true);handlers.input.forEach(handler=>handler({target:this}));}};
  elements.set('[data-replay-input]',input);elements.set('[data-replay-rows]',{textContent:JSON.stringify(rows)});
  for(const selector of ['[data-replay-position]','[data-replay-date]','[data-replay-status]','[data-replay-current]','[data-replay-progress]','[data-replay-completion]','[data-replay-progress-note]','[data-replay-delta="-1"]','[data-replay-delta="1"]'])elements.set(selector,{});
  return {root,control,input,elements,handlers,calls,click(dataset,disabled=false){const button={dataset,disabled,closest:()=>control};handlers.click.forEach(handler=>handler({target:{closest:()=>button}}));}};
}

test('buttons dispatch the same input update as dragging, and duplicate binding cannot double-step',()=>{
  const f=fakeControl();replay.bind(f.root);replay.bind(f.root);
  let decisionStep=1;f.root.addEventListener('input',event=>{decisionStep=Number(event.target.value);});
  assert.equal(f.handlers.click.length,1);
  f.click({replayDelta:'1'});assert.equal(decisionStep,2);
  f.click({replayTo:'6'});assert.equal(decisionStep,6);
  assert.equal(f.elements.get('[data-replay-position]').textContent,'第 6 / 12 日');
  assert.equal(f.elements.get('[data-replay-date]').textContent,'2020-07-25');
  assert.equal(f.elements.get('[data-replay-status]').textContent,'消息作用中');
  assert.equal(f.elements.get('[data-replay-current]').textContent,'第 6 日');
  assert.equal(f.elements.get('[data-replay-progress]').textContent,'当前进度 6 / 12 日');
  assert.equal(f.elements.get('[data-replay-completion]').textContent,'已定位 50%');
  assert.match(f.elements.get('[data-replay-progress-note]').textContent,/消息进入第 6 日/);
  assert.match(f.input['aria-valuetext'],/第 6 日/);
  assert.ok(f.calls.some(c=>c.key==='--replay-progress'&&c.value.startsWith('45.45')));
  f.click({replayTo:'999'});assert.equal(decisionStep,12);
  assert.equal(f.elements.get('[data-replay-delta="1"]').disabled,true);
  f.click({replayTo:'1'});assert.equal(decisionStep,1);
  assert.equal(f.elements.get('[data-replay-delta="-1"]').disabled,true);
  f.click({replayDelta:'1'},true);assert.equal(decisionStep,1);
});

test('a native keyboard or drag input updates the timeline date and status without a click',()=>{
  const f=fakeControl();replay.bind(f.root);f.input.value='9';f.input.dispatchEvent(new Event('input',{bubbles:true}));
  assert.equal(f.elements.get('[data-replay-status]').textContent,'消息作用已结束');
  assert.equal(f.elements.get('[data-replay-date]').textContent,'2020-07-28');
  assert.equal(f.elements.get('[data-replay-position]').textContent,'第 9 / 12 日');
});

test('strategy page distinguishes shared LLM facts from three untrained rule profiles',()=>{
  const source=fs.readFileSync('design/app.js','utf8'),method=source.slice(source.indexOf('  function strategyArchitecturePanel(){'),source.indexOf('  async function hydrateStrategies(){'));
  const context=vm.createContext({roles:[{name:'激进型',color:'#c98242'},{name:'保守型',color:'#597cc3'},{name:'机构型',color:'#8876b5'}],icon:()=>'',heading:()=>'',
    strategyWorkspace:{parameters:null},strategyUnavailable:()=>'<p>参数未加载</p>'});
  vm.runInContext(method,context);const html=context.agentsPage();
  assert.match(html,/DeepSeek-V4-Flash-0731-W8A8/);assert.match(html,/没有分别训练三个投资者模型/);
  assert.match(html,/角色差异来自预设规则/);assert.match(html,/文本方向和不确定性由你设定/);
  assert.match(html,/归档研究案例使用已登记的事实映射/);
  assert.match(html,/激进型/);assert.match(html,/保守型/);assert.match(html,/机构型/);
  assert.match(html,/参数未加载/);
});
