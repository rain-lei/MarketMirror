const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {CaseState,renderLibrary,renderDetail,renderFacts,renderSource,renderClock} = require('../design/case-view.js');

function bundle(id='case',seed=7,mode='reviewed_llm') {
  const roles=['aggressive','conservative','institutional'];
  const fact={kind:'operational_disruption',status:'scheduled',claim:'运输将关闭。',
    evidence:[{source:'document',quote:'运输关闭',start:1,end:5}]};
  const path=noText=>({trace:Array.from({length:18},(_,i)=>({
    trade_date:`2000-01-${String(i+3).padStart(2,'0')}`,signal_cutoff_date:`2000-01-${String(i+1).padStart(2,'0')}`,
    execution_reference_date:`2000-01-${String(i+2).padStart(2,'0')}`,
    observations:Object.fromEntries(['A','B','C'].map(asset=>{
      const active=!noText&&mode!=='no_text'&&i>=3&&i<9&&asset===(mode==='llm_asset_placebo'?'B':'A');
      return [asset,{text_signal:active?-.4:0,text_uncertainty:active?.6:0}];
    }))})),summary:{role_wealth_multiple:Object.fromEntries(roles.map(r=>[r,1])),
      strategy_requested:100,strategy_accepted:100,strategy_filled:0}});
  const links=Array.from({length:18},(_,i)=>({source_visible:i>=3,information_active:i>=3&&i<9,
    exposed_asset:mode==='llm_asset_placebo'?'B':'A',mapping:{signal:-.4,uncertainty:.6,
      used_fact_indices:mode==='keywords'?[]:[0],unresolved_fact_indices:[],keyword_matches:mode==='keywords'?['关闭']:[]}}));
  return {case:{case_id:id,label:'归档原文案例',title:'原文标题',available_at:'2000-01-04T03:00:00+08:00',
      visibility_precision:'exact_page_timestamp',source_url:'https://example.com/source',segments:[{source:'document',text:'😀运输关闭。'}]},
    seed,mode,review:{raw_facts:[{...fact,status:'in_force',claim:'运输已关闭。'}],reviewed_record:{facts:[fact]},
      checks:[{fact_index:0,changes:{status:{raw:'in_force',reviewed:'scheduled'},claim:{raw:'运输已关闭。',reviewed:'运输将关闭。'}},
        notes:'发布时间早于实施时刻。'}],independent_gold:false,review_basis:'single_ai_post_model_source_review'},
    mapping_rules:{operational_disruption:{signal:-.4,uncertainty:.6}},
    comparison:{role_changes:Object.fromEntries(roles.map(r=>[r,{decisions:72,targets_changed:1,requests_changed:1,actual_fills_changed:0}]))},
    source_links:{baseline:links,with_message:links},result:{audit:{paths_checked:mode==='no_text'?1:2,days_checked:mode==='no_text'?18:36},
      paths:{baseline:path(true),with_message:path(false)}}};
}

test('case, seed, mode and refresh races never replace the current archive',()=>{
  const state=new CaseState(),old=state.begin('old'),next=state.begin('new',11,'keywords');
  assert.equal(state.accept(old,bundle('old')),false);
  assert.equal(state.fail(old,'old error'),false);
  assert.equal(state.detail,null);
  assert.equal(state.accept(next,bundle('new',11,'keywords')),true);
  const seed=state.begin('new',23),mode=state.begin('new',23,'llm_asset_placebo');
  assert.equal(state.accept(seed,bundle('new',23,'keywords')),false);
  assert.equal(state.accept(mode,bundle('new',23,'llm_asset_placebo')),true);
  const first=state.begin('new'),last=state.begin('new');
  assert.equal(state.accept(first,bundle('new',23,'llm_asset_placebo')),false);
  assert.equal(state.accept(last,bundle('new',23,'llm_asset_placebo')),true);
});

test('initial step follows the archive clock even when seed changes before the first response',()=>{
  const state=new CaseState();state.step=15;
  state.begin('case');
  state.accept(state.begin('case',11),bundle('case',11));
  assert.equal(state.step,4);
  state.step=8;
  state.accept(state.begin('case',23),bundle('case',23));
  assert.equal(state.step,8);
});

test('a failed refresh keeps the known archive and reports it; identity mismatches fail',()=>{
  const state=new CaseState();state.accept(state.begin('case'),bundle());
  const ticket=state.begin('case');
  assert.match(renderDetail(state.detail,state),/正在重新核验所选路径/);
  state.fail(ticket,'文件核验失败');
  assert.equal(state.loading,false);
  assert.equal(state.detail.case.case_id,'case');
  assert.match(renderDetail(state.detail,state),/文件核验失败。保留上次读取的归档/);
  assert.throws(()=>state.accept(state.begin('case'),bundle('other')),/响应与当前选择不一致/);
});

test('the clock uses the selected archived asset and path, including zero and expired inputs',()=>{
  const state=new CaseState(),data=bundle('case',7,'llm_asset_placebo');
  state.step=4;state.asset='A';
  assert.match(renderClock(data,state),/假设暴露于资产 B/);
  assert.match(renderClock(data,state),/当前资产 A 实际收到信号 0.00 \/ 不确定性 0.00/);
  state.asset='B';
  assert.match(renderClock(data,state),/信号 -0.40 \/ 不确定性 0.60/);
  assert.match(renderClock(data,state),/本步采用的复核事实/);
  assert.match(renderClock(data,state),/data-case-quote="0"/);
  state.group='baseline';
  assert.match(renderClock(data,state),/无文本条件，不接收原文信号/);
  assert.match(renderClock(data,state),/信号 0.00 \/ 不确定性 0.00/);
  state.group='with_message';state.step=10;
  assert.match(renderClock(data,state),/作用期未开始或已结束/);
  assert.match(renderClock(data,state),/先前交易与价格变化仍可能影响后续账户/);
  state.step=1;
  assert.match(renderClock(data,state),/尚未可见/);
  state.step=4;state.asset='A';
  const keyword=bundle('case',7,'keywords');
  assert.match(renderClock(keyword,state),/登记关键词命中：关闭/);
  assert.match(renderClock(keyword,state),/未采用 LLM 事实映射/);
});

