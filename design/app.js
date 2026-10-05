/* MarketMirror platform preview. Scientific protocols remain separate and frozen. */
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
  const state = {page:'analysis',selected:0,step:8,asset:'A',decisionGroup:'with_message',signalVisible:true,baselineVisible:true,table:'decisions',wizard:1,search:'',sourceFilter:'all',draft:{title:'',source:'',type:'政策消息',published:'2026-10-05T09:00',signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000}};
  const analysisBinding = new SourceAnalysisBinding(state.draft.source);
  const strategyWorkspace = new StrategyWorkspace();
  const content = document.getElementById('content');
  const dialog = document.getElementById('detail-dialog');
  let chartObserver, toastTimer, rememberedExperimentId = null;
  try { rememberedExperimentId = sessionStorage.getItem('marketmirror:selected-experiment'); } catch (_) {}
  function rememberExperiment() {
    rememberedExperimentId = current().id;
    try { sessionStorage.setItem('marketmirror:selected-experiment', rememberedExperimentId); } catch (_) {}
  }
  const current = () => experiments[state.selected];
  const labels = {analysis:'结果分析',experiments:'实验空间',sources:'消息资料',agents:'策略配置',new:'新建实验'};
  const format = n => n.toLocaleString('zh-CN');
  const signed = n => `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;
  const heading = (eyebrow, title, caption, actions = '') => `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><div class="heading-caption">${caption}</div></div><div class="heading-actions">${actions}</div></div>`;
  const newButton = () => `<button class="btn primary" type="button" data-action="new">${icon('plus')}新建实验</button>`;
  function toast(message) { const el=document.getElementById('toast');el.textContent=message;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),3500); }
  function modal(title, body, footer = `<button type="button" class="btn primary" data-action="close">知道了</button>`) {
    document.getElementById('dialog-content').innerHTML=`<div class="dialog-head"><h2>${title}</h2><button class="icon-button" type="button" data-action="close" aria-label="关闭对话框">${icon('close')}</button></div><div class="dialog-body">${body}</div><div class="dialog-footer">${footer}</div>`;
    if (!dialog.open) dialog.showModal();
  }
  function navigate(page) { if(page==='analysis')rememberExperiment();state.page=page;location.hash=page;render();document.querySelector('.sidebar').classList.remove('open');window.scrollTo({top:0,behavior:'instant'}); }
  function values() {
    const stored=current().backendResult?.sessions;
    if(Array.isArray(stored)&&stored.length===18){const baseline=stored.map(()=>100);return {baseline,signal:stored.map(row=>Number(row.price))};}
    const baseline=[100,100.08,99.91,100.04,100.16,100.07,100.25,100.33,100.2,100.39,100.3,100.55,100.47,100.62,100.7,100.58,100.81,100.94];
    const changes=[0,0,0,0,.12,.34,.72,1.04,.81,1.21,1.44,1.29,1.69,1.53,1.8,1.93,1.72,1.9];
    const multiplier=(current().kind==='negative'?-.95:current().kind==='uncertain'?.13:1)*(state.asset==='A'?1:state.asset==='B'?.45:.2);
    return {baseline,signal:baseline.map((v,i)=>v+changes[i]*multiplier)};
  }
  function roleDecision(index) {
    const step=state.step,active=step>=5 && step<=10, negative=current().kind==='negative';
    const quantities=active?[600,0,300]:step>10?[0,0,100]:[0,0,0];
    const filled=active?[400,0,200]:step>10?[0,0,100]:[0,0,0];
    if(current().kind==='uncertain'){quantities[0]=active?100:0;quantities[2]=0;filled[0]=quantities[0];filled[2]=0;}
    const q=quantities[index];
    return {action:q?(negative?'卖出':'买入'):'等待',quantity:q,filled:filled[index],weight:roles[index].weight+(active?(negative?-1:1)*[12,0,5][index]:0),
      reason:step<5?'消息尚未可见，保持原有目标。':index===1?'不确定性尚未消除，等待下一步确认。':q?(index===0?'信息信号触发目标仓位调整。':'按再平衡规则调整资产配置。'):'当前偏差未触发新增交易。'};
  }
  function analysis() {
    if(current().custom)return runStatusPanel(current())+savedAnalysis();
    const e=current(),v=values(),delta=v.signal.at(-1)-100,impact=v.signal.at(-1)-v.baseline.at(-1);
    return heading('EXPERIMENT ANALYSIS',esc(e.title),`<span>${e.id}</span><span class="sep"></span><span>${e.sessions||18} 个决策步</span><span class="sep"></span><span>三资产模拟市场</span><span class="badge">${e.custom?'本机平台预览':'对照实验 · 示例'}</span>`, `<button class="btn" type="button" data-action="export">${icon('download')}导出</button>${newButton()}`)
      +(e.custom?'<div class="callout">'+icon('help')+'实验配置已保存，并生成了本机 platform_preview 结果。当前图表仍是高保真展示图层，尚未接入 DeepSeek 或冻结研究协议。</div>':'')
      +`<section class="metrics-strip" aria-label="示例实验指标">
        <div class="metric"><div class="metric-label">期末价格变化 ${icon('chart')}</div><div class="metric-value positive" id="metric-price">${signed(delta)}<span class="unit">%</span></div><div class="metric-foot">相对初始价格 100</div></div>
        <div class="metric"><div class="metric-label">消息影响差值 ${icon('layers')}</div><div class="metric-value" id="metric-impact">${signed(impact)}<span class="unit">pp</span></div><div class="metric-foot">有消息 − 无消息</div></div>
        <div class="metric"><div class="metric-label">累计成交股数 ${icon('agents')}</div><div class="metric-value">12,800<span class="unit">股</span></div><div class="metric-foot">含背景账户 · 展示数据</div></div>
        <div class="metric"><div class="metric-label">市场最大回撤 ${icon('shield')}</div><div class="metric-value" id="metric-drawdown">${drawdown(v.signal).toFixed(2)}<span class="unit">%</span></div><div class="metric-foot">模拟价格收盘序列</div></div>
      </section>
      <div class="analysis-grid"><div class="left-stack">
        <section class="panel"><div class="panel-header"><div><h2>市场价格路径</h2><p class="panel-subtitle">观察同一市场在两种信息条件下的变化</p></div><div class="segmented" role="group" aria-label="选择资产">${['A','B','C'].map(a=>`<button type="button" data-asset="${a}" class="${a===state.asset?'active':''}" aria-pressed="${a===state.asset}">资产 ${a}</button>`).join('')}</div></div>
        <div class="legend-row"><button class="legend-button" type="button" data-series="signal" aria-pressed="${state.signalVisible}"><span class="legend-line"></span>有消息</button><button class="legend-button" type="button" data-series="baseline" aria-pressed="${state.baselineVisible}"><span class="legend-line baseline"></span>无消息基线</button><small>初始价格 = 100</small></div>
        <div class="chart-container"><svg class="price-chart" id="price-chart" role="img" aria-label="18个决策步的模拟价格对照示意图"></svg><div class="chart-hover" id="chart-hover" hidden></div></div><div class="chart-caption"><span>价格 · 模型单位</span><span>决策步 →</span></div>
        <div class="timeline-control"><div class="timeline-label"><span>拖动时间轴，查看当步决策</span><strong id="step-label">第 ${String(state.step).padStart(2,'0')} / 18 步</strong></div><input type="range" id="time-slider" min="1" max="18" value="${state.step}" aria-label="选择决策步"><div class="timeline-ticks"><span>T01 · 初始状态</span><span>T05 · 消息可见</span><span>T18 · 实验结束</span></div></div></section>
        <section><div class="section-title"><h2>三类策略 · 当步响应</h2><span>点击策略，查看决策详情</span></div><div class="agent-grid" id="agent-cards">${agentCards()}</div></section>
        <section class="panel"><div class="table-toolbar"><button type="button" data-table="decisions" class="table-tab ${state.table==='decisions'?'active':''}">决策与执行</button><button type="button" data-table="holdings" class="table-tab ${state.table==='holdings'?'active':''}">账户概况</button></div><div class="table-wrap" id="execution-table">${executionTable()}</div></section>
      </div><aside class="right-stack"><section class="panel"><div class="panel-header"><h2>实验摘要</h2><button class="subtle-link" type="button" data-action="duplicate">复制配置 ${icon('chevron')}</button></div><div class="summary-content"><div class="source-label">${icon('document')}${esc(e.type)} · 示例消息</div><p class="source-preview">${esc(e.source.slice(0,74))}${e.source.length>74?'…':''}</p><button class="subtle-link" type="button" data-action="source">查看消息原文 ${icon('arrow')}</button><div class="summary-list"><div class="summary-row"><span>信息条件</span><b>有消息 / 无消息</b></div><div class="summary-row"><span>影响资产</span><b>资产 A</b></div><div class="summary-row"><span>持续时间</span><b>6 个决策步</b></div><div class="summary-row"><span>随机种子</span><span class="chip">${e.seed}</span></div><div class="summary-row"><span>参与策略</span><div class="chips"><span class="chip">激进</span><span class="chip">保守</span><span class="chip">机构</span></div></div></div><div class="summary-assumption">资产暴露与冲击幅度为情景假设；用于观察机制，不代表真实市场预测。</div></div></section>
      <section class="panel"><div class="panel-header"><h2>决策溯源</h2><span class="badge neutral" id="trace-step">T${String(state.step).padStart(2,'0')}</span></div><div class="trace-list" id="trace-list">${trace()}</div><div class="trace-footer">${icon('lock')}原文 → 变量 → 决策 → 成交</div></section></aside></div>`;
  }
  function drawdown(series){let high=series[0],dd=0;series.forEach(v=>{high=Math.max(high,v);dd=Math.max(dd,(high-v)/high*100);});return dd;}
  function agentCards(){return roles.map((r,i)=>{const d=roleDecision(i);return `<button type="button" class="agent-card" style="--role:${r.color}" data-role="${i}" aria-label="查看${r.name}第${state.step}步决策"><div class="agent-top"><span class="agent-title"><span class="agent-mark">${icon(r.icon)}</span>${r.name}</span><span class="badge ${d.action==='等待'?'neutral':i===0?'orange':'blue'}">${d.action}</span></div><div class="agent-return ${current().kind==='negative'?'':'positive'}">${signed((current().kind==='negative'?-1:1)*[1.86,.42,1.12][i]*state.step/18)}%</div><div class="agent-return-label">截至当步 · 示例账户收益</div><div class="allocation-label"><span>目标股票仓位</span><b>${d.weight}%</b></div><div class="allocation-track"><i style="width:${d.weight}%"></i></div><div class="agent-divider"></div><div class="agent-request"><span>请求 / 实际成交</span><b>${d.quantity} / ${d.filled} 股</b></div><p class="agent-note">${d.reason}</p></button>`}).join('');}
  function executionTable(){return `<table><thead><tr>${(state.table==='decisions'?['策略','决策','请求股数','实际成交','执行状态']:['策略','初始资金（模型元）','目标仓位','风险预算']).map(s=>`<th>${s}</th>`).join('')}</tr></thead><tbody>${roles.map((r,i)=>{const d=roleDecision(i);return `<tr><td style="--role:${r.color}"><span class="row-dot"></span>${r.name}</td>${state.table==='decisions'?`<td>${d.action}</td><td>${format(d.quantity)}</td><td>${format(d.filled)}</td><td><span class="badge ${d.quantity>d.filled?'orange':'neutral'}">${!d.quantity?'无新增订单':d.quantity>d.filled?'部分成交':'全部成交'}</span></td>`:`<td>1,000,000</td><td>${d.weight}%</td><td>${r.risk.toFixed(3)}</td>`}</tr>`}).join('')}</tbody></table>`;}
  function trace(){const visible=state.step>=5,active=visible&&state.step<=10;const steps=[['原文与可见时间',visible?'消息已进入本步可见信息集。':'消息尚未公开，不参与本步决策。'],['信息变量',active?`情景信号 <strong>${current().kind==='negative'?'−0.60':current().kind==='uncertain'?'0.00':'+0.60'}</strong> · 不确定性 <strong>0.20</strong>`:visible?'消息作用期已结束。':'信号为 0，等待消息可见。'],['三类策略响应',active?'激进调整仓位，保守等待确认，机构按组合规则响应。':'按各自基础仓位与风险规则行动。'],['订单与实际成交',active?'激进请求 600 股 → 示例成交 400 股。': '是否发单取决于目标仓位与当前持仓。']];return steps.map((s,i)=>`<div class="trace-item"><span class="trace-number">${i+1}</span><div><div class="trace-title">${s[0]}</div><p class="trace-description">${s[1]}</p></div></div>`).join('');}
  function drawChart(){const svg=document.getElementById('price-chart');if(!svg)return;const w=Math.max(240,svg.clientWidth),h=svg.clientHeight||242,p={l:43,r:20,t:29,b:30},v=values(),all=[...v.baseline,...v.signal];let min=Math.floor((Math.min(...all)-.2)*2)/2,max=Math.ceil((Math.max(...all)+.2)*2)/2;const x=i=>p.l+i*(w-p.l-p.r)/17,y=z=>h-p.b-(z-min)/(max-min)*(h-p.t-p.b);const line=a=>a.map((n,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(n).toFixed(2)}`).join(' ');const n=state.step-1;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);const ticks=Array.from({length:5},(_,i)=>min+(max-min)*i/4);const xs=w<400?[0,5,11,17]:[0,3,6,9,12,15,17];svg.innerHTML=`<defs><linearGradient id="area-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--teal)" stop-opacity=".12"/><stop offset="100%" stop-color="var(--teal)" stop-opacity="0"/></linearGradient></defs><rect x="${x(4)}" y="${p.t}" width="${x(9)-x(4)}" height="${h-p.b-p.t}" fill="var(--teal)" opacity=".035"/>${ticks.map(t=>`<path d="M${p.l} ${y(t)}H${w-p.r}" class="chart-grid"/><text x="${p.l-10}" y="${y(t)+3}" text-anchor="end" class="chart-label">${t.toFixed(1)}</text>`).join('')}${xs.map(i=>`<text x="${x(i)}" y="${h-9}" text-anchor="middle" class="chart-label">${String(i+1).padStart(2,'0')}</text>`).join('')}<path d="M${x(4)} ${p.t}V${h-p.b}" stroke="var(--teal)" stroke-opacity=".3" stroke-dasharray="3 4"/><text x="${x(4)+7}" y="17" class="chart-label" style="fill:var(--teal)">消息可见</text>${state.signalVisible?`<path d="${line(v.signal)}L${x(17)} ${h-p.b}L${x(0)} ${h-p.b}Z" fill="url(#area-fill)"/><path d="${line(v.signal)}" fill="none" stroke="var(--teal)" stroke-width="2.4" stroke-linejoin="round"/>`:''}${state.baselineVisible?`<path d="${line(v.baseline)}" fill="none" stroke="#95a5b8" stroke-width="1.7" stroke-dasharray="5 5" stroke-linejoin="round"/>`:''}<path d="M${x(n)} ${p.t}V${h-p.b}" stroke="var(--faint)" stroke-dasharray="3 4"/>${state.signalVisible?`<circle cx="${x(n)}" cy="${y(v.signal[n])}" r="4" fill="var(--surface)" stroke="var(--teal)" stroke-width="2"/>`:''}${state.baselineVisible?`<circle cx="${x(n)}" cy="${y(v.baseline[n])}" r="3" fill="var(--surface)" stroke="#95a5b8" stroke-width="1.6"/>`:''}`;
    svg.onpointermove=e=>{const box=svg.getBoundingClientRect(),i=Math.max(0,Math.min(17,Math.round(((e.clientX-box.left)-p.l)/(w-p.l-p.r)*17)));const tip=document.getElementById('chart-hover');tip.hidden=false;tip.style.left=Math.min(w-155,Math.max(25,x(i)))+'px';tip.textContent=`T${String(i+1).padStart(2,'0')}　${state.signalVisible?'有消息 '+v.signal[i].toFixed(2):''}${state.baselineVisible?' / 基线 '+v.baseline[i].toFixed(2):''}`;};svg.onpointerleave=()=>{document.getElementById('chart-hover').hidden=true;};svg.onclick=e=>{const box=svg.getBoundingClientRect();setStep(Math.max(1,Math.min(18,Math.round(((e.clientX-box.left)-p.l)/(w-p.l-p.r)*17)+1)));};
  }
  function setStep(step){state.step=step;document.getElementById('time-slider').value=step;document.getElementById('step-label').textContent=`第 ${String(step).padStart(2,'0')} / 18 步`;document.getElementById('trace-step').textContent='T'+String(step).padStart(2,'0');document.getElementById('agent-cards').innerHTML=agentCards();document.getElementById('execution-table').innerHTML=executionTable();document.getElementById('trace-list').innerHTML=trace();drawChart();}
  function experimentRows(){const rows=experiments.filter(e=>(e.title+e.type).toLowerCase().includes(state.search.toLowerCase()));return rows.length?rows.map(e=>{const inconsistent=e.run_status==='completed'&&!e.backendResult;const status=e.custom?(e.run_status==='running'?'运行中':e.run_status==='failed'?'运行失败':e.run_status==='interrupted'?'运行已中断':inconsistent?'结果缺失 · 请刷新':e.backendResult?(e.backendResult.mode==='synthetic_market'?'已完成 · 撮合实验':'已完成 · 本机预览'):'已保存 · 无可用结果'):'已完成 · 示例';return `<tr class="experiment-row"><td><button class="subtle-link experiment-name" type="button" data-open="${experiments.indexOf(e)}">${esc(e.title)}</button><small>${e.id} · ${esc(e.type)}</small></td><td><span class="badge ${e.custom?'neutral':''}">${status}</span></td><td>${e.sessions||18} 步 / 三类策略</td><td>${e.seed}</td><td>${e.time}</td><td><button class="icon-button" data-open="${experiments.indexOf(e)}" aria-label="打开${esc(e.title)}">${icon('arrow')}</button></td></tr>`;}).join(''):'<tr><td colspan="6" class="empty">没有找到匹配的实验。试试“政策”或“需求”。</td></tr>';}
  function experimentsPage(){return heading('YOUR EXPERIMENTS','实验空间','每一次实验，都保留信息、假设与结果。',newButton())+`<div class="overview-intro"><div><div class="eyebrow">从一个问题开始</div><h2>同一条消息，不同策略会怎样行动？</h2><p>选择一个示例，或创建自己的消息情景。</p></div><button class="btn" type="button" data-open="${experiments.findIndex(e=>!e.custom)}">打开示例实验 ${icon('arrow')}</button></div><div class="experiment-tools"><div class="searchbox">${icon('search')}<input id="experiment-search" type="search" value="${esc(state.search)}" placeholder="搜索实验名称或消息类型" aria-label="搜索实验"></div><span class="form-hint">${experiments.length} 个实验 · 示例与本机记录</span></div><section class="panel table-wrap"><table><thead><tr><th>实验名称</th><th>状态</th><th>实验范围</th><th>种子</th><th>创建时间</th><th></th></tr></thead><tbody id="experiment-rows">${experimentRows()}</tbody></table></section>`;}
  function sourcesPage(){
    const saved=experiments.filter(e=>e.custom),examples=experiments.filter(e=>!e.custom);
    const cards=rows=>rows.map(e=>`<article class="panel source-card"><div class="source-label">${icon('document')}<span class="badge neutral">${esc(e.type)}</span><span class="badge neutral">${e.custom?'已保存原文':'虚构示例'}</span></div><h2>${esc(e.title)}</h2><details><summary>展开消息原文 · ${e.source.length} 字符</summary><p style="white-space:pre-wrap">${esc(e.source)}</p></details><p class="form-hint">${e.custom?(e.text_analysis?'已关联事实提取与引文；不代表事实真实性已验证。':'未关联模型分析。'):'仅用于体验操作流程。'}</p><div class="source-card-bottom"><span class="form-hint">实验 ${esc(e.id)}</span><button class="subtle-link" type="button" data-use="${experiments.indexOf(e)}">复制消息与配置 ${icon('arrow')}</button></div><button class="subtle-link" type="button" data-open="${experiments.indexOf(e)}">查看关联实验</button></article>`).join('');
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
  const strategyValue = (key,value) => key==='text_sensitivity'?Number(value).toFixed(2):(Number(value)*100).toFixed(key==='base_weight'?0:2)+'%';
  function strategyTable(parameters){
    return `<div class="table-wrap"><table><thead><tr><th>策略</th>${strategyFields.map(f=>`<th>${f.label}</th>`).join('')}</tr></thead><tbody>${roles.map(r=>`<tr><td><span class="row-dot" style="--role:${r.color}"></span>${r.name}</td>${strategyFields.map(f=>`<td>${strategyValue(f.key,parameters[r.key][f.key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function strategyCards(parameters,scope){
    return `<div class="role-editor-grid strategy-editor-grid">${roles.map(r=>{const rules=strategyWorkspace.fixedRules[r.key];return `<section class="panel role-editor" style="--role:${r.color}"><div class="agent-mark">${icon(r.icon)}</div><h2>${r.name}</h2><p class="role-description">${r.desc}</p>${strategyFields.map(f=>{const bounds=strategyWorkspace.limits[r.key][f.key],id=`strategy-${scope}-${r.key}-${f.key}`,value=parameters[r.key][f.key];return `<div class="form-field"><label for="${id}">${f.label}<span class="role-parameter-value" id="${id}-value">${strategyValue(f.key,value)}</span></label><input id="${id}" type="range" data-strategy-scope="${scope}" data-strategy-role="${r.key}" data-strategy-key="${f.key}" min="${bounds[0]*f.scale}" max="${bounds[1]*f.scale}" step="${f.step}" value="${Number((value*f.scale).toFixed(6))}" ${strategyWorkspace.saving?'disabled':''}><small>${f.hint}</small></div>`;}).join('')}<div class="summary-assumption">最高股票权重 ${Math.round(rules.max_weight*100)}% · 连续确认 ${rules.confirmation_steps} 步 · 每 ${rules.rebalance_interval} 步再平衡</div></section>`;}).join('')}</div>`;
  }
  function strategyUnavailable(){return `<div class="analysis-status" role="status">${strategyWorkspace.loading?'正在读取本机策略参数…':esc(strategyWorkspace.error||'策略参数尚未加载')} ${strategyWorkspace.loading?'':'<button class="btn compact" data-action="retry-strategies">重新加载</button>'}</div>`;}
  function draftStrategyPanel(){
    if(!state.draft.strategy_parameters||!strategyWorkspace.parameters)return strategyUnavailable();
    return `<div class="draft-strategy-section"><h2>本次实验的三类策略</h2><p class="evidence-note">参数将随本次实验独立保存。这里的调整仅用于本次实验。</p><details class="strategy-disclosure"><summary>展开策略参数</summary>${strategyCards(state.draft.strategy_parameters,'experiment')}</details></div>`;
  }
  function agentsPage(){
    const unavailable=!strategyWorkspace.parameters,disabled=unavailable||strategyWorkspace.saving;
    return heading('AGENT STRATEGIES','三类策略配置','用明确的行为规则，表达不同的决策偏好。',`<button class="btn" data-action="reset-roles" ${disabled?'disabled':''}>${icon('replay')}恢复平台默认</button><button class="btn primary" data-action="save-roles" ${disabled?'disabled':''}>${strategyWorkspace.saving?'正在保存…':'保存为工作区默认'}</button>`)
      +(unavailable?strategyUnavailable():`<div class="callout">${icon('sliders')}保存后用于之后新建的实验。每次实验都保留独立参数，已有实验按原参数运行。</div><div class="strategy-save-state" id="strategy-save-state" role="status">${strategyWorkspace.error?esc(strategyWorkspace.error):strategyWorkspace.dirty?'有未保存的修改':strategyWorkspace.updatedAt?'已保存在本机 · 新建实验时自动载入':'当前使用平台默认参数'}</div>${strategyCards(strategyWorkspace.draft,'workspace')}`);
  }
  async function hydrateStrategies(){
    strategyWorkspace.loading=true;strategyWorkspace.error='';
    try{strategyWorkspace.load(await apiJson('/api/platform/strategies'));if(!state.draft.strategy_parameters)state.draft.strategy_parameters=strategyWorkspace.snapshot();}
    catch(error){strategyWorkspace.error='策略参数读取失败：'+error.message;}
    finally{strategyWorkspace.loading=false;if(['agents','new'].includes(state.page)){if(state.page==='new')saveDraft();render();}}
  }
  async function saveStrategies(){
    if(!strategyWorkspace.parameters||strategyWorkspace.saving)return;
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
  function saveDraft(){document.querySelectorAll('[data-draft]').forEach(el=>{state.draft[el.dataset.draft]=el.type==='range'||el.type==='number'?Number(el.value):el.value;});analysisBinding.setSource(state.draft.source);}
  const factStatus = {confirmed:'原文陈述',uncertain:'尚不确定',question:'提问'};
  function analysisEvidence(analysis){
    const time=analysis.created_at?new Date(analysis.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未记录';
    return `<div class="evidence-meta"><span class="badge">引文匹配通过</span><span>${esc(analysis.model)}</span><span>${esc(time)} · 北京时间</span></div>
      <p class="evidence-note">以下为模型提取，原文引文已匹配。实验方向、强度与不确定性由你设置。</p>
      <div class="evidence-facts">${analysis.facts.length?analysis.facts.map((fact,index)=>`<article class="evidence-fact"><div class="evidence-fact-heading"><span class="trace-number">${index+1}</span><span class="badge neutral">${esc(factStatus[fact.status]||fact.status)}</span></div><p class="evidence-claim">${esc(fact.claim)}</p><blockquote>${esc(fact.quote)}</blockquote></article>`).join(''):'<p class="evidence-note">模型未提取到可用事实。这份空结果仍会如实保存。</p>'}</div>
      <details class="evidence-version"><summary>分析记录与版本</summary><dl><dt>记录编号</dt><dd>${esc(analysis.analysis_id)}</dd><dt>原文 SHA-256</dt><dd>${esc(analysis.source_sha256)}</dd><dt>提示词 SHA-256</dt><dd>${esc(analysis.prompt_sha256)}</dd></dl></details>`;
  }
  function draftAnalysisPanel(){
    if(analysisBinding.pending)return '<div class="analysis-status" role="status">正在使用 DeepSeek 提取事实…<button type="button" class="subtle-link" data-action="detach-analysis">取消等待，手动设置</button></div>';
    if(analysisBinding.analysis)return `<div class="analysis-status"><span>${analysisBinding.analysis.facts.length} 条模型事实将随实验保存</span><button type="button" class="subtle-link" data-action="detach-analysis">取消关联</button></div><details class="evidence-draft" open><summary>事实与原文依据</summary>${analysisEvidence(analysisBinding.analysis)}</details>`;
    const message=analysisBinding.error?'提取失败：'+analysisBinding.error:analysisBinding.outdated?'原文已修改，旧分析已取消关联。可重新提取，或继续手动设置情景。':'可选：提取事实并随实验保存；也可直接手动设置情景。';
    return `<p class="analysis-status ${analysisBinding.error?'error':''}" role="status">${esc(message)}</p>`;
  }
  function updateDraftAnalysisPanel(){
    const panel=document.getElementById('draft-analysis-panel');if(panel)panel.innerHTML=draftAnalysisPanel();
    const button=document.querySelector('[data-action="analyze-source"]');
    if(button){button.disabled=analysisBinding.pending;button.textContent=analysisBinding.pending?'正在提取事实…':'使用 DeepSeek 提取事实';}
  }
  function experimentEvidence(analysis){
    return `<section class="panel evidence-panel"><div class="panel-header"><h2>文本分析与原文依据</h2><span class="badge neutral">${analysis?'已关联':'手动情景'}</span></div><div class="summary-content">${analysis?analysisEvidence(analysis):'<p class="evidence-note">此实验未关联模型分析，情景变量由用户直接设定。</p>'}</div></section>`;
  }
  function wizardPage(){const d=state.draft;return heading('NEW EXPERIMENT','从一条消息，建立一个实验','先定义消息与假设，再观察三类策略的响应。',`<button class="btn ghost" data-action="cancel-new" type="button">返回实验空间</button>`)+`<div class="stepper" aria-label="创建步骤">${['消息与来源','情景与策略','确认实验'].map((label,i)=>`<div class="step-item ${state.wizard===i+1?'active':''}"><span>${state.wizard>i+1?'✓':i+1}</span>${label}</div>`).join('')}</div><div class="wizard-layout"><section class="panel"><div class="form-body">${state.wizard===1?`
      <div class="form-field"><label for="draft-title">实验名称</label><input id="draft-title" data-draft="title" maxlength="70" value="${esc(d.title)}" placeholder="例如：流动性宽松消息对三类策略的影响"></div><div class="form-two"><div class="form-field"><label for="draft-type">消息类型</label><select id="draft-type" data-draft="type">${['政策消息','宏观消息','公司问答','其他消息'].map(x=>`<option ${x===d.type?'selected':''}>${x}</option>`).join('')}</select></div><div class="form-field"><label for="draft-time">发布时间 · 北京时间</label><input id="draft-time" type="datetime-local" data-draft="published" value="${esc(d.published)}"></div></div><div class="form-field"><label for="draft-source">消息原文</label><button class="btn" type="button" data-action="analyze-source">使用 DeepSeek 提取事实</button><textarea id="draft-source" data-draft="source" maxlength="12000" placeholder="粘贴政策、新闻或公司问答原文。公司问答请同时保留提问和回复。">${esc(d.source)}</textarea><small>保留原文，便于在决策详情中查看证据与信息可见时间。</small></div><div id="draft-analysis-panel">${draftAnalysisPanel()}</div><div class="form-hint">也可以从示例开始</div><div class="sample-picker">${experiments.filter(e=>!e.custom).map(e=>`<button type="button" data-sample="${experiments.indexOf(e)}">${esc(e.title.split(' · ')[0])}</button>`).join('')}</div>
      `:state.wizard===2?`<h2>文本变量与情景假设</h2><div class="review-box"><strong>情景假设 · 变量由你设定</strong><p>参考下方事实设定方向与强度。新建实验从中性信号 0 开始；复制实验保留原参数。模型提取不会替你判断利好或利空，也不会改动这些参数。</p></div><div id="draft-analysis-panel">${draftAnalysisPanel()}</div><div class="form-two"><div class="form-field"><label for="draft-signal">情景方向与强度 <span id="signal-value">${d.signal.toFixed(2)}</span></label><input id="draft-signal" type="range" data-draft="signal" min="-1" max="1" step=".05" value="${d.signal}"><small>−1 负向 · 0 中性 · +1 正向。信号为 0 时，不确定性仍可能影响策略。</small></div><div class="form-field"><label for="draft-uncertainty">信息不确定性 <span id="uncertainty-value">${d.uncertainty.toFixed(2)}</span></label><input id="draft-uncertainty" type="range" data-draft="uncertainty" min="0" max="1" step=".05" value="${d.uncertainty}"><small>0 低不确定性 · 1 高不确定性</small></div></div><div class="form-two"><div class="form-field"><label for="draft-duration">消息持续步数</label><select id="draft-duration" data-draft="duration">${[3,6,9].map(n=>`<option value="${n}" ${Number(d.duration)===n?'selected':''}>${n} 个决策步</option>`).join('')}</select></div><div class="form-field"><label for="draft-seed">随机种子</label><input id="draft-seed" type="number" data-draft="seed" min="0" max="999999" value="${d.seed}"></div></div><div class="form-field"><label for="draft-cash">每类策略初始资金（模型元）</label><input id="draft-cash" type="number" data-draft="cash" min="10000" max="100000000" step="10000" value="${d.cash}"></div><div class="summary-assumption">模拟资产 A / B / C，消息作用于资产 A。包含有消息与无消息两组、${d.sessions||18} 个决策步。三类策略同时参与。</div>${draftStrategyPanel()}`:`<h2>确认实验配置</h2><div class="review-box"><strong>${esc(d.title)}</strong><p>${esc(d.source.slice(0,180))}${d.source.length>180?'…':''}</p></div>${[['消息类型',d.type],['发布时间',d.published.replace('T',' ')+'（北京时间）'],['参与策略','激进型 / 保守型 / 机构型'],['信息条件','有消息 / 无消息'],['情景变量',`信号 ${Number(d.signal).toFixed(2)} · 不确定性 ${Number(d.uncertainty).toFixed(2)}`],['持续时间',`${d.duration} 步`],['初始资金',format(d.cash)+' 模型元 / 类'],['随机种子',d.seed]].map(([a,b])=>`<div class="review-field"><span class="form-hint">${a}</span><span>${esc(b)}</span></div>`).join('')}<div id="draft-analysis-panel">${draftAnalysisPanel()}</div>${d.strategy_parameters?strategyTable(d.strategy_parameters):strategyUnavailable()}<label class="checkline"><input id="confirm-demo" type="checkbox">我理解这是合成市场实验，情景变量由我设定，结果不代表真实市场预测。</label>`}</div><div class="form-error" id="form-error" role="alert"></div><div class="form-footer">${state.wizard>1?'<button class="btn" type="button" data-action="previous">上一步</button>':'<span class="form-hint">提交后保存到本机</span>'}<button class="btn primary" type="button" data-action="${state.wizard===3?'preview-run':'next'}">${state.wizard===3?'运行市场实验':'下一步'} ${icon('arrow')}</button></div></section><aside class="guide-block"><div class="eyebrow">EXPERIMENT GUIDE</div><h2>先明确假设，再观察结果</h2><p>让消息、策略和执行之间的每一个环节都可见。</p><div class="guide-item">${icon('document')}保留事实与原文证据</div><div class="guide-item">${icon('clock')}明确消息可见时间</div><div class="guide-item">${icon('agents')}比较三类策略的差异</div><div class="guide-item">${icon('layers')}保留无消息对照组</div><div class="guide-item">${icon('replay')}保存参数以便复现</div><div class="summary-assumption">可先用 DeepSeek 提取事实，再设定情景变量。运行后保存消息、分析依据、参数与撮合账本。</div></aside></div>`;}
  let experimentLoadError='';
  function render(){document.querySelector('.nav-count').textContent=experiments.length;document.querySelector('.preview-tag').textContent=current().custom&&state.page==='analysis'?'已保存实验':'本机研究空间';if(chartObserver)chartObserver.disconnect();document.getElementById('breadcrumb-current').textContent=labels[state.page]||'结果分析';document.querySelectorAll('[data-nav]').forEach(a=>{const active=a.dataset.nav===state.page;a.classList.toggle('active',active);active?a.setAttribute('aria-current','page'):a.removeAttribute('aria-current');});content.innerHTML=(experimentLoadError&&['experiments','analysis','sources'].includes(state.page)?`<div class="callout" role="alert"><div><strong>本机实验记录未能完整加载</strong><p>${esc(experimentLoadError)}。现有页面内容可能不是最新状态，不代表记录已丢失。</p><button class="btn" data-action="refresh-runs">重新加载记录</button></div></div>`:'')+({analysis,experiments:experimentsPage,sources:sourcesPage,agents:agentsPage,new:wizardPage}[state.page]||analysis)();icons();if(state.page==='new')updateDraftAnalysisPanel();if(state.page==='analysis'){drawChart();chartObserver=new ResizeObserver(drawChart);const chart=document.getElementById('price-chart');if(chart)chartObserver.observe(chart);}}
  function useSample(index){const e=experiments[index];state.draft={...state.draft,title:e.title,source:e.source,type:e.type,seed:e.seed,signal:e.signal??(e.kind==='negative'?-.6:e.kind==='uncertain'?0:.6),uncertainty:e.uncertainty??(e.kind==='uncertain'?.8:.2),duration:e.duration??6,cash:e.cash??1000000,sessions:e.sessions??18,published:e.published_at||state.draft.published,strategy_parameters:parametersFromExperiment(e)||(strategyWorkspace.parameters?strategyWorkspace.snapshot():null)};analysisBinding.reset(e.source);if(e.text_analysis)analysisBinding.finish(analysisBinding.begin(e.source),e.text_analysis);state.wizard=1;navigate('new');}
  async function apiJson(path, options={}) { const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}}); const body=await response.json(); if(!response.ok){const error=new Error(body.error||`API ${response.status}`);error.status=response.status;throw error;} return body; }
  const activeRuns=new Set();
  function runStatusPanel(e){
    const status=e.run_status||'not_started';
    if(status==='completed'&&e.backendResult)return '';
    const busy=activeRuns.has(e.id)||status==='running'||status==='unknown';
    const label=status==='completed'&&!e.backendResult?'运行记录已完成，但结果文件缺失':status==='unknown'?'运行状态待确认':busy?'实验正在运行':status==='failed'?'上次运行失败':status==='interrupted'?'上次运行已中断':'实验配置已保存，尚未完成运行';
    return `<section class="callout" role="status"><div><strong>${label}</strong><p>${e.backendResult?'下方保留的是上一次成功结果。':'完成运行后可查看撮合结果。'} 已尝试 ${Number(e.run_attempts)||0} 次。</p>${e.run_error?`<p>${esc(e.run_error.message||'请重试或检查本机服务。')}</p>`:''}<button class="btn primary" data-action="retry-run" ${busy?'disabled':''}>${status==='unknown'?'等待状态确认':busy?'运行中…':'运行此实验'}</button><button class="btn" data-action="refresh-runs">刷新状态</button></div></section>`;
  }
  async function retryRun(){
    const e=current();if(!e.custom||activeRuns.has(e.id)||['running','unknown'].includes(e.run_status))return;
    activeRuns.add(e.id);e.run_status='running';e.run_error=null;render();
    try{e.backendResult=await apiJson(`/api/platform/experiments/${e.id}/run`,{method:'POST',body:'{}'});e.run_status='completed';toast('实验完成，结果已保存。');}
    catch(error){e.run_status='unknown';e.run_error={message:'请求未完成：'+error.message+'。请刷新确认服务端状态后重试。'};toast('请刷新确认运行状态。');}
    finally{activeRuns.delete(e.id);await hydrateExperiments();render();}
  }
  async function persistAndRun() { const payload={title:state.draft.title,source:state.draft.source,type:state.draft.type,published_at:state.draft.published,signal:Number(state.draft.signal),uncertainty:Number(state.draft.uncertainty),duration:Number(state.draft.duration),sessions:Number(state.draft.sessions||18),seed:Number(state.draft.seed),cash:Number(state.draft.cash),analysis_id:analysisBinding.analysisId,strategy_parameters:copyParameters(state.draft.strategy_parameters)}; const record=await apiJson('/api/platform/experiments',{method:'POST',body:JSON.stringify(payload)}); experiments.unshift({...record,custom:true,time:'刚刚',kind:record.signal<0?'negative':'positive',backendResult:null}); const result=await apiJson(`/api/platform/experiments/${record.id}/run`,{method:'POST',body:'{}'}); return {record,result}; }
  async function hydrateExperiments(){try{const rows=await apiJson('/api/platform/experiments');const loaded=await Promise.all(rows.map(async row=>{const detail=await apiJson(`/api/platform/experiments/${row.id}`);let result=null;try{result=await apiJson(`/api/platform/experiments/${row.id}/result`);}catch(error){if(error.status!==404)throw error;}const created=detail.created_at?new Date(detail.created_at):null;return {...detail,id:row.id,kind:Number(detail.signal)<0?'negative':Number(detail.uncertainty)>.65?'uncertain':'positive',time:created&&!Number.isNaN(created.getTime())?created.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'本机记录',sessions:Number(detail.sessions)||18,custom:true,backendResult:result,customResultMissing:detail.run_status==='completed'&&!result};}));const selectedId=rememberedExperimentId||current().id;const ids=new Set(loaded.map(e=>e.id));for(let i=experiments.length-1;i>=0;i--)if(ids.has(experiments[i].id))experiments.splice(i,1);experiments.push(...loaded);experimentLoadError='';state.selected=Math.max(0,experiments.findIndex(e=>e.id===selectedId));if(['experiments','analysis','sources'].includes(state.page))render();}catch(error){experimentLoadError=error.message||'服务连接失败';if(['experiments','analysis','sources'].includes(state.page))render();}}
  async function previewRun(){
    saveDraft();
    if(!state.draft.strategy_parameters){toast('请先加载策略参数再运行。');return;}
    if(analysisBinding.pending){toast('文本分析仍在进行，可等待完成或取消关联后运行。');return;}
    if(!document.getElementById('confirm-demo').checked){document.getElementById('form-error').textContent='请先确认预览说明。';return;}
    const button=document.querySelector('[data-action="preview-run"]');button.disabled=true;
    try{
      const {record,result}=await persistAndRun();
      const index=experiments.findIndex(e=>e.id===record.id);experiments[index]={...experiments[index],...record,run_status:'completed',backendResult:result};state.selected=index;
      navigate('analysis');toast('实验完成，撮合账本已审计并保存。');
    }catch(error){document.getElementById('form-error').textContent='保存或运行失败：'+error.message+'。已保存的实验可在实验空间中打开并重试。';await hydrateExperiments();}
    finally{button.disabled=false;}
  }
  function savedPriceChart(rows,title='情景价格路径',bounds=null){
    if(!rows.length)return '';
    const prices=rows.map(r=>Number(r.price)),low=bounds?.low??Math.min(...prices)-.2,high=bounds?.high??Math.max(...prices)+.2;
    const x=i=>50+i*700/Math.max(1,rows.length-1),y=p=>210-(p-low)/(high-low)*180;
    const path=prices.map((p,i)=>`${i?'L':'M'}${x(i)},${y(p)}`).join(' ');
    return `<section class="panel"><div class="panel-header"><h2>${esc(title)}</h2><span class="badge neutral">已保存数据</span></div><svg viewBox="0 0 800 250" style="width:100%;max-height:320px" role="img" aria-label="${esc(title)}：已保存实验的逐步价格曲线"><path d="M50 30V210H750" fill="none" stroke="#aab7c4"/><path d="${path}" fill="none" stroke="var(--teal)" stroke-width="3"/>${prices.map((p,i)=>`<circle cx="${x(i)}" cy="${y(p)}" r="3" fill="var(--teal)"><title>第 ${i+1} 步：${p}</title></circle>`).join('')}<text x="50" y="237" fill="currentColor">第 1 步</text><text x="680" y="237" fill="currentColor">第 ${rows.length} 步</text><text x="6" y="35" fill="currentColor">${high.toFixed(1)}</text><text x="6" y="210" fill="currentColor">${low.toFixed(1)}</text></svg></section>`;
  }
  function marketStepCards(){
    const view=MarketDecisionView.buildStep(current().backendResult,state.decisionGroup,state.asset,state.step);
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
    const label={aggressive:'激进型',conservative:'保守型',institutional:'机构型'};
    const allPrices=[...priceRows(active),...priceRows(base)].map(row=>row.price);
    const bounds={low:Math.min(...allPrices)-.2,high:Math.max(...allPrices)+.2};
    return heading('MARKET EXPERIMENT',esc(e.title),'<span class="badge">合成市场 · 撮合计算</span>',`<a class="btn" href="/api/platform/experiments/${e.id}/export" download>${icon('download')}导出账本</a><button class="btn" data-action="duplicate">复制为新实验</button>${newButton()}`)
      +`<div class="callout">账本审计通过：${r.audit.days_checked} 个市场日。消息从第 5 步作用于资产 A；信号与暴露为人工情景假设，${e.text_analysis?'已关联模型事实作为参考':'未关联模型分析'}。</div>`
      +`<section class="panel"><div class="panel-header"><h2>实验条件</h2></div><div class="summary-content"><p>${esc(e.source)}</p><p>信号 ${e.signal} · 不确定性 ${e.uncertainty} · 持续 ${e.duration} 步 · 种子 ${e.seed}</p><p>每类 4 个账户，每类初始现金合计 ${format(r.assumptions.effective_initial_cash_per_role)} 元，另有每账户每资产 500 股初始库存。</p></div></section>`
      +experimentStrategyPanel(e)+experimentEvidence(e.text_analysis)
      +`<div class="section-title"><h2>市场价格对照 <small>两组使用相同坐标刻度</small></h2><div class="segmented">${['A','B','C'].map(a=>`<button data-asset="${a}" class="${state.asset===a?'active':''}">资产 ${a}</button>`).join('')}</div></div>`
      +'<div class="market-chart-grid">'+savedPriceChart(priceRows(active),'有消息',bounds)+savedPriceChart(priceRows(base),'无消息对照',bounds)+'</div>'
      +`<section class="panel timeline-control"><div class="timeline-label"><label for="market-step">逐步查看策略与成交 · 资产 ${state.asset}</label><strong id="market-step-label">第 ${state.step} / ${active.trace.length} 步</strong></div><input id="market-step" type="range" min="1" max="${active.trace.length}" value="${state.step}"><div class="segmented" role="group" aria-label="决策信息条件">${[['with_message','有消息组'],['baseline','无消息组']].map(([key,label])=>`<button type="button" data-decision-group="${key}" class="${state.decisionGroup===key?'active':''}" aria-pressed="${state.decisionGroup===key}">${label}</button>`).join('')}</div><p>第 5 步消息可见。当前查看${state.decisionGroup==='baseline'?'无消息对照组':'有消息组'}的真实实验账本；切换时保留资产与决策步。</p></section><div class="role-editor-grid" id="market-step-cards">${marketStepCards()}</div>`
      +`<section class="panel"><div class="panel-header"><h2>三类策略 · 账户收益对照</h2></div><div class="table-wrap"><table><thead><tr><th>策略</th><th>有消息收益</th><th>无消息收益</th><th>差值</th></tr></thead><tbody>${Object.keys(label).map(k=>{const a=(active.summary.role_wealth_multiple[k]-1)*100,b=(base.summary.role_wealth_multiple[k]-1)*100;return `<tr><td>${label[k]}</td><td>${signed(a)}%</td><td>${signed(b)}%</td><td>${signed(a-b)} pp</td></tr>`;}).join('')}</tbody></table></div></section>`
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
    const nav=e.target.closest('[data-nav]');if(nav){e.preventDefault();navigate(nav.dataset.nav);return;}
    const open=e.target.closest('[data-open]');if(open){state.selected=Number(open.dataset.open);state.step=8;navigate('analysis');return;}
    const sample=e.target.closest('[data-sample],[data-use]');if(sample){saveDraft();useSample(Number(sample.dataset.sample??sample.dataset.use));return;}
    const group=e.target.closest('[data-decision-group]');if(group){state.decisionGroup=group.dataset.decisionGroup;render();return;}
    const asset=e.target.closest('[data-asset]');if(asset){state.asset=asset.dataset.asset;render();return;}
    const series=e.target.closest('[data-series]');if(series){const key=series.dataset.series+'Visible';if(state[key]&&!state[series.dataset.series==='signal'?'baselineVisible':'signalVisible']){toast('至少保留一条价格曲线。');return;}state[key]=!state[key];series.setAttribute('aria-pressed',state[key]);drawChart();return;}
    const table=e.target.closest('[data-table]');if(table){state.table=table.dataset.table;document.querySelectorAll('[data-table]').forEach(b=>b.classList.toggle('active',b===table));document.getElementById('execution-table').innerHTML=executionTable();return;}
    const role=e.target.closest('[data-role]');if(role){const i=Number(role.dataset.role),r=roles[i],d=roleDecision(i);modal(`${r.name} · 第 ${state.step} 步决策`, `<div class="eyebrow">DECISION TRACE · 展示数据</div><p>${r.desc}</p><div class="review-field"><span>决策动作</span><strong>${d.action}</strong></div><div class="review-field"><span>目标股票仓位</span><strong>${d.weight}%</strong></div><div class="flow-line"><span>请求 ${d.quantity} 股</span>${icon('arrow')}<span>成交 ${d.filled} 股</span></div><div class="review-box"><strong>决策解释</strong><p>${d.reason}</p><p>${d.quantity>d.filled?'示例中订单只获得部分匹配，因此请求量大于成交量。':'本步示例没有未成交的新增数量。'}</p></div><p>这是决策详情的设计示例，尚未读取真实实验账本。</p>`);return;}
    const b=e.target.closest('[data-action]');if(!b)return;const action=b.dataset.action;
    if(action==='retry-run'){await retryRun();return;}
    if(action==='refresh-runs'){await hydrateExperiments();return;}
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

    if(action==='new'){analysisBinding.reset('');state.draft={title:'',source:'',type:'政策消息',published:state.draft.published,signal:0,uncertainty:.2,duration:6,sessions:18,seed:7,cash:1000000,strategy_parameters:strategyWorkspace.parameters?strategyWorkspace.snapshot():null};state.wizard=1;navigate('new');}
    if(action==='close')dialog.close();
    if(action==='menu')document.querySelector('.sidebar').classList.toggle('open');
    if(action==='theme'){document.body.dataset.theme=document.body.dataset.theme==='dark'?'light':'dark';drawChart();}
    if(action==='cancel-new')navigate('experiments');
    if(action==='duplicate')useSample(state.selected);
    if(action==='source')modal('消息原文',`<span class="badge neutral">${esc(current().type)} · 虚构展示文本</span><p class="quote">${esc(current().source)}</p><p>正式版在此保留来源链接、发布时间、文本版本及提取证据。当前未绑定真实来源。</p>`, `<button class="btn" type="button" data-action="close">关闭</button><button class="btn primary" type="button" data-action="source-new">用此消息新建</button>`);
    if(action==='source-new'){dialog.close();useSample(state.selected);}
    if(action==='help')modal('关于这个设计预览',`<div class="eyebrow">MARKETMIRROR · PRODUCT CONCEPT</div><p>这是金融仿真实验平台的高保真交互原型。你可以浏览实验、拖动决策时间轴、查看三类策略、调整配置，并走完新建实验流程。</p><div class="review-box"><strong>当前可体验</strong><p>实验搜索、消息选择、分步表单、曲线切换、决策详情、主题切换与预览配置导出。</p></div><p>新建实验可提取 DeepSeek 事实并运行合成市场撮合，配置与账本保存在本机。内置示例仍用于界面展示。事实引文可核对，情景强度由用户设定。</p>`);
    if(action==='settings')modal('模型设置',`<div class="eyebrow">MODEL CONNECTION · 界面示意</div><div class="form-field"><label for="model-name">模型</label><input id="model-name" value="DeepSeek-V4-Flash-0731-W8A8" readonly></div><div class="form-field"><label for="model-endpoint">接口地址</label><input id="model-endpoint" value="http://aigw.dlut.edu.cn/v1" readonly></div><div class="review-box"><strong>后端读取本地保存的密钥</strong><p>新建实验中点击“使用 DeepSeek 提取事实”即可调用，无需每次输入。浏览器不读取密钥。</p></div><span class="badge neutral">连接状态：尚未在此页检测</span>`);
    if(action==='export'){const record=current();const data={artifact:record.custom?'MarketMirror experiment':'MarketMirror UI prototype',schema_version:'platform-export-v2',mode:record.backendResult?.mode||'design_fixture',experiment:record,selected_asset:state.asset,selected_step:state.step,fixture:record.custom?null:values()};const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`marketmirror-${record.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('已导出当前记录及其数据类型标识。');}
    if(action==='save-roles')saveStrategies();
    if(action==='retry-strategies'){hydrateStrategies();render();}
    if(action==='reset-roles'){strategyWorkspace.restoreDefaults();render();toast('已载入平台默认值，保存后生效。');}
    if(action==='next'){saveDraft();const d=state.draft,error=document.getElementById('form-error');if(state.wizard===1&&(!d.title.trim()||d.source.trim().length<20||!d.published)){error.textContent='请填写实验名称、发布时间，以及至少 20 个字的消息原文。';return;}if(state.wizard===2&&(!Number.isInteger(d.seed)||d.seed<0||d.seed>999999||!Number.isFinite(d.cash)||d.cash<10000||d.cash>100000000)){error.textContent='种子须为 0–999999 的整数；初始资金须在 1万–1亿 模型元之间。';return;}state.wizard++;render();}
    if(action==='previous'){saveDraft();state.wizard--;render();}
    if(action==='preview-run')previewRun();
  });
  document.addEventListener('input',e=>{if(e.target.id==='draft-source'){state.draft.source=e.target.value;analysisBinding.setSource(e.target.value);updateDraftAnalysisPanel();}if(e.target.id==='market-step'){state.step=Number(e.target.value);document.getElementById('market-step-label').textContent=`第 ${state.step} / ${current().backendResult.paths.with_message.trace.length} 步`;document.getElementById('market-step-cards').innerHTML=marketStepCards();}if(e.target.id==='time-slider')setStep(Number(e.target.value));if(e.target.id==='experiment-search'){state.search=e.target.value;document.getElementById('experiment-rows').innerHTML=experimentRows();}if(e.target.dataset.strategyScope){const scope=e.target.dataset.strategyScope,role=e.target.dataset.strategyRole,key=e.target.dataset.strategyKey,field=strategyFields.find(f=>f.key===key),value=Number((Number(e.target.value)/field.scale).toFixed(6));try{if(scope==='workspace'){strategyWorkspace.edit(role,key,value);document.getElementById('strategy-save-state').textContent=strategyWorkspace.dirty?'有未保存的修改':'当前参数与保存值一致';}else{state.draft.strategy_parameters[role][key]=strategyWorkspace.validate(role,key,value);}document.getElementById(e.target.id+'-value').textContent=strategyValue(key,value);}catch(error){toast(error.message);}}if(e.target.id==='draft-signal')document.getElementById('signal-value').textContent=Number(e.target.value).toFixed(2);if(e.target.id==='draft-uncertainty')document.getElementById('uncertainty-value').textContent=Number(e.target.value).toFixed(2);});
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
  window.addEventListener('hashchange',()=>{const p=location.hash.slice(1);if(labels[p]&&p!==state.page){state.page=p;render();}});
  const initial=location.hash.slice(1);if(labels[initial])state.page=initial;
  icons();render();hydrateExperiments();hydrateStrategies();
})();
