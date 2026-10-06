/* Real source text, reviewed facts and immutable research paths. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory(require('./decision-view.js').ReplayControl);else root.MarketCaseView=factory(root.MarketReplayControl);})
(typeof globalThis!=='undefined'?globalThis:this,function(replay){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const modes={no_text:'无文本',keywords:'简单关键词',reviewed_llm:'复核后 LLM',llm_asset_placebo:'暴露置换对照'};
  const roles={aggressive:'激进型',conservative:'保守型',institutional:'机构型'};
  const kinds={regulatory_constraint:'监管约束',operational_disruption:'运营中断',approval_uncertainty:'审批不确定',completed_transaction:'交易陈述',static_information:'静态背景',information_gap:'信息缺失',calendar_change:'日历安排',other:'未量化事项'};
  const statuses={in_force:'已生效',scheduled:'已宣布待实施',uncertain:'结果不确定',completed:'已完成',static:'已有状态',unknown:'时点或状态未知'};
  const segmentNames={document:'原文',question:'投资者提问',reply:'公司回复'};
  const numeric=n=>typeof n==='number'&&Number.isFinite(n);
  const value=(n,d=4)=>numeric(n)?n.toFixed(d):'—';
  const signed=n=>numeric(n)?(n>=0?'+':'')+n.toFixed(6):'—';
  const date=timestamp=>String(timestamp??'').replace('T',' ').replace(/\.999999/,'');
  const alert=message=>message?`<div class="callout" role="alert">${esc(message)}</div>`:'';
  function safeURL(url){try{const p=new URL(url);return ['https:','http:'].includes(p.protocol)?p.href:null;}catch(_){return null;}}

  class CaseState{
    constructor(){this.catalog=null;this.selectedId=null;this.loadedCaseId=null;this.mode='reviewed_llm';this.seed=7;this.step=4;this.asset='A';this.group='with_message';this.factView='reviewed';this.evidenceFocus=null;this.detail=null;this.loading=false;this.error='';this.generation=0;}
    begin(id,seed=this.seed,mode=this.mode){const resetStep=this.loadedCaseId!==id;if(resetStep)this.evidenceFocus=null;if(resetStep||seed!==this.seed||mode!==this.mode)this.detail=null;this.selectedId=id;this.seed=seed;this.mode=mode;this.loading=true;this.error='';return {id,seed,mode,resetStep,generation:++this.generation};}
    current(t){return t.generation===this.generation&&t.id===this.selectedId&&t.seed===this.seed&&t.mode===this.mode;}
    accept(t,bundle){if(!this.current(t))return false;if(bundle.case.case_id!==t.id||bundle.seed!==t.seed||bundle.mode!==t.mode)throw new Error('案例响应与当前选择不一致');this.detail=bundle;this.loadedCaseId=t.id;this.loading=false;this.error='';if(t.resetStep){const first=bundle.source_links.with_message.findIndex(r=>r.source_visible);this.step=first<0?1:first+1;}this.step=Math.min(this.step,bundle.result.paths.with_message.trace.length);return true;}
    fail(t,message){if(!this.current(t))return false;this.loading=false;this.error=message;return true;}
  }

  function renderLibrary(catalog,state){
    if(!catalog)return '<section class="panel case-empty">正在读取本机案例归档…</section>';
    if(!catalog.available)return `<section class="panel case-empty">${esc(catalog.message)}<p><a href="https://github.com/rain-lei/MarketMirror/blob/codex/research-rebuild/research/PROTOTYPE_REPRODUCTION_2026.md" target="_blank" rel="noreferrer">查看研究复现说明</a></p></section>`;
    return `<div class="case-library-grid">${catalog.cases.map(item=>`<button type="button" class="case-library-card ${item.case_id===state.selectedId?'active':''}" data-case-select="${esc(item.case_id)}" aria-pressed="${item.case_id===state.selectedId}"><span class="badge neutral">${item.stock_code?'公司回复':'政策 / 官方材料'}</span><strong>${esc(item.label)}</strong><span>${esc(item.available_at.slice(0,10))} · ${item.facts} 条事实${item.corrected_facts?` · ${item.corrected_facts} 条修订`:''}</span></button>`).join('')}</div><p class="case-help">6 个开发案例、4 种信息条件、5 个种子，共 120 条已归档路径。来源和总清单先校验，所选路径打开时核对哈希、来源时钟和账本；无新的模型请求。</p>`;
  }

  function renderControls(state){
    return `<section class="panel case-controls"><div class="form-field"><label for="case-mode">信息条件</label><select id="case-mode">${Object.entries(modes).map(([key,label])=>`<option value="${key}" ${state.mode===key?'selected':''}>${label}</option>`).join('')}</select></div><div class="form-field"><label for="case-seed">随机种子</label><select id="case-seed">${[7,11,23,47,89].map(seed=>`<option value="${seed}" ${state.seed===seed?'selected':''}>${seed}</option>`).join('')}</select></div><button type="button" class="btn" data-action="refresh-case">重新核验所选路径</button><span class="case-help">资产和步骤可在下面切换</span></section>`;
  }

  function renderSource(caseData,focus){
    const url=safeURL(caseData.source_url);
    return `<section class="panel case-source-panel"><div class="panel-header"><div><h2>来源原文</h2><p class="panel-subtitle">${esc(caseData.title)}</p></div>${url?`<a class="btn compact" href="${esc(url)}" target="_blank" rel="noreferrer">公开来源</a>`:'<span class="badge neutral">用户提供的问答工作簿</span>'}</div><div class="summary-content"><p>归档可用时间：${esc(date(caseData.available_at))}</p><p class="case-help">${caseData.visibility_precision.includes('date_only')?'来源只有日期，按北京时间日结束作为保守可用时间。':'使用归档页面时间，不等同于全渠道首次公开时间。'}A/B/C 为示意资产，股票代码仅标识原文主体。</p><details id="case-source-reader" ${focus?'open':''}><summary>展开完整原文${focus?' · 已定位引文':''}</summary>${caseData.segments.map(segment=>{let text=esc(segment.text);if(focus?.source===segment.source){const chars=Array.from(segment.text);if(chars.slice(focus.start,focus.end).join('')===focus.quote)text=esc(chars.slice(0,focus.start).join(''))+`<mark id="case-evidence-mark">${esc(focus.quote)}</mark>`+esc(chars.slice(focus.end).join(''));}return `<article class="case-source-segment"><h3>${segmentNames[segment.source]||esc(segment.source)}</h3><p>${text}</p></article>`;}).join('')}</details></div></section>`;
  }

  function renderFacts(bundle,state){
    const raw=state.factView==='raw',facts=raw?bundle.review.raw_facts:bundle.review.reviewed_record.facts;
    return `<div class="panel-header"><div><h2>模型提取与源文复核</h2><p class="panel-subtitle">DeepSeek-V4-Flash-0731-W8A8 · 单一助手复核</p></div><div class="segmented" role="group" aria-label="事实版本">${[['reviewed','复核后事实'],['raw','原始提取']].map(([key,label])=>`<button data-case-facts="${key}" class="${state.factView===key?'active':''}" aria-pressed="${state.factView===key}">${label}</button>`).join('')}</div></div><div class="summary-content"><p class="case-help">${raw?'显示当时模型的原始提取，修订前的判断可能有误。':'显示经原文复核修订后的开发案例事实。'}复核状态以原文发布时为基准，未取得独立人工金标准；此页不报告准确率。无文本和关键词条件不采用下方 LLM 事实映射。</p><div class="case-facts-list">${facts.map((fact,index)=>{const check=bundle.review.checks.find(c=>c.fact_index===index),changed=check&&Object.keys(check.changes).length>0,rule=bundle.mapping_rules[fact.kind];return `<article class="case-fact"><div class="case-fact-tags"><span class="badge neutral">${kinds[fact.kind]||esc(fact.kind)}</span><span class="badge neutral">${statuses[fact.status]||esc(fact.status)}</span>${changed?'<span class="badge orange">已修订</span>':''}</div><p>${esc(fact.claim)}</p>${fact.evidence.map((e,i)=>`<blockquote>${esc(e.quote)}<small>${segmentNames[e.source]||esc(e.source)} · 字符 [${e.start}, ${e.end}) <button class="subtle-link" data-case-quote="${index}" data-case-quote-part="${i}" data-case-quote-version="${raw?'raw':'reviewed'}">定位原文</button></small></blockquote>`).join('')}<div class="case-mapping-label">声明映射：${rule.signal===null?'未量化，排除数值贡献':`信号 ${value(rule.signal,2)} / 不确定性 ${value(rule.uncertainty,2)}`}</div><details><summary>${changed?'查看修订与理由':'查看复核理由'}</summary>${changed?Object.entries(check.changes).map(([key,change])=>`<p>${key==='status'?'状态':'陈述'}：${esc(change.raw)} → ${esc(change.reviewed)}</p>`).join(''):''}<p>${esc(check?.notes||'未存档')}</p></details></article>`;}).join('')||'<p class="case-help">未提取可供此案例使用的事实。</p>'}</div><p class="case-help">同一事实类别最多贡献一次。数值强度、持续时间及资产暴露均为预先声明的实验假设。</p></div>`;
  }

  function renderClock(bundle,state){
    const path=bundle.result.paths[state.group],day=path.trace[state.step-1],link=bundle.source_links[state.group][state.step-1],observation=day.observations[state.asset];
    const noText=state.group==='baseline'||bundle.mode==='no_text';
    const receives=!noText&&link.information_active&&state.asset===link.exposed_asset;
    let basis='';
    if(receives){
      const mapping=link.mapping;
      if(bundle.mode==='keywords')basis=`<p>登记关键词命中：${mapping.keyword_matches.map(esc).join('、')||'无'}。本条件按固定关键词规则取值，未采用 LLM 事实映射。</p>`;
      else basis=`<p>本步采用的复核事实：${mapping.used_fact_indices.map(index=>{const fact=bundle.review.reviewed_record.facts[index];return `<button type="button" class="subtle-link" data-case-quote="${index}" data-case-quote-part="0" data-case-quote-version="reviewed">${index+1}. ${esc(fact.claim)}</button>`;}).join(' · ')||'无数值贡献'}。</p>${mapping.unresolved_fact_indices.length?`<p>${mapping.unresolved_fact_indices.length} 条未量化事项保留在来源记录中，未填成数值贡献。</p>`:''}`;
      basis=`<details class="case-input-basis"><summary>查看本步来源依据</summary>${basis}<p>该来源的登记映射为信号 ${value(mapping.signal,2)} / 不确定性 ${value(mapping.uncertainty,2)}；数值和暴露均为实验假设。</p></details>`;
    }
    return `<div class="case-clock-grid"><div><small>信息截点</small><strong>${esc(day.signal_cutoff_date)}</strong></div><div><small>执行参考日</small><strong>${esc(day.execution_reference_date)}</strong></div><div><small>模型交易日</small><strong>${esc(day.trade_date)}</strong></div><div><small>原文状态</small><strong>${link.source_visible?'已可见':'尚未可见'}</strong></div></div><p class="case-help">${noText?'无文本条件，不接收原文信号。':link.information_active?`作用期内，假设暴露于资产 ${link.exposed_asset}。`:'作用期未开始或已结束，未输入新的文本脉冲。'}当前资产 ${state.asset} 实际收到信号 ${value(observation.text_signal,2)} / 不确定性 ${value(observation.text_uncertainty,2)}。先前交易与价格变化仍可能影响后续账户。</p>${basis}`;
  }

  function renderStats(bundle){
    const a=bundle.result.paths.with_message.summary,b=bundle.result.paths.baseline.summary;
    return `<section class="panel case-stats"><div class="panel-header"><h2>所选条件与无文本的完整路径对照</h2></div><div class="table-wrap" tabindex="0" aria-label="三类策略完整路径对照"><table><thead><tr><th>策略</th><th>所选条件收益</th><th>无文本收益</th><th>收益差 pp</th><th>目标改变</th><th>订单改变</th><th>成交改变</th></tr></thead><tbody>${Object.entries(roles).map(([key,label])=>{const c=bundle.comparison.role_changes[key];return `<tr><td>${label}</td><td>${signed((a.role_wealth_multiple[key]-1)*100)}%</td><td>${signed((b.role_wealth_multiple[key]-1)*100)}%</td><td>${signed((a.role_wealth_multiple[key]-b.role_wealth_multiple[key])*100)}</td><td>${c.targets_changed}/${c.decisions}</td><td>${c.requests_changed}/${c.decisions}</td><td>${c.actual_fills_changed}/${c.decisions}</td></tr>`;}).join('')}</tbody></table></div><div class="summary-content">${bundle.mode==='no_text'?'<p class="case-help">无文本条件使用同一路径自对照，差异预期为 0。</p>':''}<p class="case-help">比较每个角色的账户与步骤，改变次数涉及整个三资产组合。完整路径差异包含先前交易与价格反馈，不表示固定账户状态下的纯文本效应。</p><div class="case-order-totals">${[['所选条件',a],['无文本',b]].map(([label,data])=>`<div><strong>${label}</strong><span>请求 ${data.strategy_requested.toLocaleString('zh-CN')} / 接受 ${data.strategy_accepted.toLocaleString('zh-CN')} / 成交 ${data.strategy_filled.toLocaleString('zh-CN')} 股</span></div>`).join('')}</div></div></section>`;
  }

  function renderDetail(bundle,state,parts={}){
    if(!bundle)return alert(state.error)+`<section class="panel case-empty">${state.loading?'正在核验所选原文与两条完整路径…':'选择一个案例，查看原文与已存档实验。'}</section>`;
    const url=`/api/platform/source-cases/${encodeURIComponent(bundle.case.case_id)}/export?seed=${bundle.seed}&mode=${encodeURIComponent(bundle.mode)}`;
    const timeline=replay.render({id:'case-step',step:state.step,title:`逐步决策 · 资产 ${state.asset}`,label:'选择案例决策步',context:'归档日历 · 消息作用区间为登记假设',rows:bundle.result.paths.with_message.trace.map((d,i)=>({date:d.trade_date,visible:bundle.source_links.with_message[i].source_visible,active:bundle.source_links.with_message[i].information_active}))});
    return alert(state.error?state.error+'。保留上次读取的归档。':'')+(state.loading?'<p class="case-help" role="status">正在重新核验所选路径，以下为上次读取的归档。</p>':'')+`<div class="case-detail-heading"><div><h2>${esc(bundle.case.label)}</h2><p class="case-help">${modes[bundle.mode]} · 种子 ${bundle.seed} · ${bundle.result.audit.days_checked} 个市场日账本核验通过</p></div><div class="heading-actions"><a class="btn" href="${url}" download>导出所选归档</a><button class="btn primary" data-action="new-from-case">以此原文新建实验</button></div></div><div id="case-source-panel">${renderSource(bundle.case,state.evidenceFocus)}</div><div class="market-chart-grid">${parts.charts||''}</div><section class="panel timeline-control">${timeline}<div class="case-timeline-options"><div class="segmented" role="group" aria-label="案例资产">${['A','B','C'].map(asset=>`<button data-case-asset="${asset}" class="${state.asset===asset?'active':''}" aria-pressed="${state.asset===asset}">${asset}</button>`).join('')}</div><div class="segmented" role="group" aria-label="案例路径">${[['with_message',modes[bundle.mode]],['baseline','无文本参考']].map(([group,label])=>`<button data-case-group="${group}" class="${state.group===group?'active':''}" aria-pressed="${state.group===group}">${label}</button>`).join('')}</div></div><div id="case-observation">${renderClock(bundle,state)}</div></section><div id="case-comparison">${parts.comparison||''}</div><div class="role-editor-grid" id="case-decisions">${parts.decisionCards||''}</div>${renderStats(bundle)}<section class="panel case-facts-panel" id="case-facts">${renderFacts(bundle,state)}</section><p class="case-help">真实的是来源文本及归档日历，市场价格、投资者规则、资产暴露和数值映射为假设。这六例已用于开发，不构成独立预测或语义准确率验证。</p>`;
  }
  return {CaseState,renderLibrary,renderControls,renderDetail,renderFacts,renderSource,renderClock};
});