test('raw and corrected facts preserve their status, quotes and correction reasons',()=>{
  const state=new CaseState(),data=bundle();
  const corrected=renderFacts(data,state);
  assert.match(corrected,/已宣布待实施/);
  assert.match(corrected,/运输将关闭。/);
  assert.match(corrected,/发布时间早于实施时刻/);
  assert.match(corrected,/未取得独立人工金标准/);
  assert.match(corrected,/信号 -0.40 \/ 不确定性 0.60/);
  state.factView='raw';
  const raw=renderFacts(data,state);
  assert.match(raw,/已生效/);
  assert.match(raw,/运输已关闭。/);
  assert.match(raw,/data-case-quote-version="raw"/);
  assert.match(raw,/修订前的判断可能有误/);
});

test('quote locations use Unicode code points and invalid spans never highlight unrelated text',()=>{
  const data=bundle(),e=data.review.reviewed_record.facts[0].evidence[0];
  assert.match(renderSource(data.case,e),/😀<mark id="case-evidence-mark">运输关闭<\/mark>。/);
  assert.doesNotMatch(renderSource(data.case,{...e,start:0}),/<mark/);
  const unsafe={...data.case,title:'<script>x</script>',source_url:'javascript:alert(1)',
    segments:[{source:'document',text:'<script>x</script>'}]};
  const html=renderSource(unsafe,null);
  assert.doesNotMatch(html,/<script>|href="javascript:/);
  assert.match(html,/&lt;script&gt;/);
});

test('unavailable catalogs expose no substitute cases and exports identify the selected condition',()=>{
  const state=new CaseState();
  const missing=renderLibrary({available:false,cases:[],message:'暂无归档'},state);
  assert.match(missing,/暂无归档/);
  assert.doesNotMatch(missing,/data-case-select/);
  state.accept(state.begin('case',89,'keywords'),bundle('case',89,'keywords'));
  const html=renderDetail(state.detail,state);
  assert.match(html,/\/export\?seed=89&mode=keywords/);
  assert.match(html,/\+0.000000%/);
  assert.match(html,/真实的是来源文本及归档日历/);
  assert.match(html,/data-replay="case-step"/);assert.match(html,/消息进入 · 第 4 步/);
  assert.match(html,/消息作用区间 · 第 4 — 9 步/);
  assert.match(html,/2000-01-06/);
  assert.doesNotMatch(html,/NaN|undefined/);
  const paired=renderDetail(state.detail,state,{comparison:'<section>已核对的步骤对照</section>'});
  assert.match(paired,/id="case-comparison"><section>已核对的步骤对照/);
  const selfReference=bundle('case',7,'no_text');
  const selfHTML=renderDetail(selfReference,state);
  assert.match(selfHTML,/18 个市场日账本核验通过/);
  assert.match(selfHTML,/无文本条件使用同一路径自对照/);
});

const source=fs.readFileSync('design/app.js','utf8');
const copy=source.slice(source.indexOf('  function newFromCase(){'),source.indexOf('  function batchesPage(){'));
test('reusing case text starts a new experiment without attaching the old model or market results',()=>{
  const original=bundle(),parameters={aggressive:{text_sensitivity:1.2}};
  const resets=[];
  const context=vm.createContext({caseWorkspace:{detail:original},state:{draft:{text_analysis:'old'},wizard:3},
    strategyWorkspace:{parameters,snapshot:()=>JSON.parse(JSON.stringify(parameters))},
    batchArchivedExperiment:{id:'old'},analysisBinding:{reset(text){resets.push(text);}},navigate(){},toast(){}});
  vm.runInContext(copy,context);context.newFromCase();
  assert.equal(context.state.draft.source,'原文：😀运输关闭。');
  assert.equal(context.state.draft.published,'2000-01-04T03:00');
  assert.equal(context.state.draft.signal,0);
  assert.equal(context.state.draft.sessions,18);
  assert.equal(context.state.draft.seed,7);
  assert.equal(context.state.draft.text_analysis,undefined);
  assert.equal(context.batchArchivedExperiment,null);
  assert.deepEqual(resets,[context.state.draft.source]);
  context.state.draft.strategy_parameters.aggressive.text_sensitivity=1.8;
  assert.equal(parameters.aggressive.text_sensitivity,1.2);
  assert.equal(original.review.reviewed_record.facts[0].status,'scheduled');
});

const loadFunctions=source.slice(source.indexOf('  async function loadSourceCase('),source.indexOf('  function newFromCase(){'));
test('missing library cancels an in-flight selection and ends its loading state',async()=>{
  let resolveDetail;
  const state=new CaseState();state.catalog={available:true};
  const context=vm.createContext({caseWorkspace:state,caseCatalogVersion:0,caseCatalogLoading:false,caseCatalogError:'',
    updateCasePanels(){},apiJson(path){return path.endsWith('/source-cases')?Promise.resolve({available:false,cases:[]}):
      new Promise(resolve=>{resolveDetail=resolve;});}});
  vm.runInContext(loadFunctions,context);
  const pending=context.loadSourceCase('case');
  await context.hydrateSourceCases();
  resolveDetail(bundle());await pending;
  assert.equal(state.detail,null);
  assert.equal(state.selectedId,null);
  assert.equal(state.loading,false);
  assert.equal(state.catalog.available,false);
});
