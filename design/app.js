/* Local simulation UI. Scientific protocols remain separate and frozen. */
(() => {
  'use strict';
  const paths = {
    grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    chart:'M4 3v17h17M7 14l4-4 4 2 5-7',
    document:'M14 2H5v20h14V7zM14 2v5h5M8 11h8M8 15h8M8 18h5',
    agents:'M8 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M2 21v-3a6 6 0 0 1 12 0v3M16 4a4 4 0 0 1 0 7M17 14a5 5 0 0 1 5 5v2',
    lock:'M6 10h12v11H6zM8 10V6a4 4 0 0 1 8 0v4M12 14v3',
    layers:'m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5',
    help:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M9 8a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z',
    moon:'M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11',
    menu:'M4 6h16M4 12h16M4 18h16',
    plus:'M12 5v14M5 12h14',
    download:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
    arrow:'M4 12h16m-6-6 6 6-6 6',
    chevron:'m9 5 7 7-7 7',
    close:'m6 6 12 12M6 18 18 6',
    bolt:'m13 2-9 12h7l-1 8 10-12h-7z',
    shield:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6zM8 12l3 3 5-6',
    building:'M4 22V3h13v19M17 9h4v13M8 7h1M12 7h1M8 11h1M12 11h1M8 15h1M12 15h1M9 22v-4h4v4',
    search:'M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14m5-2 6 6',
    check:'m5 12 4 4L19 6',
    clock:'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20M12 6v6l4 2',
    sliders:'M4 3v7m0 5v6M12 3v12m0 5v1M20 3v2m0 5v11M1 10h6v5H1zM9 15h6v5H9zM17 5h6v5h-6z',
    replay:'M3 10a9 9 0 1 1 2 9M3 3v7h7',
    spark:'m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3z',
    folder:'M2 6h8l2 3h10v12H2zM2 6V3h7l3 3h8v3',
  };
  const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.document}"/></svg>`;
  const esc = (value) => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = (root = document) => root.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
  const experiments = [
    {id:'EXP-026',title:'流动性宽松政策 · 三类策略对照',type:'政策消息',kind:'positive',source:'为支持实体经济发展，拟适度降低融资成本，增加中长期流动性供给。政策实施节奏及覆盖范围仍待进一步明确。',note:'研究宽松信息进入后，不同风险偏好下的仓位调整与成交差异。',time:'10月05日 10:30',seed:7},
    {id:'EXP-025',title:'需求收缩冲击 · 风险响应观察',type:'宏观消息',kind:'negative',source:'近期部分行业订单出现下降，企业预计短期需求承压。影响持续时间和后续恢复速度仍存在不确定性。',note:'观察负向消息下的减仓请求，以及流动性对实际成交的影响。',time:'10月04日 15:42',seed:23},
    {id:'EXP-024',title:'审批不确定性 · 等待行为研究',type:'公司问答',kind:'uncertain',source:'投资者：项目是否已经获批？公司回复：目前仍在推进相关审批工作，最终结果和具体时间尚存在不确定性，请关注后续公告。',note:'研究信息尚未确认时，三类策略的等待与目标仓位变化。',time:'10月04日 09:18',seed:47},
  ];
  const roles = [
    {name:'激进型',key:'aggressive',icon:'bolt',color:'var(--orange)',sensitivity:.9,risk:.04,weight:30,desc:'对新增信息更敏感，较快调整目标仓位，允许更大的单次仓位变化。'},
    {name:'保守型',key:'conservative',icon:'shield',color:'var(--blue)',sensitivity:.25,risk:.008,weight:20,desc:'重视消息不确定性，通过等待确认和较低风险预算控制仓位变化。'},
    {name:'机构型',key:'institutional',icon:'building',color:'var(--purple)',sensitivity:.5,risk:.016,weight:35,desc:'结合组合风险和定期再平衡规则，在多个资产之间分配有限资金。'},
  ];
  const state = {page:'experiments',experimentView:'saved',selected:0,step:8,asset:'A',decisionGroup:'with_message',wizard:1,search:'',sourceFilter:'all',draft:{title:'',source:'',type:'政策消息',published:'2026-10-05T09:00',signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000}};
  const analysisBinding = new SourceAnalysisBinding(state.draft.source);
  const draftSubmissions = new WeakMap(), draftConfirmations = new WeakMap();
  let draftStorage=null;
  try{draftStorage=sessionStorage;}catch(_){}
  const draftCache=new MarketDraftCache.DraftCache(draftStorage);
  let draftAnalysisRecovery=null;
  let navigationVersion = 0;
  const strategyWorkspace = new StrategyWorkspace();
  const strategyPreview = new MarketStrategyPreview.PreviewState();
  const comparisonWorkspace = new MarketExperimentComparison.ComparisonState();
  let comparisonStorageError='';
  try{const saved=JSON.parse(sessionStorage.getItem('marketmirror:comparison-v1'));if(saved){comparisonWorkspace.select('left',saved.left??'');comparisonWorkspace.select('right',saved.right??'');}}catch(_){}
  let strategyPreviewTimer;
  const batchWorkspace = new MarketBatchView.BatchState();
  const caseWorkspace = new MarketCaseView.CaseState();
  const observedWorkspace = new MarketObservedView.ObservedState();
  const modelSettings = new MarketModelView.ConnectionState();
  let caseCatalogLoading=false,caseCatalogVersion=0,caseCatalogError='';
  let observedCatalogLoading=false,observedCatalogVersion=0,observedCatalogError='';
  const batchForm = {title:'三类策略情景对照',seeds:'1, 7, 19',sessions:18,duration:6,cash:1000000};
  let batchFormError='', batchListLoading=false, batchListVersion=0, batchPollTimer;
  let batchArchivedExperiment=null, batchOpenVersion=0;
  try { batchWorkspace.selectedId=sessionStorage.getItem('marketmirror:selected-batch'); } catch (_) {}
  const content = document.getElementById('content');
  const dialog = document.getElementById('detail-dialog');
  let modalDownloadUrls=[],modalGeneration=0;
  let toastTimer, rememberedExperimentId = null;
  try { rememberedExperimentId = sessionStorage.getItem('marketmirror:selected-experiment'); } catch (_) {}
  function rememberExperiment() {
    rememberedExperimentId = current().id;
    try { sessionStorage.setItem('marketmirror:selected-experiment', rememberedExperimentId); } catch (_) {}
  }
  const current = () => batchArchivedExperiment || experiments[state.selected];
  const labels = {analysis:'结果分析',experiments:'实验空间',compare:'实验比较',batches:'批量对照',cases:'真实原文案例',observed:'历史行情实验',sources:'消息资料',agents:'策略配置',new:'新建实验'};
  const format = n => n.toLocaleString('zh-CN');
  const signed = n => MarketDecisionView.formatNumber(n,6,true);
  const heading = (eyebrow, title, caption, actions = '') => `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><div class="heading-caption">${caption}</div></div><div class="heading-actions">${actions}</div></div>`;
  const newButton = () => `<button class="btn primary" type="button" data-action="new">${icon('plus')}新建实验</button>`;
  function toast(message) { const el=document.getElementById('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),3500); }
  function modal(title, body, footer = `<button type="button" class="btn primary" data-action="close">知道了</button>`, section='') {
    modalGeneration++;
    releaseModalDownloads();
    dialog.dataset.section=section;
    document.getElementById('dialog-content').innerHTML=`<div class="dialog-head"><h2>${title}</h2><button class="icon-button" type="button" data-action="close" aria-label="关闭对话框">${icon('close')}</button></div><div class="dialog-body">${body}</div><div class="dialog-footer">${footer}</div>`;
    if (!dialog.open) dialog.showModal();
  }
  function releaseModalDownloads(){for(const url of modalDownloadUrls)URL.revokeObjectURL(url);modalDownloadUrls=[];}
  function showDownloadFiles(title,body,files,section){
    const prepared=[];
    try{
      for(const f of files){
        if(f.href){prepared.push({...f,url:f.href,bytes:Number.isFinite(f.bytes)?f.bytes:null,server:true});continue;}
        const blob=new Blob([f.content],{type:f.type+';charset=utf-8'});
        prepared.push({...f,url:URL.createObjectURL(blob),bytes:blob.size,server:false});
      }
      const size=f=>f.bytes==null?'服务器直链':f.bytes>=1048576?(f.bytes/1048576).toFixed(1)+' MB':Math.max(1,Math.ceil(f.bytes/1024))+' KB';
      modal(title,body+`<p class="download-package-note">文件已按本次读取的版本准备。服务器直链会以附件形式保存；浏览器生成的报告可直接打开查看。</p>`,prepared.map(f=>`<a class="btn ${f.primary?'primary':''}" href="${esc(f.url)}" ${f.server?'target="_blank" rel="noopener"':''} download="${esc(f.filename)}">${icon('download')}${esc(f.label)}<small>${size(f)}</small></a>`).join('')+'<button class="btn" type="button" data-action="close">关闭</button>',section);
      modalDownloadUrls.push(...prepared.filter(f=>!f.server).map(f=>f.url));
    }catch(error){for(const f of prepared)URL.revokeObjectURL(f.url);throw error;}
  }
  function closeModalForNavigation(){if(dialog.open)dialog.close();}
  function navigate(page) { closeModalForNavigation();if(state.page==='new'&&page!=='new')saveDraft();navigationVersion++;if(page==='analysis')rememberExperiment();state.page=page;location.hash=page;render();document.querySelector('.sidebar').classList.remove('open');window.scrollTo({top:0,behavior:'instant'}); }
  function analysis() {
    if(!experimentsLoaded)return `<section class="panel"><div class="summary-content"><h1>${experimentLoading?'正在读取本机实验…':'本机实验尚未加载'}</h1><p>读取完成后显示已有撮合记录与结果。</p>${experimentLoading?'':'<button class="btn" data-action="refresh-runs">重新加载记录</button>'}</div></section>`;
    if(!current().custom)return heading('EXPERIMENT ANALYSIS','选择一个已保存实验','先运行实验，再查看三类策略的决策与成交。',newButton())+'<section class="panel"><div class="summary-content"><p>尚未选择可读取的本机实验。可以新建实验，也可以在实验空间复用示例模板。</p><button class="btn" data-nav="experiments">打开实验空间</button></div></section>';
    if(current().corrupt)return `<section class="panel"><div class="summary-content"><h1>实验记录需要恢复</h1><p>记录编号：${esc(current().id)}</p><p>配置文件无法读取，不能运行、复制或导出此实验。原文件仍保留在本机；请从已知完整的备份恢复后重新加载。</p><button class="btn" data-action="refresh-runs">重新加载记录</button></div></section>`;
    return (batchArchivedExperiment?.comparison_archive?`<div class="callout"><div><strong>正在查看实验比较时读取的结果快照</strong><p>结果版本 ${esc(current().backendResult.provenance.result_id.slice(0,10))}，与比较页保持一致。</p><button class="btn compact" data-action="back-to-comparison">返回实验比较</button></div></div>`:batchArchivedExperiment?`<div class="callout"><div><strong>正在查看批次归档结果</strong><p>结果版本 ${esc(current().backendResult.provenance.result_id.slice(0,10))}。本页读取批次保存的版本；之后单独重跑不会改变此归档。</p><button class="btn compact" data-action="back-to-batch">返回批量对照</button></div></div>`:'')+runStatusPanel(current())+savedAnalysis();
  }
  function experimentRows(){
    const templates=state.experimentView==='templates',rows=experiments.filter(e=>Boolean(e.custom)!==templates&&(e.title+e.type).toLowerCase().includes(state.search.toLowerCase()));
    return rows.length?rows.map(e=>{const inconsistent=e.run_status==='completed'&&!e.backendResult,status=e.custom?(e.corrupt?'记录损坏 · 需恢复':e.run_status==='running'?'运行中':e.run_status==='unknown'?'状态待确认':e.run_status==='failed'?'运行失败':e.run_status==='interrupted'?'运行已中断':inconsistent?'结果缺失 · 请刷新':e.backendResult?(e.backendResult.mode==='synthetic_market'?'已完成 · 撮合实验':'已完成 · 历史预览'):'已保存 · 待运行'):'示例模板 · 虚构文本';
      const action=e.custom?'data-open':'data-use',index=experiments.indexOf(e);
      const decisionLabel=e.backendResult?.mode==='platform_preview'?'旧版公式预览':'本地规则决策';
      const modelLine=e.custom&&!e.corrupt?(e.text_analysis?`<small class="experiment-model-line"><i></i>事实提取：${esc(e.text_analysis.model||'模型名称未存档')} · ${decisionLabel}</small>`:`<small class="experiment-model-line manual"><i></i>未关联模型分析 · ${decisionLabel}</small>`):'';
      return `<tr class="experiment-row"><td>${e.corrupt?`<span class="experiment-name">${esc(e.title)}</span>`:`<button class="subtle-link experiment-name" type="button" ${action}="${index}">${esc(e.title)}</button>`}<small>${esc(e.id)} · ${esc(e.type)}</small>${e.custom&&!e.corrupt?`<small>信号 ${esc(e.signal)} · 不确定性 ${esc(e.uncertainty)}</small>`:''}${modelLine}</td><td><span class="badge neutral">${status}</span></td><td>${e.corrupt?'不可读取':`${e.sessions||18} 步 / 三类策略`}</td><td>${e.corrupt?'—':e.seed}</td><td>${e.custom?esc(e.time):'内置模板'}</td><td>${e.corrupt?'<span class="form-hint">需恢复</span>':`<button class="icon-button" ${action}="${index}" aria-label="${e.custom?'打开':'复用'}${esc(e.title)}">${icon('arrow')}</button>`}</td></tr>`;
    }).join(''):`<tr><td colspan="6" class="empty">${state.search?'没有找到匹配记录。':templates?'暂无示例模板。':experimentLoading?'正在读取本机实验…':'暂无已加载的本机实验，点击“新建实验”开始，或复用示例模板。'}</td></tr>`;
  }
  function experimentsPage(){
    const saved=experiments.filter(e=>e.custom),templates=experiments.filter(e=>!e.custom),view=state.experimentView==='templates';
    const latest=saved.find(e=>!e.corrupt&&e.backendResult?.mode==='synthetic_market'),hint=view?'选择模板后编辑原文和假设，再运行自己的实验。':latest?'打开已有结果，或在实验比较中核对参数调整的效果。':'输入一段消息，设定假设，运行三类策略的对照。';
    return heading('YOUR EXPERIMENTS','实验空间','每一次实验，都保留信息、假设与结果。',`<button class="btn" type="button" data-nav="compare">比较已有实验</button>${hasDraftContent()?'<button class="btn" type="button" data-action="resume-draft">继续草稿</button>':''}${newButton()}`)
      +`<div class="overview-intro"><div><div class="eyebrow">从一个问题开始</div><h2>同一条消息，不同策略会怎样行动？</h2><p>${hint}</p></div>${!view&&latest?`<button class="btn" data-open="${experiments.indexOf(latest)}">查看最新撮合实验 ${icon('arrow')}</button>`:`<button class="btn" data-nav="cases">查看真实原文案例 ${icon('arrow')}</button>`}</div>`
      +`<div class="experiment-tools"><div class="segmented" role="group" aria-label="实验记录范围">${[['saved','本机实验',saved.length],['templates','示例模板',templates.length]].map(([key,label,count])=>`<button type="button" data-experiment-view="${key}" class="${state.experimentView===key?'active':''}" aria-pressed="${state.experimentView===key}">${label} · ${count}</button>`).join('')}</div><div class="searchbox">${icon('search')}<input id="experiment-search" type="search" value="${esc(state.search)}" placeholder="搜索实验名称或消息类型" aria-label="搜索实验"></div></div>`
      +(view?'<div class="callout">示例模板使用虚构文本；点击复用进入新建流程，保存并运行后生成自己的撮合结果。</div>':'')
      +`<section class="panel table-wrap"><table><thead><tr><th>实验名称</th><th>状态</th><th>实验范围</th><th>种子</th><th>创建时间</th><th></th></tr></thead><tbody id="experiment-rows">${experimentRows()}</tbody></table></section>`;
  }
  function rememberComparison(){
    try{sessionStorage.setItem('marketmirror:comparison-v1',JSON.stringify({left:comparisonWorkspace.leftId,right:comparisonWorkspace.rightId}));comparisonStorageError='';}
    catch(_){comparisonStorageError='浏览器未保存实验选择，刷新后需重新选择。';}
  }
  function comparisonPage(){
    return heading('EXPERIMENT COMPARISON','实验比较','核对输入条件，再观察三类策略的参数调整与实际执行。',`<button class="btn" type="button" data-comparison-action="variant" ${comparisonWorkspace.status!=='ready'?'disabled':''}>调整基准策略再跑</button><button class="btn" type="button" data-comparison-action="export" ${comparisonWorkspace.status!=='ready'?'disabled':''}>${icon('download')}报告与导出</button>`)
      +(comparisonStorageError?`<p class="form-error">${esc(comparisonStorageError)}</p>`:'')
      +(!experimentsLoaded?'<section class="panel"><div class="summary-content">正在读取本机实验…</div></section>':MarketExperimentComparison.renderPicker(comparisonWorkspace,experiments)+`<div id="experiment-comparison-body">${MarketExperimentComparison.renderBody(comparisonWorkspace)}</div>`);
  }
  async function loadExperimentComparison(){
    const ticket=comparisonWorkspace.begin();if(!ticket){if(state.page==='compare')render();return;}
    if(state.page==='compare')render();
    try{
      const snapshots=await Promise.all([apiJson(`/api/platform/experiments/${ticket.leftId}/export`),apiJson(`/api/platform/experiments/${ticket.rightId}/export`)]);
      await MarketExperimentComparison.verifySources(snapshots.map(s=>s.experiment));
      comparisonWorkspace.accept(ticket,snapshots[0].experiment,snapshots[1].experiment);
    }catch(error){comparisonWorkspace.fail(ticket,error.message);}
    finally{if(state.page==='compare')render();}
  }
  function includeInComparison(record){
    if(!record?.custom||record.backendResult?.mode!=='synthetic_market'){toast('请先完成撮合实验再比较。');return;}
    comparisonWorkspace.select(comparisonWorkspace.leftId&&comparisonWorkspace.leftId!==record.id?'right':'left',record.id);
    rememberComparison();navigate('compare');
  }
  function createStrategyVariant(record){
    batchArchivedExperiment=null;useSample(-1,record);state.draft.title=record.title.slice(0,110)+' · 策略调整';state.wizard=2;persistDraft();render();toast('已复制消息与市场条件，调整本次策略参数后可运行。');
  }
  function openComparisonSnapshot(id){
    const record=[comparisonWorkspace.left,comparisonWorkspace.right].find(r=>r?.id===id);if(!record)return;
    batchArchivedExperiment={...record,custom:true,comparison_archive:{result_id:record.result_id}};
    state.step=comparisonWorkspace.step;state.asset=comparisonWorkspace.asset;state.decisionGroup=comparisonWorkspace.group;
    navigate('analysis');
  }
  async function openComparisonDownloads(){
    const snapshot=MarketExperimentComparison.exportSnapshot(comparisonWorkspace);if(!snapshot)return;
    modal('实验报告与导出','<p role="status">正在核对来源并准备报告…</p>','<button class="btn" type="button" data-action="close">关闭</button>','comparison-export');
    const generation=modalGeneration;
    const active=()=>dialog.open&&dialog.dataset.section==='comparison-export'&&modalGeneration===generation;
    try{
      await MarketExperimentComparison.verifySources([snapshot.left,snapshot.right]);
      const report=MarketExperimentComparison.buildReport(snapshot);if(!active())return;
      const filename=`marketmirror-compare-${snapshot.left.id.slice(0,8)}-${snapshot.right.id.slice(0,8)}`;
      showDownloadFiles('实验报告与导出',`<style>${MarketExperimentComparison.reportStyles}</style>${MarketExperimentComparison.renderReport(report)}`,[
        {label:'中文报告',filename:filename+'.md',type:'text/markdown',content:MarketExperimentComparison.markdownReport(report),primary:true},
        {label:'离线报告',filename:filename+'.html',type:'text/html',content:MarketExperimentComparison.htmlReport(report)},
        {label:'原始双实验 JSON',filename:filename+'-raw.json',type:'application/json',href:`/api/platform/comparisons/${encodeURIComponent(snapshot.left.id)}/${encodeURIComponent(snapshot.right.id)}/export`}
      ],'comparison-export');
    }catch(error){if(active())modal('报告准备失败',`<p>${esc(error.message)}</p>`,'<button class="btn" type="button" data-action="close">关闭</button>','comparison-export');}
  }
  function openRecordDownloads(record){
    if(!record.custom)return;
    const snapshot={artifact:'MarketMirror experiment',schema_version:'platform-export-v2',mode:record.backendResult?.mode||'not_run',experiment:record,selected_asset:state.asset,selected_step:state.step,selected_information_condition:state.decisionGroup,fixture:null};
    showDownloadFiles('导出当前实验快照',`<p>${esc(record.title)}</p><p class="form-hint">保存当前配置、文本依据和账本${record.result_id?' · 结果版本 '+esc(record.result_id.slice(0,10)):''}。不会重新运行实验。</p>`,[{label:'下载实验 JSON',filename:`marketmirror-${record.id}.json`,type:'application/json',content:JSON.stringify(snapshot,null,2),primary:true}],'experiment-export');
  }
  function openRiskOverviewDownloads(){
    const bundle=state.page==='cases'?caseWorkspace.detail:null,record=bundle?null:current();
    const result=bundle?.result||record?.backendResult;if(!result?.paths)return;
    const snapshot={artifact:'MarketMirror portfolio risk overview',
      source:bundle?{case_id:bundle.case.case_id,seed:bundle.seed,mode:bundle.mode}:{experiment_id:record.id,title:record.title},
      metrics:MarketPortfolioMetrics.buildOverview(result)};
    showDownloadFiles('导出收益、风险与执行指标','<p>指标按当前读取的完整账本计算，JSON 保留各步组合净值、超限账户与来源标识。不会重跑实验或调用模型。</p>',
      [{label:'下载指标 JSON',filename:`marketmirror-risk-${bundle?bundle.case.case_id.slice(0,24)+'-'+bundle.seed+'-'+bundle.mode:record.id}.json`,type:'application/json',content:JSON.stringify(snapshot,null,2),primary:true}],'risk-export');
  }
  function jumpToRiskStep(step,group){
    if(!Number.isSafeInteger(step)||step<1||!['with_message','baseline'].includes(group))return;
    let id;
    if(state.page==='cases'&&caseWorkspace.detail){
      if(step>caseWorkspace.detail.result.paths[group].trace.length)return;
      caseWorkspace.step=step;caseWorkspace.group=group;updateCasePanels();id='case-step';
    }else if(state.page==='analysis'&&current().backendResult?.paths){
      if(step>current().backendResult.paths[group].trace.length)return;
      state.step=step;state.decisionGroup=group;render();id='market-step';
    }else return;
    const input=document.getElementById(id);input?.closest('.timeline-control')?.scrollIntoView({block:'start',behavior:'smooth'});input?.focus({preventScroll:true});
  }
  function sourcesPage(){
    const saved=experiments.filter(e=>e.custom),examples=experiments.filter(e=>!e.custom);
    const cards=rows=>rows.filter(e=>!e.corrupt&&typeof e.source==='string').map(e=>`<article class="panel source-card"><div class="source-label">${icon('document')}<span class="badge neutral">${esc(e.type)}</span><span class="badge neutral">${e.custom?'已保存原文':'虚构示例'}</span></div><h2>${esc(e.title)}</h2><details><summary>展开消息原文 · ${e.source.length} 字符</summary><p style="white-space:pre-wrap">${esc(e.source)}</p></details><p class="form-hint">${e.custom?(e.text_analysis?'已关联事实提取与引文；不代表事实真实性已验证。':'未关联模型分析。'):'仅用于体验操作流程。'}</p><div class="source-card-bottom"><span class="form-hint">实验 ${esc(e.id)}</span><button class="subtle-link" type="button" data-use="${experiments.indexOf(e)}">复制消息与配置 ${icon('arrow')}</button></div><button class="subtle-link" type="button" data-open="${experiments.indexOf(e)}">查看关联实验</button></article>`).join('');
    return heading('INFORMATION LIBRARY','消息资料','查看已保存消息，复用原文与实验配置。',`<button class="btn primary" type="button" data-action="new">${icon('plus')}使用新消息</button>`)
      +`<div class="section-title"><h2>已保存消息 <small>${saved.length} 条实验记录</small></h2></div><div class="source-cards">${saved.length?cards(saved):'<p class="empty">暂无已加载的本机消息。保存实验后，消息原文会出现在这里。</p>'}</div>`
      +`<div class="section-title"><h2>体验示例</h2></div><div class="callout">以下示例为虚构文本，用于体验三类策略的实验流程。</div><div class="source-cards">${cards(examples)}</div>`;
  }
  const strategyFields = [
    {key:'text_sensitivity',label:'文本敏感度',scale:1,step:.05,hint:'信息信号对目标仓位的影响程度。'},
    {key:'base_weight',label:'基础股票权重',scale:100,step:1,hint:'没有新增信号时的组合股票权重。'},
    {key:'risk_budget',label:'风险预算',scale:100,step:.1,hint:'数值越低，对组合波动的约束越强。'}
  ];
  const copyParameters = value => value ? JSON.parse(JSON.stringify(value)) : null;
  const strategyValue = (key,value) => StrategyParameterControls.format(value,strategyFields.find(field=>field.key===key));
  function strategyTable(parameters){
    return `<div class="table-wrap"><table><thead><tr><th>策略</th>${strategyFields.map(f=>`<th>${f.label}</th>`).join('')}</tr></thead><tbody>${roles.map(r=>`<tr><td><span class="row-dot" style="--role:${r.color}"></span>${r.name}</td>${strategyFields.map(f=>`<td>${strategyValue(f.key,parameters[r.key][f.key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function strategyCards(parameters,scope){
    return `<div class="role-editor-grid strategy-editor-grid">${roles.map(r=>{const rules=strategyWorkspace.fixedRules[r.key];return `<section class="panel role-editor" style="--role:${r.color}"><div class="agent-mark">${icon(r.icon)}</div><h2>${r.name}</h2><p class="role-description">${r.desc}</p>${strategyFields.map(field=>StrategyParameterControls.render({field,role:r.key,scope,value:parameters[r.key][field.key],bounds:strategyWorkspace.limits[r.key][field.key],disabled:strategyWorkspace.saving})).join('')}<div class="summary-assumption">最高股票权重 ${Math.round(rules.max_weight*100)}% · 连续确认 ${rules.confirmation_steps} 步 · 每 ${rules.rebalance_interval} 步再平衡</div>${scope==='workspace'?`<div data-preview-role="${r.key}">${MarketStrategyPreview.renderRole(strategyPreview,r.key)}</div>`:''}</section>`;}).join('')}</div>`;
  }
  function strategyUnavailable(){return `<div class="analysis-status" role="status">${strategyWorkspace.loading?'正在读取本机策略参数…':esc(strategyWorkspace.error||'策略参数尚未加载')} ${strategyWorkspace.loading?'':'<button class="btn compact" data-action="retry-strategies">重新加载</button>'}</div>`;}
  function strategyInputValid(scope){
    const invalid=document.querySelector(`[data-parameter-number][data-strategy-scope="${scope}"][aria-invalid="true"]`);
    if(invalid){invalid.focus();toast('请先修正标记的策略参数。');return false;}return true;
  }
  function handleStrategyInput(input){
    const {strategyScope:scope,strategyRole:role,strategyKey:key}=input.dataset,field=strategyFields.find(f=>f.key===key),bounds=strategyWorkspace.limits[role][key];
    try{
      const value=StrategyParameterControls.parseDisplay(input.value,field,bounds);
      if(scope==='workspace')strategyWorkspace.edit(role,key,value);
      else{state.draft.strategy_parameters[role][key]=strategyWorkspace.validate(role,key,value);persistDraft();}
      StrategyParameterControls.sync(input,value,field,bounds);
    }catch(error){StrategyParameterControls.showError(input,error.message);}
    const invalid=Boolean(document.querySelector(`[data-parameter-number][data-strategy-scope="${scope}"][aria-invalid="true"]`));
    if(scope==='workspace'){
      const save=document.querySelector('[data-action="save-roles"]');if(save)save.disabled=strategyWorkspace.saving||invalid;
      document.getElementById('strategy-save-state').textContent=invalid?'有参数输入不完整或超出范围':strategyWorkspace.dirty?'有未保存的修改':'当前参数与保存值一致';
      scheduleStrategyPreview(invalid?'请先修正策略参数，当前预览已失效':'');
    }else{
      const next=document.querySelector('[data-action="next"]');if(next)next.disabled=invalid;
      const error=document.getElementById('form-error'),message='请先修正策略中输入不完整或超出范围的参数。';
      if(error&&(invalid||error.textContent===message))error.textContent=invalid?message:'';
    }
  }
  function draftStrategyPanel(){
    if(!state.draft.strategy_parameters||!strategyWorkspace.parameters)return strategyUnavailable();
    return `<div class="draft-strategy-section"><h2>本次实验的三类策略</h2><p class="evidence-note">参数将随本次实验独立保存。这里的调整仅用于本次实验。</p><details class="strategy-disclosure"><summary>展开策略参数</summary>${strategyCards(state.draft.strategy_parameters,'experiment')}</details></div>`;
  }
  function strategyArchitecturePanel(){
    return `<section class="panel strategy-model-panel"><div class="panel-header"><div><h2>三个 Agent 背后的模型</h2><p class="panel-subtitle">1 个共享 LLM + 1 个参数化决策引擎，三种配置表达行为差异</p></div><span class="badge neutral">共享 LLM · 规则引擎</span></div><div class="strategy-model-flow"><article><span class="strategy-model-icon">${icon('document')}</span><small>01 · 原文理解</small><h3>共享 DeepSeek 提取事实</h3><p>使用接入的 DeepSeek-V4-Flash-0731-W8A8，提取陈述、状态和原文引文，作为情景设置与决策追溯的文本依据。</p><button class="subtle-link" type="button" data-action="settings">查看模型连接 ${icon('arrow')}</button></article><article><span class="strategy-model-icon">${icon('agents')}</span><small>02 · 决策偏好</small><h3>三个角色，同一决策引擎</h3><p>市场与文本信号进入本地规则，按各自的敏感度、不确定性偏好和风险预算计算仓位。</p><span class="strategy-model-roles">${roles.map(r=>`<span style="--role:${r.color}"><i></i>${r.name}</span>`).join('')}</span></article><article><span class="strategy-model-icon">${icon('shield')}</span><small>03 · 交易执行</small><h3>确定性约束与撮合</h3><p>再检查连续确认、再平衡、集中度和资金限制，分别记录目标、订单与实际成交。</p><span class="strategy-model-note">每一步均可查看计算与账本依据</span></article></div><div class="strategy-model-boundary"><p>三个 Agent 没有各自独立的大模型。当前没有分别训练三个投资者模型，角色差异来自预设规则；DeepSeek 负责理解原文，仓位与订单由本地决策引擎计算，市场每一步不调用大模型。</p><details><summary>查看输入如何变成仓位</summary><p>判断分值 = 市场敏感度 × 市场信号 + 文本敏感度 × 文本方向 − 不确定性偏好 × 不确定性。无文本条件关闭后两项。</p><p>基础权重与分值先形成目标，风险预算和最高仓位再限制它。三资产情景还按各资产分值分配组合，并检查协方差与机构集中度；历史单资产回放使用单资产风险约束。</p><p>新建情景的文本方向和不确定性由你设定；归档研究案例使用已登记的事实映射。提取事实本身不等于预测涨跌。</p><p>激进型响应更快；保守型需要连续确认、对不确定性更敏感；机构型按再平衡周期行动，并在三资产实验中限制集中度。触及风险约束时可以优先减仓。</p></details></div></section>`;
  }
  function experimentModelBoundary(e){
    const hasAnalysis=Boolean(e.text_analysis),model=hasAnalysis?(e.text_analysis.model||'模型名称未存档'):'未关联模型分析';
    const description=hasAnalysis?'已关联原文事实、状态和引文，作为情景参考；方向与不确定性由你设定，模型不直接下单。':'本实验没有关联模型事实提取。方向与不确定性由你设定，三类策略依据这些情景变量决策。';
    return `<section class="panel experiment-model-boundary"><div class="panel-header"><div><h2>本实验的模型边界</h2><p class="panel-subtitle">看清文本理解、策略决策和市场撮合分别由谁完成</p></div><span class="badge neutral">可追溯链路</span></div><div class="experiment-model-lanes"><div><small>文本理解</small><strong>${esc(model)}</strong><p>${description}</p></div><div><small>三类 Agent</small><strong>本地参数化决策引擎</strong><p>同一公式和约束，分别使用敏感度、风险预算、连续确认与再平衡参数。</p></div><div><small>市场执行</small><strong>确定性撮合与账本</strong><p>记录目标、订单、接受量、成交量和账户净值；市场每一步不重新调用大模型。</p></div></div></section>`;
  }
  function agentsPage(){
    const unavailable=!strategyWorkspace.parameters,disabled=unavailable||strategyWorkspace.saving;
    return heading('AGENT STRATEGIES','三类策略配置','用明确的行为规则，表达不同的决策偏好。',`<button class="btn" data-action="reset-roles" ${disabled?'disabled':''}>${icon('replay')}恢复平台默认</button><button class="btn primary" data-action="save-roles" ${disabled?'disabled':''}>${strategyWorkspace.saving?'正在保存…':'保存为工作区默认'}</button>`)
      +strategyArchitecturePanel()
      +(unavailable?strategyUnavailable():`<div class="callout">${icon('sliders')}保存后用于之后新建的实验。每次实验都保留独立参数，已有实验按原参数运行。</div><div class="strategy-save-state" id="strategy-save-state" role="status">${strategyWorkspace.error?esc(strategyWorkspace.error):strategyWorkspace.dirty?'有未保存的修改':strategyWorkspace.updatedAt?'已保存在本机 · 新建实验时自动载入':'当前使用平台默认参数'}</div><div id="strategy-preview-controls">${MarketStrategyPreview.renderControls(strategyPreview)}</div>${strategyCards(strategyWorkspace.draft,'workspace')}`);
  }
  function updateStrategyPreview(){
    if(state.page!=='agents')return;
    const status=document.getElementById('strategy-preview-status');
    if(status){status.textContent=MarketStrategyPreview.status(strategyPreview);status.className=`strategy-preview-status ${strategyPreview.status}`;}
    const create=document.querySelector('[data-preview-experiment]');if(create)create.disabled=strategyPreview.status!=='ready';
    document.querySelectorAll('[data-preview-role]').forEach(el=>{
      if(el.querySelector('.strategy-rule-preview:not(.placeholder)'))el.style.minHeight=`${el.getBoundingClientRect().height}px`;
      el.innerHTML=MarketStrategyPreview.renderRole(strategyPreview,el.dataset.previewRole);
    });
  }
  function scheduleStrategyPreview(message=''){
    const invalid=document.querySelector('[data-parameter-number][data-strategy-scope="workspace"][aria-invalid="true"]');
    clearTimeout(strategyPreviewTimer);strategyPreview.invalidate(message||(invalid?'请先修正策略参数，当前预览已失效':''));updateStrategyPreview();
    if(state.page!=='agents'||!strategyWorkspace.parameters||strategyPreview.status==='invalid')return;
    strategyPreviewTimer=setTimeout(runStrategyPreview,200);
  }
  async function runStrategyPreview(){
    if(state.page!=='agents'||!strategyWorkspace.parameters)return;
    const ticket=strategyPreview.begin(strategyWorkspace.draft);if(!ticket){updateStrategyPreview();return;}
    updateStrategyPreview();
    try{strategyPreview.accept(ticket,await apiJson('/api/platform/strategy-preview',{method:'POST',body:JSON.stringify(ticket.input)}));}
    catch(error){strategyPreview.fail(ticket,error.message);}
    finally{updateStrategyPreview();}
  }
  async function hydrateStrategies(){
    strategyWorkspace.loading=true;strategyWorkspace.error='';
    try{strategyWorkspace.load(await apiJson('/api/platform/strategies'));strategyPreview.invalidate();if(!state.draft.strategy_parameters)state.draft.strategy_parameters=strategyWorkspace.snapshot();}
    catch(error){strategyWorkspace.error='策略参数读取失败：'+error.message;}
    finally{strategyWorkspace.loading=false;if(['agents','new'].includes(state.page)){if(state.page==='new')saveDraft();render();}}
  }
  async function saveStrategies(){
    if(!strategyWorkspace.parameters||strategyWorkspace.saving)return;
    if(!strategyInputValid('workspace'))return;
    strategyWorkspace.saving=true;strategyWorkspace.error='';render();
    try{const profile=await apiJson('/api/platform/strategies',{method:'POST',body:JSON.stringify({parameters:strategyWorkspace.draft})});strategyWorkspace.load(profile);toast('工作区策略已保存，将用于之后新建的实验。');}
    catch(error){strategyWorkspace.error='保存失败：'+error.message;}
    finally{strategyWorkspace.saving=false;if(state.page==='agents')render();}
  }
  function experimentStrategyPanel(e){
    const parameters=parametersFromExperiment(e);
    if(!parameters||roles.some(r=>!parameters[r.key]))return '';
    return `<section class="panel experiment-strategies"><div class="panel-header"><h2>本实验策略参数</h2><span class="badge neutral">${e.strategy_parameters?'已随实验保存':'来自历史运行记录'}</span></div><p class="panel-subtitle strategy-table-note">有消息与无消息两组使用同一组策略参数。</p>${strategyTable(parameters)}</section>`;
  }
  function saveDraft(){document.querySelectorAll('[data-draft]').forEach(el=>{state.draft[el.dataset.draft]=['signal','uncertainty','duration','sessions','seed','cash'].includes(el.dataset.draft)?(el.value.trim()?Number(el.value):NaN):el.value;});document.querySelectorAll('[data-assumption]').forEach(el=>{if(el.dataset.assumption){const key=el.dataset.assumption;state.draft.market_assumptions={...(state.draft.market_assumptions||{scope:'A',market:0,volatility:.01}),[key]:key==='scope'?el.value:el.value.trim()?Number(el.value)/(key==='volatility'?100:1):NaN};}});analysisBinding.setSource(state.draft.source);persistDraft();}
  const factStatus = {confirmed:'原文陈述',uncertain:'尚不确定',question:'提问'};
  function analysisEvidence(analysis){
    const time=analysis.created_at?new Date(analysis.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未记录';
    return `<div class="evidence-meta"><span class="badge">引文匹配通过</span><span>${esc(analysis.model)}</span><span>${esc(time)} · 北京时间</span></div>
      <p class="evidence-note">以下为模型提取，原文引文已匹配。实验方向、强度与不确定性由你设置。</p>
      <div class="evidence-facts">${analysis.facts.length?analysis.facts.map((fact,index)=>`<article class="evidence-fact"><div class="evidence-fact-heading"><span class="trace-number">${index+1}</span><span class="badge neutral">${esc(factStatus[fact.status]||fact.status)}</span></div><p class="evidence-claim">${esc(fact.claim)}</p><blockquote>${esc(fact.quote)}</blockquote></article>`).join(''):'<p class="evidence-note">模型未提取到可用事实。这份空结果仍会如实保存。</p>'}</div>
      <details class="evidence-version"><summary>分析记录与版本</summary><dl><dt>记录编号</dt><dd>${esc(analysis.analysis_id)}</dd><dt>原文 SHA-256</dt><dd>${esc(analysis.source_sha256)}</dd><dt>提示词 SHA-256</dt><dd>${esc(analysis.prompt_sha256)}</dd></dl></details>`;
  }
  function draftAnalysisPanel(){
    if(analysisBinding.pending)return `<div class="analysis-status" role="status">${analysisBinding.restoring?'正在读取本机已保存的事实分析…':'正在使用 DeepSeek 提取事实…'}<button type="button" class="subtle-link" data-action="detach-analysis">取消等待，手动设置</button></div>`;
    if(analysisBinding.analysis)return `<div class="analysis-status"><span>${analysisBinding.analysis.facts.length} 条模型事实将随实验保存</span><button type="button" class="subtle-link" data-action="detach-analysis">取消关联</button></div><details class="evidence-draft" open><summary>事实与原文依据</summary>${analysisEvidence(analysisBinding.analysis)}</details>`;
    const message=analysisBinding.error?'提取失败：'+analysisBinding.error:analysisBinding.outdated?'原文已修改，旧分析已取消关联。可重新提取，或继续手动设置情景。':'可选：提取事实并随实验保存；也可直接手动设置情景。';
    return `<div class="analysis-status ${analysisBinding.error?'error':''}" role="status">${esc(message)}${analysisBinding.restoreError?'<button class="subtle-link" type="button" data-action="retry-restore-analysis">重新读取</button><button class="subtle-link" type="button" data-action="detach-analysis">取消关联，手动设置</button>':''}</div>`;
  }
  function updateDraftAnalysisPanel(){
    const panel=document.getElementById('draft-analysis-panel');if(panel)panel.innerHTML=draftAnalysisPanel();
    const button=document.querySelector('[data-action="analyze-source"]');
    if(button){button.disabled=analysisBinding.pending;button.textContent=analysisBinding.pending?(analysisBinding.restoring?'正在恢复分析…':'正在提取事实…'):'使用 DeepSeek 提取事实';}
    updateDraftSubmission();
  }
  function experimentEvidence(analysis){
    return `<section class="panel evidence-panel"><div class="panel-header"><h2>文本分析与原文依据</h2><span class="badge neutral">${analysis?'已关联':'手动情景'}</span></div><div class="summary-content">${analysis?analysisEvidence(analysis):'<p class="evidence-note">此实验未关联模型分析，情景变量由用户直接设定。</p>'}</div></section>`;
  }
  function wizardPage(){const d=state.draft,ticket=draftSubmissions.get(d);return heading('NEW EXPERIMENT','从一条消息，建立一个实验','先定义消息与假设，再观察三类策略的响应。',`<button class="btn ghost" data-action="cancel-new" type="button">返回实验空间</button>`)+`<div id="draft-cache-status" class="draft-cache-status${draftCache.error?' error':''}" role="status">${esc(draftCacheMessage())}</div><div class="stepper" aria-label="创建步骤">${['消息与来源','情景与策略','确认实验'].map((label,i)=>`<div class="step-item ${state.wizard===i+1?'active':''}"><span>${state.wizard>i+1?'✓':i+1}</span>${label}</div>`).join('')}</div><div class="wizard-layout"><section class="panel"><div class="form-body">${state.wizard===1?`
      <div class="form-field"><label for="draft-title">实验名称</label><input id="draft-title" data-draft="title" maxlength="70" value="${esc(d.title)}" placeholder="例如：流动性宽松消息对三类策略的影响"></div><div class="form-two"><div class="form-field"><label for="draft-type">消息类型</label><select id="draft-type" data-draft="type">${['政策消息','宏观消息','公司问答','其他消息'].map(x=>`<option ${x===d.type?'selected':''}>${x}</option>`).join('')}</select></div><div class="form-field"><label for="draft-time">发布时间 · 北京时间</label><input id="draft-time" type="datetime-local" data-draft="published" value="${esc(d.published)}"></div></div><div class="form-field"><label for="draft-source">消息原文</label><button class="btn" type="button" data-action="analyze-source">使用 DeepSeek 提取事实</button><textarea id="draft-source" data-draft="source" maxlength="12000" placeholder="粘贴政策、新闻或公司问答原文。公司问答请同时保留提问和回复。">${esc(d.source)}</textarea><small>保留原文，便于在决策详情中查看证据与信息可见时间。</small></div><div id="draft-analysis-panel">${draftAnalysisPanel()}</div><div class="form-hint">也可以从示例开始</div><div class="sample-picker">${experiments.filter(e=>!e.custom).map(e=>`<button type="button" data-sample="${experiments.indexOf(e)}">${esc(e.title.split(' · ')[0])}</button>`).join('')}</div>
      `:state.wizard===2?`<h2>文本变量与情景假设</h2><div class="review-box"><strong>情景假设 · 变量由你设定</strong><p>参考下方事实设定方向与强度。直接新建消息默认从中性信号 0 开始；从预览创建或复制实验会保留所选参数。模型提取不会替你判断利好或利空，也不会改动这些参数。</p></div><div id="draft-analysis-panel">${draftAnalysisPanel()}</div>${MarketExperimentScenario.renderSignalFields(d)}<div class="form-two"><div class="form-field"><label for="draft-duration">消息持续步数</label><select id="draft-duration" data-draft="duration">${[3,6,9].map(n=>`<option value="${n}" ${Number(d.duration)===n?'selected':''}>${n} 个决策步</option>`).join('')}</select></div><div class="form-field"><label for="draft-seed">随机种子</label><input id="draft-seed" type="number" data-draft="seed" min="0" max="999999" value="${d.seed}"></div></div><div class="form-field"><label for="draft-sessions">实验总步数</label><input id="draft-sessions" type="number" data-draft="sessions" min="1" max="60" step="1" value="${d.sessions||18}"><small>支持 1–60 步。第 5 步消息可见；运行不足 5 步可检查消息公开前的对照。</small></div><div class="form-field"><label for="draft-cash">每类策略初始资金（模型元）</label><input id="draft-cash" type="number" data-draft="cash" min="10000" max="100000000" step="10000" value="${d.cash}"></div>${MarketExperimentScenario.renderFields(d)}<div class="summary-assumption">模拟资产 A / B / C。市场偏移与波动率假设作用于两组；有消息组另接收所选范围的文本输入。</div>${MarketExperimentScenario.renderReference(d)}${draftStrategyPanel()}`:`<h2>确认实验配置</h2><div class="review-box"><strong>${esc(d.title)}</strong><p>${esc(d.source.slice(0,180))}${d.source.length>180?'…':''}</p></div>${[['消息类型',d.type],['发布时间',d.published.replace('T',' ')+'（北京时间）'],['参与策略','激进型 / 保守型 / 机构型'],['信息条件','有消息 / 无消息'],['情景变量',`信号 ${d.signal} · 不确定性 ${d.uncertainty}`],['消息持续时间',`${d.duration} 步`],['实验总步数',`${d.sessions} 步`],['初始资金',format(d.cash)+' 模型元 / 类'],['随机种子',d.seed]].map(([a,b])=>`<div class="review-field"><span class="form-hint">${a}</span><span>${esc(b)}</span></div>`).join('')}${MarketExperimentScenario.renderSummary(d)}${MarketExperimentScenario.renderReference(d)}<div id="draft-analysis-panel">${draftAnalysisPanel()}</div>${d.strategy_parameters?strategyTable(d.strategy_parameters):strategyUnavailable()}<label class="checkline"><input id="confirm-demo" type="checkbox" ${draftConfirmations.get(d)?'checked':''}>我理解这是合成市场实验，情景变量由我设定，结果不代表真实市场预测。</label>`}</div><div id="draft-submission-panel">${draftSubmissionPanel()}</div><div class="form-error" id="form-error" role="alert">${submissionMatches(ticket)?esc(ticket.error||''):''}</div><div class="form-footer">${state.wizard>1?'<button class="btn" type="button" data-action="previous">上一步</button>':'<span class="form-hint">提交后保存到本机</span>'}<span id="draft-submit-control">${draftSubmitControl()}</span></div></section><aside class="guide-block"><div class="eyebrow">EXPERIMENT GUIDE</div><h2>先明确假设，再观察结果</h2><p>让消息、策略和执行之间的每一个环节都可见。</p><div class="guide-item">${icon('document')}保留事实与原文证据</div><div class="guide-item">${icon('clock')}明确消息可见时间</div><div class="guide-item">${icon('agents')}比较三类策略的差异</div><div class="guide-item">${icon('layers')}保留无消息对照组</div><div class="guide-item">${icon('replay')}保存参数以便复现</div><div class="summary-assumption">可先用 DeepSeek 提取事实，再设定情景变量。运行后保存消息、分析依据、参数与撮合账本。</div></aside></div>`;}
  let experimentLoadError='', experimentLoadVersion=0, experimentLoading=true, experimentsLoaded=false;
  function render(){document.querySelector('.nav-count').textContent=experiments.filter(e=>e.custom).length;document.querySelector('.preview-tag').textContent=current().custom&&state.page==='analysis'?'已保存实验':'本机研究空间';document.getElementById('breadcrumb-current').textContent=labels[state.page]||'结果分析';document.querySelectorAll('[data-nav]').forEach(a=>{const active=a.dataset.nav===state.page;a.classList.toggle('active',active);active?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current');});content.innerHTML=(experimentLoadError&&['experiments','analysis','sources','compare'].includes(state.page)?`<div class="callout" role="alert"><div><strong>本机实验记录未能完整加载</strong><p>${esc(experimentLoadError)}。现有页面内容可能不是最新状态，不代表记录已丢失。</p><button class="btn" data-action="refresh-runs">重新加载记录</button></div></div>`:'')+({analysis,experiments:experimentsPage,compare:comparisonPage,batches:batchesPage,cases:casesPage,observed:observedPage,sources:sourcesPage,agents:agentsPage,new:wizardPage}[state.page]||analysis)();icons();if(state.page==='new')updateDraftAnalysisPanel();clearTimeout(batchPollTimer);if(state.page==='batches'){if(!batchListLoading&&!batchWorkspace.busy)hydrateBatches();else scheduleBatchPoll();}if(state.page==='cases'&&!caseCatalogLoading)hydrateSourceCases();if(state.page==='observed'&&!observedCatalogLoading)hydrateObservedExperiments();if(state.page==='agents'&&strategyWorkspace.parameters&&strategyPreview.status==='idle')scheduleStrategyPreview();if(state.page==='compare'&&experimentsLoaded&&comparisonWorkspace.leftId&&comparisonWorkspace.rightId&&comparisonWorkspace.status==='idle')loadExperimentComparison();}
  function useSample(index,record=experiments[index]){const e=record,base={...state.draft};delete base.market_assumptions;delete base.preview_reference;state.draft={...base,title:e.title,source:e.source,type:e.type,seed:e.seed,signal:e.signal??(e.kind==='negative'?-.6:e.kind==='uncertain'?0:.6),uncertainty:e.uncertainty??(e.kind==='uncertain'?.8:.2),duration:e.duration??6,cash:e.cash??1000000,sessions:e.sessions??18,published:e.published_at||state.draft.published,strategy_parameters:parametersFromExperiment(e)||(strategyWorkspace.parameters?strategyWorkspace.snapshot():null)};for(const key of ['market_assumptions','preview_reference'])if(Object.hasOwn(e,key))state.draft[key]=JSON.parse(JSON.stringify(e[key]));analysisBinding.reset(e.source);if(e.text_analysis)analysisBinding.finish(analysisBinding.begin(e.source),e.text_analysis);state.wizard=1;navigate('new');}
  async function apiJson(path, options={}) { const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}}); const body=await response.json(); if(!response.ok){const error=new Error(body.error||`API ${response.status}`);error.status=response.status;throw error;} return body; }
  function hasDraftContent(){return Boolean(state.draft.title.trim()||state.draft.source.trim()||draftSubmissions.has(state.draft));}
  function draftCacheMessage(){
    if(draftCache.error)return draftCache.error;
    return draftCache.savedAt?'草稿已在当前标签页保存，刷新后可继续。提交状态以本机记录为准。':'编辑内容会自动保存到当前标签页；已提交实验保存在本机。';
  }
  function persistDraft(){
    const recovery=draftAnalysisRecovery;
    const analysisId=analysisBinding.analysisId||(recovery?.draft===state.draft&&recovery.source===state.draft.source&&(analysisBinding.restoring||analysisBinding.restoreError)?recovery.id:null);
    if(hasDraftContent())draftCache.write({draft:state.draft,wizard:state.wizard,analysisId,analysisPending:analysisBinding.pending,submission:draftSubmissions.get(state.draft)||null});
    const status=document.getElementById('draft-cache-status');if(status){status.textContent=draftCacheMessage();status.classList.toggle('error',Boolean(draftCache.error));}
  }
  function restoreDraftFromCache(){
    const saved=draftCache.read();if(!saved)return;
    state.draft=saved.draft;state.wizard=saved.wizard;analysisBinding.reset(saved.draft.source);
    if(saved.submission)draftSubmissions.set(state.draft,{...saved.submission,draft:state.draft,restored:true,busy:false,status:'unknown',record:null,error:'正在从本机记录确认上次提交。',navigationVersion:-1});
    if(saved.analysis_id)restoreDraftAnalysis(saved.analysis_id);
    else if(saved.analysis_pending)analysisBinding.error='刷新前的提取结果尚未确认；不会自动再次调用模型，可重新提取或手动设置情景。';
  }
  async function restoreDraftAnalysis(id){
    const recovery={draft:state.draft,source:state.draft.source,id};draftAnalysisRecovery=recovery;
    const ticket=analysisBinding.begin(recovery.source);analysisBinding.restoring=true;updateDraftAnalysisPanel();
    try{analysisBinding.finish(ticket,await apiJson(`/api/platform/analyses/${id}`));}
    catch(error){if(analysisBinding.fail(ticket,'上次分析未能恢复：'+error.message)){analysisBinding.restoring=false;analysisBinding.restoreError=true;}}
    finally{if(draftAnalysisRecovery===recovery&&!analysisBinding.restoreError)draftAnalysisRecovery=null;updateDraftAnalysisPanel();}
  }
  function syncRestoredDraftSubmission(){
    const ticket=draftSubmissions.get(state.draft);if(!ticket?.restored||!experimentsLoaded)return;
    const record=experiments.find(e=>e.id===ticket.id&&e.custom&&!e.corrupt);
    if(!record){ticket.record=null;ticket.status='save_failed';ticket.error='尚未读到上次提交；相同草稿重试会沿用原编号。';}
    else if(!MarketDraftCache.matchesRecord(ticket.payload,record)){ticket.record=null;ticket.status='save_failed';ticket.error='已保存记录与上次提交的配置不一致，请核对原记录。';}
    else{ticket.record=record;ticket.status=record.run_status||'not_started';ticket.error='';}
    updateDraftSubmission();
  }
  function updateModelSettings(){
    if(!dialog.open||dialog.dataset.section!=='model')return;
    const panel=document.getElementById('model-settings-panel');if(panel)panel.innerHTML=MarketModelView.render(modelSettings);
  }
  function openModelSettings(){
    modal('模型设置',`<div id="model-settings-panel">${MarketModelView.render(modelSettings)}</div>`,`<button class="btn" type="button" data-action="close">关闭</button>`,'model');
    loadModelConfiguration();
  }
  async function loadModelConfiguration(){
    const ticket=modelSettings.beginLoad();if(ticket===null){updateModelSettings();return;}
    updateModelSettings();
    try{modelSettings.accept(ticket,await apiJson('/api/platform/model'));}
    catch(error){modelSettings.fail(ticket,'配置读取失败：'+error.message);}
    finally{updateModelSettings();}
  }
  async function checkModelConnection(){
    const ticket=modelSettings.beginCheck();if(ticket===null)return;
    updateModelSettings();
    try{modelSettings.accept(ticket,await apiJson('/api/platform/model/check',{method:'POST',body:'{}'}));}
    catch(error){modelSettings.fail(ticket,'检测结果未确认：'+error.message+'。请刷新配置查看服务端状态');}
    finally{updateModelSettings();}
  }
  function caseDetail(){
    const bundle=caseWorkspace.detail;
    if(!bundle)return MarketCaseView.renderDetail(null,caseWorkspace);
    const result=bundle.result,rows=group=>result.paths[group].trace.map((day,i)=>({step:i+1,price:day.portfolio_auction.asset_calls[caseWorkspace.asset].price_after_minor/100,date:day.trade_date}));
    const prices=[...rows('baseline'),...rows('with_message')].map(row=>row.price),bounds={low:Math.min(...prices)-.2,high:Math.max(...prices)+.2};
    return MarketCaseView.renderDetail(bundle,caseWorkspace,{risk:MarketPortfolioMetrics.renderOverview(result,{activeLabel:'所选条件',baselineLabel:'无文本'}),charts:savedPriceChart(rows('with_message'),'所选条件 · 合成市场价格',bounds)+savedPriceChart(rows('baseline'),'无文本 · 合成市场价格',bounds),comparison:MarketDecisionView.renderComparison(result,{asset:caseWorkspace.asset,step:caseWorkspace.step,activeLabel:'所选条件',baselineLabel:'无文本参考'}),decisionCards:marketStepCards(result,{decisionGroup:caseWorkspace.group,asset:caseWorkspace.asset,step:caseWorkspace.step})});
  }
  function casesPage(){
    return heading('REAL SOURCE CASES','真实原文案例','从归档文本、复核事实到三类决策与真实撮合账本',`<button class="btn" data-action="refresh-cases">重新加载案例</button>`)
      +'<div class="callout">真实来源文本 · 合成市场实验。六例已用于开发，模型结果经助手修订；角色、资产暴露和数值幅度均为假设。</div>'
      +`<div id="case-catalog-error" class="form-error" role="alert">${esc(caseCatalogError)}</div><div id="case-library">${MarketCaseView.renderLibrary(caseWorkspace.catalog,caseWorkspace)}</div><div id="case-controls">${caseWorkspace.catalog?.available?MarketCaseView.renderControls(caseWorkspace):''}</div><div id="case-detail">${caseDetail()}</div>`;
  }
  function updateCasePanels(){
    if(state.page!=='cases')return;
    const library=document.getElementById('case-library'),controls=document.getElementById('case-controls'),detail=document.getElementById('case-detail'),error=document.getElementById('case-catalog-error');
    if(library)library.innerHTML=MarketCaseView.renderLibrary(caseWorkspace.catalog,caseWorkspace);
    if(controls)controls.innerHTML=caseWorkspace.catalog?.available?MarketCaseView.renderControls(caseWorkspace):'';
    if(detail)detail.innerHTML=caseDetail();if(error)error.textContent=caseCatalogError;
  }
  async function loadSourceCase(id,seed=caseWorkspace.seed,mode=caseWorkspace.mode){
    const ticket=caseWorkspace.begin(id,seed,mode);updateCasePanels();
    try{caseWorkspace.accept(ticket,await apiJson(`/api/platform/source-cases/${encodeURIComponent(id)}?seed=${seed}&mode=${encodeURIComponent(mode)}`));}
    catch(error){caseWorkspace.fail(ticket,error.message);}
    finally{updateCasePanels();}
  }
  async function hydrateSourceCases(){
    const version=++caseCatalogVersion;caseCatalogLoading=true;caseCatalogError='';
    try{
      const catalog=await apiJson('/api/platform/source-cases');if(version!==caseCatalogVersion)return;
      caseWorkspace.catalog=catalog;
      if(catalog.available){const id=catalog.cases.some(c=>c.case_id===caseWorkspace.selectedId)?caseWorkspace.selectedId:(catalog.cases[1]||catalog.cases[0]).case_id;await loadSourceCase(id);}
      else{caseWorkspace.detail=null;caseWorkspace.selectedId=null;caseWorkspace.loadedCaseId=null;caseWorkspace.loading=false;caseWorkspace.error='';caseWorkspace.evidenceFocus=null;caseWorkspace.generation++;}
    }catch(error){if(version===caseCatalogVersion)caseCatalogError='案例读取失败：'+error.message;}
    finally{if(version===caseCatalogVersion){caseCatalogLoading=false;updateCasePanels();}}
  }
  function observedPage(){
    return heading('OBSERVED RETURN REPLAY','历史行情实验','真实公司回复与历史收益，核验三类策略的输入、行为和账户',`<button class="btn" data-action="refresh-observed">重新核验归档</button>`)
      +'<div class="callout">真实历史行情 · 简化成交回放。三类 Agent 使用预设规则；本页展示已完成实验，模型读取不产生新请求。</div>'
      +`<div id="observed-error" class="form-error" role="alert">${esc(observedCatalogError)}</div><div id="observed-library">${MarketObservedView.renderLibrary(observedWorkspace.catalog,observedWorkspace)}</div><div id="observed-detail">${MarketObservedView.renderDetail(observedWorkspace.detail,observedWorkspace)}</div>`;
  }
  function updateObservedPanels(){
    if(state.page!=='observed')return;
    const library=document.getElementById('observed-library'),detail=document.getElementById('observed-detail'),error=document.getElementById('observed-error');
    if(library)library.innerHTML=MarketObservedView.renderLibrary(observedWorkspace.catalog,observedWorkspace);
    if(detail)detail.innerHTML=MarketObservedView.renderDetail(observedWorkspace.detail,observedWorkspace);
    if(error)error.textContent=observedCatalogError;
  }
  async function loadObservedExperiment(id){
    const ticket=observedWorkspace.begin(id);updateObservedPanels();
    try{observedWorkspace.accept(ticket,await apiJson(`/api/platform/observed-experiments/${encodeURIComponent(id)}`));}
    catch(error){observedWorkspace.fail(ticket,error.message);}
    finally{updateObservedPanels();}
  }
  async function hydrateObservedExperiments(){
    const version=++observedCatalogVersion;observedCatalogLoading=true;observedCatalogError='';
    try{
      const catalog=await apiJson('/api/platform/observed-experiments');if(version!==observedCatalogVersion)return;
      observedWorkspace.catalog=catalog;
      if(catalog.available){const id=catalog.experiments.some(e=>e.id===observedWorkspace.selectedId)?observedWorkspace.selectedId:catalog.experiments[0].id;await loadObservedExperiment(id);}
      else observedWorkspace.clear();
    }catch(error){if(version===observedCatalogVersion)observedCatalogError='历史实验读取失败：'+error.message;}
    finally{if(version===observedCatalogVersion){observedCatalogLoading=false;updateObservedPanels();}}
  }
  function newFromCase(){
    const bundle=caseWorkspace.detail;if(!bundle)return;
    const source=bundle.case.segments.map(s=>`${s.source==='question'?'投资者提问':s.source==='reply'?'公司回复':'原文'}：${s.text}`).join('\n\n');
    analysisBinding.reset(source);batchArchivedExperiment=null;
    state.draft={title:bundle.case.label,source,type:bundle.case.stock_code?'公司问答':'政策消息',published:bundle.case.available_at.slice(0,16),signal:0,uncertainty:.2,duration:6,sessions:18,seed:bundle.seed,cash:1000000,strategy_parameters:strategyWorkspace.parameters?strategyWorkspace.snapshot():null};
    state.wizard=1;navigate('new');toast('已复制真实原文；新实验的情景变量由你设定，重新分析后可关联模型事实。');
  }
  function batchesPage(){
    return heading('SCENARIO COMPARISON','批量对照','固定参数、多种种子，比较三类策略在不同情景下的响应',`<button class="btn" data-nav="agents">查看策略配置</button>`)
      +'<div class="batch-scenario-strip">'+[['中性','0 / 0'],['正向','+0.6 / 0.2'],['负向','−0.6 / 0.2'],['高不确定性','0 / 0.8']].map(([name,value])=>`<div><strong>${name}</strong><span>信号 / 不确定性 ${value}</span></div>`).join('')+'</div>'
      +'<p class="batch-help">四种情景均为人工假设，未使用真实公告，也不调用大模型。每项包含有消息与无消息两条撮合路径。</p>'
      +`<div class="batch-setup-grid">${MarketBatchView.renderForm(batchForm,batchWorkspace.busy,batchFormError)}<section class="panel" id="batch-history">${MarketBatchView.renderHistory(batchWorkspace.rows,batchWorkspace.selectedId,batchListLoading,batchWorkspace.listError)}</section></div>`
      +`<div id="batch-live">${MarketBatchView.renderDetail(batchWorkspace.detail,batchWorkspace)}</div>`;
  }
  function updateBatchPanels(){
    if(state.page!=='batches')return;
    const history=document.getElementById('batch-history'),live=document.getElementById('batch-live');
    if(history)history.innerHTML=MarketBatchView.renderHistory(batchWorkspace.rows,batchWorkspace.selectedId,batchListLoading,batchWorkspace.listError);
    if(live){
      const same=live.dataset.batchId===batchWorkspace.detail?.id;
      const opened=same?Array.from(live.querySelectorAll('[data-batch-disclosure][open]')).map(el=>el.dataset.batchDisclosure):[];
      const active=document.activeElement,focused=same&&live.contains(active)?{action:active.dataset.action,experiment:active.dataset.batchExperiment,href:active.getAttribute('href'),disclosure:active.parentElement?.dataset.batchDisclosure}:null;
      live.innerHTML=MarketBatchView.renderDetail(batchWorkspace.detail,batchWorkspace);
      live.dataset.batchId=batchWorkspace.detail?.id||'';
      live.querySelectorAll('[data-batch-disclosure]').forEach(el=>{el.open=opened.includes(el.dataset.batchDisclosure);});
      if(focused){const target=Array.from(live.querySelectorAll('button,a,summary')).find(el=>focused.action?el.dataset.action===focused.action:focused.experiment?el.dataset.batchExperiment===focused.experiment:focused.href?el.getAttribute('href')===focused.href:focused.disclosure&&el.parentElement?.dataset.batchDisclosure===focused.disclosure);target?.focus({preventScroll:true});}
    }
    const button=document.getElementById('batch-create-button'),error=document.getElementById('batch-form-error');
    if(button){button.disabled=batchWorkspace.busy;button.textContent=batchWorkspace.busy?'正在提交…':'创建并运行批次';}
    if(error)error.textContent=batchFormError;
  }
  function rememberBatch(){try{sessionStorage.setItem('marketmirror:selected-batch',batchWorkspace.selectedId);}catch(_) {}}
  function scheduleBatchPoll(){
    clearTimeout(batchPollTimer);
    if(state.page==='batches'&&batchWorkspace.detail?.status==='running')
      batchPollTimer=setTimeout(()=>loadBatch(batchWorkspace.selectedId,true),batchWorkspace.error?5000:2000);
  }
  async function loadBatch(id,quiet=false){
    if(!id)return;
    clearTimeout(batchPollTimer);
    const ticket=batchWorkspace.begin(id);rememberBatch();if(!quiet)updateBatchPanels();
    try{batchWorkspace.accept(ticket,await apiJson(`/api/platform/batches/${id}`));}
    catch(error){batchWorkspace.fail(ticket,error.message);}
    finally{updateBatchPanels();scheduleBatchPoll();}
  }
  async function hydrateBatches(){
    const version=++batchListVersion;batchListLoading=true;batchWorkspace.listError='';updateBatchPanels();
    try{
      const rows=await apiJson('/api/platform/batches');if(version!==batchListVersion)return;
      batchWorkspace.rows=rows;
      const id=rows.some(r=>r.id===batchWorkspace.selectedId)?batchWorkspace.selectedId:rows[0]?.id;
      if(id)await loadBatch(id);
      else{batchWorkspace.selectedId=null;batchWorkspace.detail=null;batchWorkspace.generation++;}
    }catch(error){if(version===batchListVersion)batchWorkspace.listError='批次列表读取失败：'+error.message;}
    finally{if(version===batchListVersion){batchListLoading=false;updateBatchPanels();scheduleBatchPoll();}}
  }
  async function createAndRunBatch(){
    if(batchWorkspace.busy)return;
    let payload;
    try{payload={title:batchForm.title,seeds:MarketBatchView.parseSeeds(batchForm.seeds),sessions:Number(batchForm.sessions),duration:Number(batchForm.duration),cash:Number(batchForm.cash)};}
    catch(error){batchFormError=error.message;updateBatchPanels();return;}
    batchWorkspace.busy=true;batchFormError='';updateBatchPanels();let created=null;
    try{
      created=await apiJson('/api/platform/batches',{method:'POST',body:JSON.stringify(payload)});
      const ticket=batchWorkspace.begin(created.id);batchWorkspace.accept(ticket,created);rememberBatch();updateBatchPanels();
      const started=await apiJson(`/api/platform/batches/${created.id}/run`,{method:'POST',body:'{}'});
      batchWorkspace.accept(ticket,started);toast('批次已提交，在本机服务中继续运行。');
    }catch(error){batchFormError=created?'批次已保存，请刷新确认状态后重试未完成项。'+error.message:error.message;}
    finally{batchWorkspace.busy=false;await hydrateBatches();updateBatchPanels();}
  }
  async function runBatch(){
    const id=batchWorkspace.selectedId;if(!id||batchWorkspace.busy)return;
    batchWorkspace.busy=true;const ticket=batchWorkspace.begin(id);updateBatchPanels();
    try{batchWorkspace.accept(ticket,await apiJson(`/api/platform/batches/${id}/run`,{method:'POST',body:'{}'}));}
    catch(error){batchWorkspace.fail(ticket,error.message);toast(error.message);try{batchWorkspace.accept(ticket,await apiJson(`/api/platform/batches/${id}`));}catch(_) {}}
    finally{batchWorkspace.busy=false;updateBatchPanels();scheduleBatchPoll();}
  }
  async function openBatchExperiment(batchId,experimentId){
    const version=++batchOpenVersion;
    try{
      const [record,result]=await Promise.all([apiJson(`/api/platform/experiments/${experimentId}`),apiJson(`/api/platform/batches/${batchId}/results/${experimentId}`)]);
      if(version!==batchOpenVersion||state.page!=='batches'||batchWorkspace.selectedId!==batchId)return;
      let index=experiments.findIndex(e=>e.id===experimentId);
      if(index<0){index=experiments.length;experiments.push({...record,custom:true,backendResult:result});}
      batchArchivedExperiment={...record,result_id:result.provenance.result_id,last_run_at:result.generated_at,
        run_status:'completed',run_error:null,run_attempts:result.provenance.run_attempts??null,run_finished_at:null,
        batch_archive:{batch_id:batchId,result_id:result.provenance.result_id},custom:true,backendResult:result};
      state.selected=index;state.step=8;navigate('analysis');
    }catch(error){toast('无法读取归档结果：'+error.message);}
  }
  const activeRuns=new Set();
  function runStatusPanel(e){
    const status=e.run_status||'not_started';
    if(status==='completed'&&e.backendResult)return '';
    const busy=activeRuns.has(e.id)||status==='running'||status==='unknown';
    const label=status==='completed'&&!e.backendResult?'运行记录已完成，但结果文件缺失':status==='unknown'?'运行状态待确认':busy?'实验正在运行':status==='failed'?'上次运行失败':status==='interrupted'?'上次运行已中断':'实验配置已保存，尚未完成运行';
    return `<section class="callout" role="status"><div><strong>${label}</strong><p>${e.backendResult?'下方保留的是上一次成功结果。':'完成运行后可查看撮合结果。'} 已尝试 ${Number(e.run_attempts)||0} 次。</p>${e.run_error?`<p>${esc(e.run_error.message||'请重试或检查本机服务。')}</p>`:''}<button class="btn primary" data-action="retry-run" ${busy?'disabled':''}>${status==='unknown'?'等待状态确认':busy?'运行中…':'运行此实验'}</button><button class="btn" data-action="refresh-runs">刷新状态</button></div></section>`;
  }
  async function retryRun(){
    const e=current();if(e.corrupt||!e.custom||activeRuns.has(e.id)||['running','unknown'].includes(e.run_status))return;
    activeRuns.add(e.id);upsertExperiment({...e,run_status:'running',run_error:null});
    try{const result=await apiJson(`/api/platform/experiments/${e.id}/run`,{method:'POST',body:'{}'});completeExperiment(e,result);toast('实验完成，结果已保存。');}
    catch(error){upsertExperiment({...e,run_status:'unknown',run_error:{message:'请求未完成：'+error.message+'。请刷新确认服务端状态后重试。'}});toast('请刷新确认运行状态。');}
    finally{activeRuns.delete(e.id);await hydrateExperiments();}
  }
  function draftPayload(d=state.draft){
    const payload={title:d.title,source:d.source,type:d.type,published_at:d.published,signal:Number(d.signal),uncertainty:Number(d.uncertainty),duration:Number(d.duration),sessions:Number(d.sessions||18),seed:Number(d.seed),cash:Number(d.cash),analysis_id:analysisBinding.analysisId,strategy_parameters:copyParameters(d.strategy_parameters)};
    for(const key of ['market_assumptions','preview_reference'])if(Object.hasOwn(d,key))payload[key]=JSON.parse(JSON.stringify(d[key]));
    return payload;
  }
  function submissionMatches(ticket){return Boolean(ticket)&&ticket.draft===state.draft&&ticket.signature===JSON.stringify(draftPayload());}
  function draftSubmitControl(){
    if(state.wizard!==3)return `<button class="btn primary" type="button" data-action="next">下一步 ${icon('arrow')}</button>`;
    const ticket=draftSubmissions.get(state.draft),saved=submissionMatches(ticket)&&ticket.record;
    return `<button class="btn primary" type="button" data-action="${saved&&!ticket.busy?'draft-result':'preview-run'}" ${ticket?.busy?'disabled':''}>${ticket?.busy?(ticket.status==='saving'?'正在保存…':'正在运行…'):saved?(ticket.status==='completed'&&ticket.record.backendResult?'查看已保存结果':ticket.status==='running'?'查看运行进度':'查看实验并恢复'):submissionMatches(ticket)&&ticket.status==='save_failed'?'重试保存并运行':'运行市场实验'} ${icon('arrow')}</button>`;
  }
  function draftSubmissionPanel(){
    const ticket=draftSubmissions.get(state.draft);if(!ticket)return '';
    const labels={saving:'正在保存实验',running:'实验正在运行',completed:'实验已完成',unknown:'运行状态待确认',failed:'运行失败',interrupted:'运行已中断',not_started:'配置已保存',save_failed:'保存请求未确认'};
    return `<div class="callout" role="status"><div><strong>${labels[ticket.status]||'实验配置已保存'} · ${esc(ticket.payload.title)}</strong><p>提交时的消息、参数及模型关联已固定。可以切换页面或新建另一份实验，当前编辑不会改动已提交记录。</p>${ticket.record?'<button class="btn compact" type="button" data-action="draft-result">打开已保存实验</button>':''}${ticket.error?`<p>${esc(ticket.error)}</p>`:''}</div></div>`;
  }
  function updateDraftSubmission(){
    persistDraft();
    if(state.page!=='new')return;
    const panel=document.getElementById('draft-submission-panel'),control=document.getElementById('draft-submit-control');
    if(panel)panel.innerHTML=draftSubmissionPanel();if(control)control.innerHTML=draftSubmitControl();
    const error=document.getElementById('form-error'),ticket=draftSubmissions.get(state.draft);
    if(error)error.textContent=submissionMatches(ticket)?ticket.error||'':'';
  }
  function upsertExperiment(record,result){
    const selectedId=experiments[state.selected]?.id,index=experiments.findIndex(e=>e.id===record.id),previous=index>=0?experiments[index]:null;
    const value={...previous,...record,custom:true,kind:Number(record.signal)<0?'negative':Number(record.signal)>0?'positive':'neutral',time:previous?.time||'刚刚',backendResult:result===undefined?previous?.backendResult||record.backendResult||null:result};
    if(index>=0)experiments[index]=value;else experiments.unshift(value);
    const selected=experiments.findIndex(e=>e.id===selectedId);state.selected=selected>=0?selected:0;
    experimentLoadVersion++;experimentLoading=false;experimentsLoaded=true;
    if(['experiments','analysis','sources','compare'].includes(state.page))render();
    return value;
  }
  function completeExperiment(record,result){
    if(!result||result.mode!=='synthetic_market'||result.provenance?.experiment_id!==record.id||!result.provenance.result_id)throw new Error('运行结果与实验记录不一致，请刷新确认');
    for(const key of ['source_sha256','strategy_parameters_sha256','analysis_sha256','market_assumptions_sha256','decision_preview_sha256'])if(record[key]!=null&&result.provenance[key]!==record[key])throw new Error('运行结果依据与提交配置不一致，请刷新确认');
    return upsertExperiment({...record,run_status:'completed',run_error:null,result_id:result.provenance.result_id,run_attempts:result.provenance.run_attempts,last_run_at:result.generated_at},result);
  }
  async function openSubmittedExperiment(){
    const ticket=draftSubmissions.get(state.draft);if(!ticket?.record)return;
    const version=navigationVersion;
    let index=experiments.findIndex(e=>e.id===ticket.record.id);
    if(index<0){await hydrateExperiments();index=experiments.findIndex(e=>e.id===ticket.record.id);}
    if(state.page!=='new'||navigationVersion!==version||draftSubmissions.get(state.draft)!==ticket)return;
    if(index<0){toast('尚未读取到已保存实验，请在实验空间刷新确认。');return;}
    state.selected=index;batchArchivedExperiment=null;navigate('analysis');
  }
  async function persistAndRun(ticket){
    const record=await apiJson('/api/platform/experiments',{method:'POST',headers:{'Idempotency-Key':ticket.id},body:ticket.signature});
    if(record.id!==ticket.id)throw new Error('保存响应与提交编号不一致，请刷新确认本机服务版本');
    ticket.record=upsertExperiment(record);ticket.status=record.run_status;updateDraftSubmission();
    if(record.run_status==='completed'){
      const snapshot=await apiJson(`/api/platform/experiments/${record.id}/export`);
      if(snapshot.experiment?.id!==record.id)throw new Error('实验快照身份不一致');
      if(snapshot.experiment.result_id!==snapshot.experiment.backendResult?.provenance?.result_id)throw new Error('归档结果版本与记录不一致');
      ticket.record=completeExperiment(snapshot.experiment,snapshot.experiment.backendResult);return;
    }
    if(record.run_status==='running')return;
    activeRuns.add(record.id);ticket.status='running';ticket.record=upsertExperiment({...record,run_status:'running',run_error:null});updateDraftSubmission();
    try{
      const result=await apiJson(`/api/platform/experiments/${record.id}/run`,{method:'POST',body:'{}'});
      ticket.record=completeExperiment(record,result);ticket.status='completed';
    }catch(error){ticket.record=upsertExperiment({...record,run_status:'unknown',run_error:{message:'请求未完成，请刷新确认服务端状态。'}});throw error;}
    finally{activeRuns.delete(record.id);}
  }
  async function hydrateExperiments(){
    const version=++experimentLoadVersion;experimentLoading=true;
    if(!experimentsLoaded&&['experiments','analysis','sources'].includes(state.page))render();
    try{
      const rows=await apiJson('/api/platform/experiments');if(version!==experimentLoadVersion)return;
      const settled=[];
      for(let offset=0;offset<rows.length;offset+=4){
        if(version!==experimentLoadVersion)return;
        settled.push(...await Promise.allSettled(rows.slice(offset,offset+4).map(async row=>{
          if(row.run_status==='corrupt')return {...row,custom:true,kind:'neutral',time:'记录损坏',sessions:0,backendResult:null,corrupt:true};
          const snapshot=await apiJson(`/api/platform/experiments/${row.id}/export`),detail=snapshot.experiment;
          if(!detail||detail.id!==row.id)throw new Error('实验快照身份不一致');
          const result=detail.backendResult,provenance=result?.provenance;
          if(result!=null&&(typeof result!=='object'||Array.isArray(result)||!['synthetic_market','platform_preview'].includes(result.mode)))throw new Error('结果结构或版本不受支持');
          if(provenance?.experiment_id&&provenance.experiment_id!==row.id)throw new Error('结果不属于所选实验');
          if(detail.result_id&&provenance?.result_id&&detail.result_id!==provenance.result_id)throw new Error('结果版本与记录不一致');
          for(const key of ['source_sha256','strategy_parameters_sha256','analysis_sha256'])if(provenance?.[key]!=null&&detail[key]!=null&&provenance[key]!==detail[key])throw new Error('结果依据与记录不一致');
          for(const key of ['market_assumptions_sha256','decision_preview_sha256'])if(detail[key]!=null&&provenance?.[key]!==detail[key])throw new Error('结果的市场假设或预览参考与记录不一致');
          const created=detail.created_at?new Date(detail.created_at):null;
          return {...detail,kind:Number(detail.signal)<0?'negative':Number(detail.signal)>0?'positive':'neutral',time:created&&!Number.isNaN(created.getTime())?created.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'本机记录',sessions:Number(detail.sessions)||18,custom:true,backendResult:result||null,customResultMissing:detail.run_status==='completed'&&!result};
        })));
      }
      if(version!==experimentLoadVersion)return;
      const loaded=settled.filter(item=>item.status==='fulfilled').map(item=>item.value),failed=settled.flatMap((item,index)=>item.status==='rejected'?[rows[index].id]:[]);
      const selectedId=rememberedExperimentId||current().id,previous=new Map(experiments.filter(e=>e.custom).map(e=>[e.id,e]));
      const retained=rows.flatMap((row,index)=>settled[index].status==='fulfilled'?[settled[index].value]:previous.has(row.id)?[previous.get(row.id)]:[]),templates=experiments.filter(e=>!e.custom);
      const previousSelected=experiments.find(e=>e.id===selectedId),prefer=previousSelected?.custom||retained.some(e=>e.id===selectedId);
      experiments.splice(0,experiments.length,...templates,...retained);
      const fallback=retained.find(e=>!e.corrupt&&e.backendResult?.mode==='synthetic_market')||retained.find(e=>!e.corrupt)||retained[0]||templates[0];
      const index=prefer?experiments.findIndex(e=>e.id===selectedId):-1;state.selected=index>=0?index:Math.max(0,experiments.indexOf(fallback));
      experimentLoadError=failed.length?`${loaded.length} 条记录已加载，${failed.length} 条读取失败（${failed.join('、')}），请重试`:'';experimentsLoaded=true;
      syncRestoredDraftSubmission();
    }catch(error){if(version===experimentLoadVersion)experimentLoadError=error.message||'服务连接失败';}
    finally{if(version===experimentLoadVersion){experimentLoading=false;if(typeof document!=='undefined'){const count=document.querySelector?.('.nav-count');if(count)count.textContent=experiments.filter(e=>e.custom).length;}if(['experiments','analysis','sources','compare'].includes(state.page))render();}}
  }
  async function previewRun(){
    if(state.page!=='new'||state.wizard!==3)return;
    saveDraft();
    const previous=draftSubmissions.get(state.draft);if(previous?.busy)return;
    if(submissionMatches(previous)&&previous.record){await openSubmittedExperiment();return;}
    if(!state.draft.strategy_parameters){toast('请先加载策略参数再运行。');return;}
    if(analysisBinding.pending){toast('文本分析仍在进行，可等待完成或取消关联后运行。');return;}
    if(analysisBinding.restoreError){toast('请先重新读取上次分析，或取消关联后继续手动实验。');return;}
    if(!document.getElementById('confirm-demo')?.checked){const error=document.getElementById('form-error');if(error)error.textContent='请先确认实验说明。';return;}
    const payload=draftPayload(),signature=JSON.stringify(payload),ticket={draft:state.draft,payload,signature,id:submissionMatches(previous)?previous.id:crypto.randomUUID().replace(/-/g,''),navigationVersion,busy:true,status:'saving',record:null,error:''};
    draftSubmissions.set(state.draft,ticket);updateDraftSubmission();
    try{
      await persistAndRun(ticket);
      if(ticket.status==='completed'&&state.page==='new'&&state.wizard===3&&navigationVersion===ticket.navigationVersion&&submissionMatches(ticket))await openSubmittedExperiment();
      toast(ticket.status==='completed'?`实验「${ticket.payload.title}」已完成，撮合账本已审计并保存。`:'实验已保存，正在运行；可打开记录查看状态。');
    }catch(error){
      ticket.status=ticket.record?'unknown':'save_failed';ticket.error='请求未完成：'+error.message;
      await hydrateExperiments();
      const saved=experiments.find(e=>e.id===ticket.id&&e.custom&&!e.corrupt);
      if(saved){ticket.record=saved;ticket.status=saved.run_status||'unknown';}
      if(ticket.status==='completed'&&ticket.record.backendResult){ticket.error='';toast(`实验「${ticket.payload.title}」的结果已在本机确认保存。`);}
      else{ticket.error+=ticket.record?'。请打开已保存实验，确认状态后恢复运行。':'。重试将使用同一提交编号，不会重复建立相同实验。';toast(ticket.record?'已保存的配置仍保留，请打开实验确认状态。':'保存状态待确认，可重试同一提交。');}
    }finally{ticket.busy=false;updateDraftSubmission();}
  }
  function savedPriceChart(rows,title='情景价格路径',bounds=null){
    if(!rows.length)return '';
    const prices=rows.map(r=>Number(r.price)),low=bounds?.low??Math.min(...prices)-.2,high=bounds?.high??Math.max(...prices)+.2;
    const x=i=>50+i*700/Math.max(1,rows.length-1),y=p=>210-(p-low)/(high-low)*180;
    const path=prices.map((p,i)=>`${i?'L':'M'}${x(i)},${y(p)}`).join(' ');
    return `<section class="panel"><div class="panel-header"><h2>${esc(title)}</h2><span class="badge neutral">已保存数据</span></div><svg viewBox="0 0 800 250" style="width:100%;max-height:320px" role="img" aria-label="${esc(title)}：已保存实验的逐步价格曲线"><path d="M50 30V210H750" fill="none" stroke="#aab7c4"/><path d="${path}" fill="none" stroke="var(--teal)" stroke-width="3"/>${prices.map((p,i)=>`<circle cx="${x(i)}" cy="${y(p)}" r="3" fill="var(--teal)"><title>${rows[i].date?esc(rows[i].date)+' · ':''}第 ${i+1} 步：${p}</title></circle>`).join('')}<text x="50" y="237" fill="currentColor">第 1 步</text><text x="680" y="237" fill="currentColor">第 ${rows.length} 步</text><text x="6" y="35" fill="currentColor">${high.toFixed(1)}</text><text x="6" y="210" fill="currentColor">${low.toFixed(1)}</text></svg></section>`;
  }
  function marketStepCards(result=current().backendResult,viewState=state){
    const view=MarketDecisionView.buildStep(result,viewState.decisionGroup,viewState.asset,viewState.step);
    const value=(n,d=2)=>typeof n==='number'&&Number.isFinite(n)?n.toFixed(d):'未存档';
    const pct=n=>n==null?'未存档':value(n*100)+'%';
    const qty=n=>n==null?'未存档':format(n);
    const contribution=n=>n==null?'未存档':(n>=0?'+':'')+value(n,4);
    return view.roles.map(data=>{
      const role=roles.find(r=>r.key===data.role);
      if(!data.accounts.length)return `<article class="panel decision-card"><h2>${role.name}</h2><p>此步没有可读取的账户决策。</p></article>`;
      const action=data.orders===null?'订单未存档':data.buys&&data.sells?'双向下单':data.buys?'买入':data.sells?'卖出':'未下单';
      const terms=data.terms;
      const constraints=data.constraints.length?data.constraints.map(c=>`<li><span>${esc(c.label)}</span><small>${c.accounts}/${data.accounts.length} 个账户</small></li>`).join(''):'<li><span>未记录额外约束触发</span></li>';
      return `<article class="panel decision-card" data-decision-role="${role.key}" style="--role:${role.color}">
        <div class="decision-card-head"><span class="agent-mark">${icon(role.icon)}</span><div><h2>${role.name}</h2><small>资产 ${view.asset} · ${data.accounts.length} 个账户</small></div><span class="decision-action">${action}</span></div>
        <div class="decision-stage"><span class="decision-stage-label">01 · 收到的信息</span><dl class="decision-pairs"><div><dt>文本信号</dt><dd>${value(view.observation?.text_signal,4)}</dd></div><div><dt>文本不确定性</dt><dd>${value(view.observation?.text_uncertainty,4)}</dd></div></dl></div>
        <div class="decision-stage"><span class="decision-stage-label">02 · 综合判断</span><div class="decision-score">${contribution(data.belief)}<small>判断分值</small></div>${terms?`<dl class="decision-terms"><div><dt>市场因素</dt><dd>${contribution(terms.market)}</dd></div><div><dt>文本贡献</dt><dd>${contribution(terms.text)}</dd></div><div><dt>不确定性扣减</dt><dd>${contribution(terms.uncertainty)}</dd></div></dl>`:'<p class="decision-note">未能从此版本账本核对分项贡献，仅显示已存档分值。</p>'}</div>
        <div class="decision-stage"><span class="decision-stage-label">03 · 仓位与约束</span><div class="decision-weights"><div><small>决策前</small><strong>${pct(data.before)}</strong></div><span aria-hidden="true">→</span><div><small>目标</small><strong>${pct(data.target)}</strong></div></div><p class="decision-note">拟下单仓位变化 ${data.orderChange==null?'未存档':contribution(data.orderChange*100)+' 个百分点'}</p><ul class="decision-constraints">${constraints}</ul><p class="decision-note">约束记录针对整个组合，非仅当前资产。</p></div>
        <div class="decision-stage"><span class="decision-stage-label">04 · 实际执行</span><p class="decision-note">买入请求 ${qty(data.buys)} 股 · 卖出请求 ${qty(data.sells)} 股</p><div class="decision-execution"><div><small>请求</small><strong>${qty(data.requested)}</strong></div><div><small>接受</small><strong>${qty(data.accepted)}</strong></div><div><small>成交</small><strong>${qty(data.filled)}</strong></div></div></div>
        <details class="decision-detail"><summary>查看账户与订单依据</summary><p class="decision-note">数值按决策前账户净资产加权。分值为策略计算量，不是收益预测；成交股数来自撮合账本。</p><div class="table-wrap"><table><thead><tr><th>账户</th><th>分值</th><th>文本贡献</th><th>不确定性扣减</th><th>敏感度</th><th>基础权重</th><th>风险预算</th></tr></thead><tbody>${data.accounts.map(a=>`<tr><td>${esc(a.name)}</td><td>${value(a.belief,4)}</td><td>${a.terms?contribution(a.terms.text):'未核对'}</td><td>${a.terms?contribution(a.terms.uncertainty):'未核对'}</td><td>${value(a.parameters.text_sensitivity)}</td><td>${pct(a.parameters.base_weight)}</td><td>${pct(a.parameters.risk_budget)}</td></tr>`).join('')}</tbody></table></div><p class="decision-note">参与计算的常规规则：${data.rules.map(r=>esc(r.label)).join(' · ')||'未存档'}。此处不表示这些上限均已触发。</p>${data.orders===null?'<p>订单账本未存档。</p>':data.orders.length?data.orders.map(o=>`<div class="decision-order"><strong>${esc(o.owner)} · ${o.side==='buy'?'买入':'卖出'}</strong><p>限价 ${value(o.limit_price_minor/100)} · 请求 ${qty(o.quantity)} → 接受 ${qty(o.accepted_quantity)} → 成交 ${qty(o.filled_quantity)} 股</p><p>${(o.reasons||[]).map(code=>esc(MarketDecisionView.describeOrder(code))).join(' · ')||'未记录接受量裁减或未成交原因'}</p></div>`).join(''):'<p class="decision-note">当前资产本步未提交订单。拟下单仓位变化还需按交易单位取整。</p>'}</details>
      </article>`;
    }).join('');
  }
  function marketAnalysis(){
    const e=current(),r=e.backendResult,active=r.paths.with_message,base=r.paths.baseline;
    state.step=Math.max(1,Math.min(state.step,active.trace.length));
    const priceRows=path=>path.trace.map((d,i)=>({step:i+1,price:d.portfolio_auction.asset_calls[state.asset].price_after_minor/100}));
    const allPrices=[...priceRows(active),...priceRows(base)].map(row=>row.price);
    const bounds={low:Math.min(...allPrices)-.2,high:Math.max(...allPrices)+.2};
    return heading('MARKET EXPERIMENT',esc(e.title),'<span class="badge">合成市场 · 撮合计算</span>',`${batchArchivedExperiment?`<button class="btn" data-action="export">${icon('download')}导出当前快照</button>`:`<a class="btn" href="/api/platform/experiments/${e.id}/export" download>${icon('download')}导出账本</a>`}<button class="btn" data-action="compare-current">加入实验比较</button><button class="btn" data-action="strategy-variant">调整策略再跑</button><button class="btn" data-action="duplicate">复制为新实验</button>${newButton()}`)
      +`<div class="callout">账本审计通过：${r.audit.days_checked} 个市场日。消息从第 5 步作用于${r.market_assumptions?.scope==='public'?'全部三资产':'资产 A'}；信号与暴露为人工情景假设，${e.text_analysis?'已关联模型事实作为参考':'未关联模型分析'}。</div>`
      +`<nav class="result-shortcuts" aria-label="结果页快捷入口"><button class="btn" type="button" data-action="compare-decisions">查看同一步决策对照 ${icon('arrow')}</button><span>三类策略 · 有消息与无消息</span></nav>`
      +MarketPortfolioMetrics.renderOverview(r)
      +`<section class="panel"><div class="panel-header"><h2>实验条件</h2></div><div class="summary-content"><p>${esc(e.source)}</p><p>信号 ${e.signal} · 不确定性 ${e.uncertainty} · 持续 ${e.duration} 步 · 种子 ${e.seed}</p>${MarketExperimentScenario.renderSummary(e)}${MarketExperimentScenario.renderReference(e)}<p>每类 4 个账户，每类初始现金合计 ${format(r.assumptions.effective_initial_cash_per_role)} 元，另有每账户每资产 500 股初始库存。</p></div></section>`
      +experimentStrategyPanel(e)+(typeof experimentModelBoundary==='function'?experimentModelBoundary(e):'')+experimentEvidence(e.text_analysis)
      +`<div class="section-title"><h2>市场价格对照 <small>两组使用相同坐标刻度</small></h2><div class="segmented">${['A','B','C'].map(a=>`<button data-asset="${a}" class="${state.asset===a?'active':''}">资产 ${a}</button>`).join('')}</div></div>`
      +'<div class="market-chart-grid">'+savedPriceChart(priceRows(active),'有消息',bounds)+savedPriceChart(priceRows(base),'无消息对照',bounds)+'</div>'
      +`<section class="panel timeline-control" id="market-decision-controls" tabindex="-1" aria-label="决策步与信息条件">${MarketReplayControl.render({id:'market-step',step:state.step,title:`逐步查看策略与成交 · 资产 ${state.asset}`,label:'选择市场决策步',context:'合成情景 · 消息作用区间为实验假设',rows:active.trace.map((d,i)=>({visible:i>=4,active:i>=4&&i<4+e.duration}))})}<div class="segmented" role="group" aria-label="决策信息条件">${[['with_message','有消息组'],['baseline','无消息组']].map(([key,label])=>`<button type="button" data-decision-group="${key}" class="${state.decisionGroup===key?'active':''}" aria-pressed="${state.decisionGroup===key}">${label}</button>`).join('')}</div><p class="replay-context">${active.trace.length<5?'本实验在消息进入前结束，两组均未收到文本输入。':'假设第 5 步消息进入。'}当前查看${state.decisionGroup==='baseline'?'无消息对照组':'有消息组'}的实验账本；切换时保留资产与决策步。</p></section><div id="market-comparison">${MarketDecisionView.renderComparison(r,{asset:state.asset,step:state.step})}</div><div class="role-editor-grid" id="market-step-cards">${marketStepCards()}</div>`
      +`<section class="panel"><div class="panel-header"><h2>策略订单 · 全程合计</h2></div><div class="table-wrap"><table><thead><tr><th>信息条件</th><th>请求股数</th><th>接受股数</th><th>成交股数</th></tr></thead><tbody>${[['有消息',active],['无消息',base]].map(([name,path])=>`<tr><td>${name}</td><td>${format(path.summary.strategy_requested)}</td><td>${format(path.summary.strategy_accepted)}</td><td>${format(path.summary.strategy_filled)}</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  function savedAnalysis(){
    if(current().backendResult?.mode==='synthetic_market')return marketAnalysis();
    const e=current(),result=e.backendResult;
    if(!result)return heading('SAVED EXPERIMENT',esc(e.title),'已保存配置')+`<section class="panel"><div class="summary-content"><p>${esc(e.source)}</p><p>信号 ${esc(e.signal)} · 不确定性 ${esc(e.uncertainty)} · 持续 ${esc(e.duration)} 步 · 种子 ${esc(e.seed)}</p></div></section>`;
    return heading('SAVED EXPERIMENT',esc(e.title),'<span class="badge neutral">本机平台预览</span>',`<button class="btn" data-action="export">${icon('download')}导出记录</button>${newButton()}`)
      +'<div class="callout">该记录来自本机预览计算；价格为情景公式输出，成交量为演示值。尚未接入市场撮合引擎或大模型。</div>'
      +`<section class="panel"><div class="panel-header"><h2>消息与实验配置</h2></div><div class="summary-content"><p>${esc(e.source)}</p><p>信号 ${esc(e.signal)} · 不确定性 ${esc(e.uncertainty)} · 持续 ${esc(e.duration)} 步 · 种子 ${esc(e.seed)}</p></div></section>`
      +(result?savedPriceChart(result.sessions):'')
      +(result?`<section class="panel"><div class="panel-header"><h2>三类策略 · 期末预览</h2></div><div class="table-wrap"><table><thead><tr><th>策略</th><th>方向</th><th>目标仓位</th><th>请求股数</th><th>演示成交股数</th></tr></thead><tbody>${result.roles.map(r=>`<tr><td>${esc(r.name)}</td><td>${esc(r.action)}</td><td>${esc(r.target_weight)}%</td><td>${esc(r.requested_shares)}</td><td>${esc(r.filled_shares)}</td></tr>`).join('')}</tbody></table></div></section><section class="panel"><div class="panel-header"><h2>逐步价格记录</h2></div><div class="table-wrap"><table><thead><tr><th>决策步</th><th>情景价格</th><th>消息可见</th><th>作用期内</th></tr></thead><tbody>${result.sessions.map(r=>`<tr><td>${esc(r.step)}</td><td>${esc(r.price)}</td><td>${r.message_visible?'是':'否'}</td><td>${r.message_active?'是':'否'}</td></tr>`).join('')}</tbody></table></div></section>`:'<div class="callout">配置已保存，尚无可读取的运行结果。</div>');
  }
  document.addEventListener('click',async e=>{
    const riskStep=e.target.closest('[data-risk-step]');if(riskStep){jumpToRiskStep(Number(riskStep.dataset.riskStep),riskStep.dataset.riskGroup);return;}
    const comparisonAction=e.target.closest('[data-comparison-action]');if(comparisonAction){
      const action=comparisonAction.dataset.comparisonAction;
      if(action==='swap'){comparisonWorkspace.swap();rememberComparison();render();}
      if(action==='refresh'){comparisonWorkspace.invalidate();render();}
      if(action==='variant'&&comparisonWorkspace.status==='ready')createStrategyVariant(comparisonWorkspace.left);
      if(action==='export')await openComparisonDownloads();return;
    }
    const comparisonOpen=e.target.closest('[data-comparison-open]');if(comparisonOpen){openComparisonSnapshot(comparisonOpen.dataset.comparisonOpen);return;}
    const comparisonGroup=e.target.closest('[data-comparison-group]');if(comparisonGroup){comparisonWorkspace.group=comparisonGroup.dataset.comparisonGroup;document.getElementById('experiment-comparison-body').innerHTML=MarketExperimentComparison.renderBody(comparisonWorkspace);return;}
    const comparisonAsset=e.target.closest('[data-comparison-asset]');if(comparisonAsset){comparisonWorkspace.asset=comparisonAsset.dataset.comparisonAsset;document.getElementById('experiment-comparison-body').innerHTML=MarketExperimentComparison.renderBody(comparisonWorkspace);return;}
    const preset=e.target.closest('[data-preview-preset]');if(preset){strategyPreview.preset(preset.dataset.previewPreset);document.getElementById('strategy-preview-controls').innerHTML=MarketStrategyPreview.renderControls(strategyPreview);scheduleStrategyPreview();return;}
    if(e.target.closest('[data-preview-retry]')){scheduleStrategyPreview(document.querySelector('[data-parameter-number][data-strategy-scope="workspace"][aria-invalid="true"]')?'请先修正策略参数，当前预览已失效':'');return;}
    if(e.target.closest('[data-preview-experiment]')){
      if(!strategyInputValid('workspace'))return;
      const config=MarketExperimentScenario.fromPreview(strategyPreview);if(!config){toast('请等当前参数计算完成后再创建实验。');return;}
      state.draft={...state.draft,...config,title:state.draft.title||'三类策略 · 预览情景实验'};
      batchArchivedExperiment=null;state.wizard=state.draft.source.trim().length>=20?2:1;
      persistDraft();navigate('new');toast('已带入当前情景与策略。草稿原文保留，预览观察状态将作为参考保存。');return;
    }
    const nav=e.target.closest('[data-nav]');if(nav){e.preventDefault();navigate(nav.dataset.nav);return;}
    const observedSelect=e.target.closest('[data-observed-select]');if(observedSelect){await loadObservedExperiment(observedSelect.dataset.observedSelect);return;}
    const observedCondition=e.target.closest('[data-observed-condition]');if(observedCondition&&observedWorkspace.detail){observedWorkspace.condition=observedCondition.dataset.observedCondition;updateObservedPanels();return;}
    const observedChart=e.target.closest('[data-observed-chart]');if(observedChart){observedWorkspace.chart=observedChart.dataset.observedChart;updateObservedPanels();return;}
    const observedFacts=e.target.closest('[data-observed-facts]');if(observedFacts){observedWorkspace.factView=observedFacts.dataset.observedFacts;updateObservedPanels();return;}
    const observedQuote=e.target.closest('[data-observed-fact],[data-observed-receipt]');if(observedQuote&&observedWorkspace.detail){const b=observedWorkspace.detail,part=Number(observedQuote.dataset.observedPart),receipt=observedQuote.dataset.observedReceipt,facts=observedWorkspace.factView==='raw'?b.model.normalized.facts:b.review.reviewed_record.facts;const quote=receipt?b.explanations[observedWorkspace.condition][observedWorkspace.step-1].agents[receipt]?.text_evidence_used[part]:facts[Number(observedQuote.dataset.observedFact)]?.evidence[part];if(quote){observedWorkspace.evidenceFocus=quote;document.getElementById('observed-source').outerHTML=MarketObservedView.renderSource(b,quote);document.getElementById('observed-source-mark')?.scrollIntoView({block:'center',behavior:'smooth'});}return;}
    const caseSelect=e.target.closest('[data-case-select]');if(caseSelect){await loadSourceCase(caseSelect.dataset.caseSelect);return;}
    const caseAsset=e.target.closest('[data-case-asset]');if(caseAsset){caseWorkspace.asset=caseAsset.dataset.caseAsset;updateCasePanels();return;}
    const caseGroup=e.target.closest('[data-case-group]');if(caseGroup){caseWorkspace.group=caseGroup.dataset.caseGroup;updateCasePanels();return;}
    const caseFacts=e.target.closest('[data-case-facts]');if(caseFacts){caseWorkspace.factView=caseFacts.dataset.caseFacts;if(caseWorkspace.detail)document.getElementById('case-facts').innerHTML=MarketCaseView.renderFacts(caseWorkspace.detail,caseWorkspace);return;}
    const caseQuote=e.target.closest('[data-case-quote]');if(caseQuote&&caseWorkspace.detail){const bundle=caseWorkspace.detail,facts=caseQuote.dataset.caseQuoteVersion==='raw'?bundle.review.raw_facts:bundle.review.reviewed_record.facts,quote=facts[Number(caseQuote.dataset.caseQuote)]?.evidence[Number(caseQuote.dataset.caseQuotePart)];if(quote){caseWorkspace.evidenceFocus=quote;document.getElementById('case-source-panel').innerHTML=MarketCaseView.renderSource(bundle.case,quote);document.getElementById('case-evidence-mark')?.scrollIntoView({block:'center',behavior:'smooth'});}return;}
    const batchSelect=e.target.closest('[data-batch-select]');if(batchSelect){await loadBatch(batchSelect.dataset.batchSelect);return;}
    const batchExperiment=e.target.closest('[data-batch-experiment]');if(batchExperiment){await openBatchExperiment(batchExperiment.dataset.batchId,batchExperiment.dataset.batchExperiment);return;}
    const experimentView=e.target.closest('[data-experiment-view]');if(experimentView){state.experimentView=experimentView.dataset.experimentView;state.search='';render();return;}
    const open=e.target.closest('[data-open]');if(open){batchArchivedExperiment=null;state.selected=Number(open.dataset.open);state.step=8;navigate('analysis');return;}
    const sample=e.target.closest('[data-sample],[data-use]');if(sample){saveDraft();useSample(Number(sample.dataset.sample??sample.dataset.use));return;}
    const group=e.target.closest('[data-decision-group]');if(group){state.decisionGroup=group.dataset.decisionGroup;render();return;}
    const asset=e.target.closest('[data-asset]');if(asset){state.asset=asset.dataset.asset;render();return;}
    const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action;
    if(action==='export-risk-overview'){openRiskOverviewDownloads();return;}
    if(action==='compare-decisions'){const controls=document.getElementById('market-decision-controls');controls?.scrollIntoView({block:'start',behavior:'smooth'});controls?.focus({preventScroll:true});return;}
    if(action==='compare-current'){includeInComparison(current());return;}
    if(action==='strategy-variant'){createStrategyVariant(current());return;}
    if(action==='back-to-comparison'){batchArchivedExperiment=null;navigate('compare');return;}
    if(action==='refresh-cases'){await hydrateSourceCases();return;}
    if(action==='refresh-observed'){await hydrateObservedExperiments();return;}
    if(action==='refresh-case'){if(caseWorkspace.selectedId)await loadSourceCase(caseWorkspace.selectedId);return;}
    if(action==='new-from-case'){newFromCase();return;}
    if(action==='refresh-batches'){await hydrateBatches();return;}
    if(action==='run-batch'){await runBatch();return;}
    if(action==='back-to-batch'){batchArchivedExperiment=null;navigate('batches');return;}
    if(action==='retry-run'){await retryRun();return;}
    if(action==='draft-result'){await openSubmittedExperiment();return;}
    if(action==='refresh-runs'){await hydrateExperiments();return;}
    if(action==='refresh-model'){await loadModelConfiguration();return;}
    if(action==='check-model'){await checkModelConnection();return;}
    if(action==='resume-draft'){navigate('new');return;}
    if(action==='retry-restore-analysis'){if(draftAnalysisRecovery?.draft===state.draft)await restoreDraftAnalysis(draftAnalysisRecovery.id);return;}
    if(action==='analyze-source'){
      saveDraft();const source=state.draft.source;
      if(source.trim().length<20){toast('请先填写至少20字的消息原文。');return;}
      const ticket=analysisBinding.begin(source);updateDraftAnalysisPanel();
      try{
        const result=await apiJson('/api/platform/analyze',{method:'POST',body:JSON.stringify({source})});
        if(analysisBinding.finish(ticket,result))toast('事实提取完成，将随实验保存。');
      }catch(error){analysisBinding.fail(ticket,error.message);}
      finally{updateDraftAnalysisPanel();}return;
    }
    if(action==='detach-analysis'){analysisBinding.reset(state.draft.source);updateDraftAnalysisPanel();return;}

    if(action==='new'){draftCache.clear();draftAnalysisRecovery=null;analysisBinding.reset('');state.draft={title:'',source:'',type:'政策消息',published:state.draft.published,signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000,strategy_parameters:strategyWorkspace.parameters?strategyWorkspace.snapshot():null};state.wizard=1;navigate('new');}
    if(action==='close')dialog.close();
    if(action==='menu')document.querySelector('.sidebar').classList.toggle('open');
    if(action==='theme'){document.body.dataset.theme=document.body.dataset.theme==='dark'?'light':'dark';}
    if(action==='cancel-new')navigate('experiments');
    if(action==='duplicate')useSample(state.selected,current());
    if(action==='source')modal('消息原文',`<span class="badge neutral">${esc(current().type)} · 虚构展示文本</span><p class="quote">${esc(current().source)}</p><p>正式版在此保留来源链接、发布时间、文本版本及提取证据。当前未绑定真实来源。</p>`, `<button class="btn" type="button" data-action="close">关闭</button><button class="btn primary" type="button" data-action="source-new">用此消息新建</button>`);
    if(action==='source-new'){dialog.close();useSample(state.selected,current());}
    if(action==='help')modal('MarketMirror 使用说明',`<div class="eyebrow">MARKETMIRROR · RESEARCH STUDIO</div><p>这是本机运行的金融仿真实验平台。输入消息、设置情景变量，比较激进型、保守型和机构型三类预设规则的仓位、订单与成交。</p><div class="review-box"><strong>实验与对照</strong><p>实验空间显示本机记录；示例模板复用后才会生成结果。批量对照比较四种人工情景与多个种子；真实原文案例读取六个已归档的开发实验。</p></div><p>新建实验可提取 DeepSeek 事实并运行合成市场撮合，配置与账本保存在本机。运行时可以切换页面或新建另一份草稿，结果会继续保存。原文事实可以核对；情景强度为明确假设，结果不代表真实市场预测。</p>`);
    if(action==='settings'){openModelSettings();return;}
    if(action==='export'){openRecordDownloads(current());return;}
    if(action==='save-roles')saveStrategies();
    if(action==='retry-strategies'){hydrateStrategies();render();}
    if(action==='reset-roles'){strategyWorkspace.restoreDefaults();strategyPreview.invalidate();render();toast('已载入平台默认值，保存后生效。');}
    if(action==='next'){if(state.wizard===2&&!strategyInputValid('experiment'))return;saveDraft();const d=state.draft,error=document.getElementById('form-error');if(state.wizard===1&&(!d.title.trim()||d.source.trim().length<20||!d.published)){error.textContent='请填写实验名称、发布时间，以及至少 20 个字的消息原文。';return;}if(state.wizard===2&&(!Number.isFinite(d.signal)||d.signal< -1||d.signal>1||!Number.isFinite(d.uncertainty)||d.uncertainty<0||d.uncertainty>1||!MarketExperimentScenario.validate(MarketExperimentScenario.resolve(d)))){error.textContent='请填写完整的情景变量与市场假设，并检查允许范围。';return;}if(state.wizard===2&&(!Number.isInteger(d.sessions)||d.sessions<1||d.sessions>60)){error.textContent='实验总步数须为 1–60 的整数。';return;}if(state.wizard===2&&(!Number.isInteger(d.seed)||d.seed<0||d.seed>999999||!Number.isFinite(d.cash)||d.cash<10000||d.cash>100000000)){error.textContent='种子须为 0–999999 的整数；初始资金须在 1万–1亿 模型元之间。';return;}state.wizard++;render();}
    if(action==='previous'){saveDraft();state.wizard--;render();}
    if(action==='preview-run')previewRun();
  });
  document.addEventListener('submit',e=>{if(e.target.id==='batch-create-form'){e.preventDefault();createAndRunBatch();}});
  function updateDraftField(e){
    if(state.page!=='new')return;
    const el=e.target;
    if(el.dataset.signalSlider||el.dataset.scenarioNumber){const key=el.dataset.signalSlider??el.dataset.scenarioNumber;state.draft[key]=MarketExperimentScenario.syncSignalInput(el,document);updateDraftSubmission();}
    else if(el.dataset.draft){state.draft[el.dataset.draft]=['signal','uncertainty','duration','sessions','seed','cash'].includes(el.dataset.draft)?(el.value.trim()?Number(el.value):NaN):el.value;if(el.dataset.draft==='source')analysisBinding.setSource(el.value);updateDraftSubmission();}
    if(el.dataset.assumption){
      const key=el.dataset.assumption;state.draft.market_assumptions={...MarketExperimentScenario.resolve(state.draft),[key]:key==='scope'?el.value:el.value.trim()?Number(el.value)/(key==='volatility'?100:1):NaN};
      el.setAttribute('aria-invalid',MarketExperimentScenario.validate(state.draft.market_assumptions)?'false':'true');updateDraftSubmission();
    }
    if(el.id==='confirm-demo')draftConfirmations.set(state.draft,el.checked);
  }
  document.addEventListener('input',updateDraftField);
  document.addEventListener('input',e=>{if(!e.target.dataset.previewField)return;const error=strategyPreview.edit(e.target.dataset.previewField,e.target.value);e.target.setAttribute('aria-invalid',error?'true':'false');scheduleStrategyPreview(document.querySelector('[data-parameter-number][data-strategy-scope="workspace"][aria-invalid="true"]')?'请先修正策略参数，当前预览已失效':'');});
  document.addEventListener('change',updateDraftField);
  document.addEventListener('change',e=>{
    if(e.target.dataset.comparisonSelect){comparisonWorkspace.select(e.target.dataset.comparisonSelect,e.target.value);rememberComparison();render();return;}
    updateComparisonStep(e);
  });
  function updateComparisonStep(e){
    if(e.target.dataset.comparisonStep===undefined||comparisonWorkspace.status!=='ready')return;
    const max=Math.min(comparisonWorkspace.left.sessions,comparisonWorkspace.right.sessions),value=e.target.value.trim()?Number(e.target.value):NaN;
    const error=document.getElementById('comparison-step-error');
    if(!Number.isInteger(value)||value<1||value>max){e.target.setAttribute('aria-invalid','true');error.textContent=`请输入 1–${max} 的整数；当前对照仍为第 ${comparisonWorkspace.step} 步。`;return;}
    e.target.setAttribute('aria-invalid','false');error.textContent='';comparisonWorkspace.step=value;
    document.querySelector('.comparison-step-panel h2').textContent=`同一步目标仓位 · 第 ${value} 步`;
    document.querySelector('[data-comparison-step-cards]').innerHTML=MarketExperimentComparison.renderStepCards(comparisonWorkspace);
  }
  document.addEventListener('input',updateComparisonStep);
  document.addEventListener('input',e=>{if(e.target.id==='observed-step'&&observedWorkspace.detail){observedWorkspace.step=Number(e.target.value);const b=observedWorkspace.detail;document.getElementById('observed-step-label').textContent=`第 ${observedWorkspace.step} / ${b.sessions} 日`;document.getElementById('observed-clock').innerHTML=MarketObservedView.renderClock(b,observedWorkspace);document.getElementById('observed-decisions').innerHTML=MarketObservedView.renderDecisions(b,observedWorkspace);document.querySelector('.observed-chart-panel').outerHTML=MarketObservedView.renderCharts(b,observedWorkspace);}});
  document.addEventListener('input',e=>{if(e.target.dataset.batchField)batchForm[e.target.dataset.batchField]=e.target.value;});
  document.addEventListener('change',e=>{if(e.target.dataset.batchField)batchForm[e.target.dataset.batchField]=e.target.value;});
  document.addEventListener('change',e=>{if(e.target.id==='case-seed'||e.target.id==='case-mode')loadSourceCase(caseWorkspace.selectedId,e.target.id==='case-seed'?Number(e.target.value):caseWorkspace.seed,e.target.id==='case-mode'?e.target.value:caseWorkspace.mode);});
  document.addEventListener('input',e=>{if(e.target.id==='case-step'&&caseWorkspace.detail){caseWorkspace.step=Number(e.target.value);document.getElementById('case-step-label').textContent=`第 ${caseWorkspace.step} / 18 步`;document.getElementById('case-observation').innerHTML=MarketCaseView.renderClock(caseWorkspace.detail,caseWorkspace);document.getElementById('case-decisions').innerHTML=marketStepCards(caseWorkspace.detail.result,{decisionGroup:caseWorkspace.group,asset:caseWorkspace.asset,step:caseWorkspace.step});document.getElementById('case-comparison').innerHTML=MarketDecisionView.renderComparison(caseWorkspace.detail.result,{asset:caseWorkspace.asset,step:caseWorkspace.step,activeLabel:'所选条件',baselineLabel:'无文本参考'});}});
  document.addEventListener('input',e=>{if(e.target.id==='draft-source'){state.draft.source=e.target.value;analysisBinding.setSource(e.target.value);updateDraftAnalysisPanel();}if(e.target.id==='market-step'){state.step=Number(e.target.value);document.getElementById('market-step-label').textContent=`第 ${state.step} / ${current().backendResult.paths.with_message.trace.length} 步`;document.getElementById('market-step-cards').innerHTML=marketStepCards();document.getElementById('market-comparison').innerHTML=MarketDecisionView.renderComparison(current().backendResult,{asset:state.asset,step:state.step});}if(e.target.id==='experiment-search'){state.search=e.target.value;document.getElementById('experiment-rows').innerHTML=experimentRows();}if(e.target.dataset.strategyScope)handleStrategyInput(e.target);});
  dialog.addEventListener('close',()=>{modalGeneration++;releaseModalDownloads();});
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
  window.addEventListener('hashchange',()=>{const p=location.hash.slice(1);if(labels[p]&&p!==state.page){closeModalForNavigation();if(state.page==='new')saveDraft();navigationVersion++;state.page=p;render();}});
  const initial=location.hash.slice(1);if(labels[initial])state.page=initial;
  restoreDraftFromCache();
  window.addEventListener('pagehide',()=>{if(state.page==='new')saveDraft();persistDraft();});
  MarketReplayControl.bind(document);
  StrategyParameterControls.bind(document);
  icons();render();hydrateExperiments();hydrateStrategies();
})();
