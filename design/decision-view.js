/* Read archived execution data; never substitute the experiment's input knobs. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MarketDecisionView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const roleKeys = ['aggressive', 'conservative', 'institutional'];
  const roleNames = {aggressive:'激进型', conservative:'保守型', institutional:'机构型'};
  const roleColors = {aggressive:'orange', conservative:'blue', institutional:'purple'};
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function formatNumber(value, digits = 6, signed = false) {
    if (!numeric(value)) return '未存档';
    const text = value !== 0 && Math.abs(value) < .5 * 10 ** -digits
      ? value.toExponential(2) : value.toFixed(digits);
    return (signed && value >= 0 ? '+' : '') + text;
  }
  const ruleLabels = {
    portfolio_risk: '按组合风险计算仓位上限', portfolio_allocation: '按三资产判断分配仓位',
    institutional_concentration: '检查机构单资产集中度'
  };
  const constraintLabels = {
    risk_liquidation_priority: '风险超限，优先减仓',
    confirmation_or_rebalance_wait: '等待连续确认或再平衡时点',
    actual_portfolio_risk_cap: '缩减目标仓位以满足风险预算',
    minimum_portfolio_trade: '变化小于最小交易阈值，本步不调仓',
    portfolio_turnover_cap: '按单步换手上限缩减订单'
  };
  const orderLabels = {
    trading_halted: '本步暂停交易', price_outside_model_band: '报价超出模型价格区间',
    cash_and_fee_reservation: '可用现金及费用预留限制接受量',
    sellable_inventory: '可卖库存限制接受量',
    unmatched_day_order_expired: '未撮合部分在本步结束后失效'
  };
  function describeOrder(code) { return orderLabels[code] || `未识别记录：${code}`; }
  function beliefTerms(observation, spec, archivedBelief) {
    const p = spec?.parameters, profile = spec?.profile;
    const values = [observation?.market_signal, observation?.text_signal, observation?.text_uncertainty,
      p?.market_sensitivity, p?.text_sensitivity, p?.uncertainty_aversion,
      profile?.momentum_loading, profile?.market_bias, archivedBelief];
    if (!values.every(numeric)) return null;
    // This formula is used only when it reconstructs the archived belief.
    const shock = observation.scenario_shock ?? 0;
    if (!numeric(shock)) return null;
    const market = p.market_sensitivity * Math.max(-1, Math.min(1,
      observation.market_signal * profile.momentum_loading + profile.market_bias + shock));
    const text = p.text_sensitivity * observation.text_signal;
    const uncertainty = -p.uncertainty_aversion * observation.text_uncertainty;
    if (Math.abs(market + text + uncertainty - archivedBelief) > 1e-9) return null;
    return {market, text, uncertainty};
  }
  function buildStep(result, group, asset, step) {
    if (!['with_message', 'baseline'].includes(group) || !['A', 'B', 'C'].includes(asset)
        || !Number.isInteger(step) || step < 1) throw new Error('无效的决策查看条件');
    const path = result?.paths?.[group], day = path?.trace?.[step - 1];
    if (!day) throw new Error('此步决策账本不存在');
    const observation = day.observations?.[asset] || null;
    const orders = day.portfolio_auction?.asset_calls?.[asset]?.orders;
    const decisions = day.decisions && typeof day.decisions === 'object' && !Array.isArray(day.decisions) ? day.decisions : null;
    const validDecision = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const expectedNames = Object.keys(path.participant_specs || {}).filter(name => roleKeys.includes(path.participant_specs[name]?.parameters?.role));
    const coverageComplete = decisions !== null
      && Object.keys(decisions).every(name => roleKeys.includes(path.participant_specs?.[name]?.parameters?.role) && validDecision(decisions[name]))
      && expectedNames.every(name => validDecision(decisions[name]));
    return {group, asset, step, observation, roles: roleKeys.map(role => {
      const names = Object.keys(decisions || {}).filter(name => path.participant_specs?.[name]?.parameters?.role === role && validDecision(decisions[name]));
      const accounts = names.map(name => {
        const spec = path.participant_specs[name], decision = day.decisions[name];
        return {name, parameters: spec.parameters, nav: decision.nav_minor,
          before: decision.current_weights?.[asset], target: decision.desired_weights?.[asset],
          orderChange: decision.order_weight_changes?.[asset], belief: decision.beliefs?.[asset],
          terms: beliefTerms(observation, spec, decision.beliefs?.[asset]),
          reasons: decision.reasons || []};
      });
      const selected = coverageComplete && names.length && Array.isArray(orders) ? orders.filter(o => names.includes(o.owner)) : null;
      const nav = accounts.reduce((sum, a) => sum + a.nav, 0);
      const mean = getter => coverageComplete && accounts.length && numeric(nav) && nav > 0 && accounts.every(a => numeric(getter(a)) && numeric(a.nav) && a.nav > 0)
        ? accounts.reduce((sum, a) => sum + getter(a) * a.nav, 0) / nav : null;
      const total = key => selected && selected.every(o => numeric(o[key])) ? selected.reduce((sum, o) => sum + o[key], 0) : null;
      const sideTotal = side => selected && selected.every(o => numeric(o.quantity)) ? selected.filter(o => o.side === side).reduce((sum, o) => sum + o.quantity, 0) : null;
      const reasons = [...new Set(accounts.flatMap(a => a.reasons))];
      const rules = reasons.filter(r => r in ruleLabels).map(code => ({code, label: ruleLabels[code]}));
      const constraints = reasons.filter(r => !(r in ruleLabels)).map(code => ({code,
        label: constraintLabels[code] || `未识别决策记录：${code}`,
        accounts: accounts.filter(a => a.reasons.includes(code)).length}));
      const terms = accounts.length && accounts.every(a => a.terms)
        ? Object.fromEntries(['market','text','uncertainty'].map(key => [key, mean(a => a.terms[key])])) : null;
      return {role, accounts, coverageComplete, before: mean(a => a.before), target: mean(a => a.target),
        orderChange: mean(a => a.orderChange), belief: mean(a => a.belief), terms, rules, constraints,
        orders: selected, buys: sideTotal('buy'), sells: sideTotal('sell'),
        requested: total('quantity'), accepted: total('accepted_quantity'), filled: total('filled_quantity')};
    })};
  }
  function buildComparison(result, asset, step) {
    const selected = buildStep(result, 'with_message', asset, step), baseline = buildStep(result, 'baseline', asset, step);
    const difference = (a,b) => numeric(a) && numeric(b) && numeric(a-b) ? a-b : null;
    return {asset,step,roles:selected.roles.map(a => {
      const b = baseline.roles.find(role => role.role === a.role);
      const delta = Object.fromEntries(['before','target','belief','requested','accepted','filled'].map(key => [key,difference(a[key],b[key])]));
      return {role:a.role,selected:a,baseline:b,delta};
    })};
  }
  function renderComparison(result, options = {}) {
    const {asset='A',step=1,activeLabel='有消息组',baselineLabel='无消息组'} = options;
    const view = buildComparison(result,asset,step),active = esc(activeLabel),base = esc(baselineLabel);
    const weight = value => numeric(value) ? formatNumber(value*100) + '%' : '未存档';
    const bar = (value,label,muted) => `<div class="comparison-weight"><span>${label}</span><div class="comparison-track" aria-hidden="true">${numeric(value)?`<i class="${muted?'reference':''}" style="width:${Math.max(0,Math.min(100,value*100))}%"></i>`:''}</div><strong>${weight(value)}</strong></div>`;
    return `<section class="panel decision-comparison"><div class="panel-header"><div><h2>同一步决策对照</h2><p class="panel-subtitle">资产 ${esc(asset)} · 第 ${step} 步 · ${active}与${base}</p></div></div><div class="comparison-grid">${view.roles.map(row => {
      const a=row.selected,b=row.baseline;
      const metrics=[['决策前权重',a.before,b.before,true],['判断分值',a.belief,b.belief,false],['请求股数',a.requested,b.requested,false],['接受股数',a.accepted,b.accepted,false],['成交股数',a.filled,b.filled,false]];
      return `<article class="comparison-role" data-comparison-role="${row.role}" style="--role:var(--${roleColors[row.role]})"><div class="comparison-role-heading"><span class="comparison-role-dot" aria-hidden="true"></span><h3>${roleNames[row.role]}</h3></div><p class="comparison-delta-label">目标权重差 · 百分点</p><strong class="comparison-delta">${formatNumber(numeric(row.delta.target)?row.delta.target*100:null,6,true)}</strong>${bar(a.target,active,false)}${bar(b.target,base,true)}<div class="comparison-axis" aria-hidden="true"><span>0%</span><span>100%</span></div><div class="comparison-table"><table><thead><tr><th>指标</th><th>${active}</th><th>${base}</th><th>差值</th></tr></thead><tbody>${metrics.map(([label,av,bv,percent])=>{const delta=numeric(av)&&numeric(bv)?av-bv:null,quantity=label.endsWith('股数');return `<tr><th scope="row">${label}</th><td>${percent?weight(av):formatNumber(av,quantity?0:6)}</td><td>${percent?weight(bv):formatNumber(bv,quantity?0:6)}</td><td>${formatNumber(numeric(delta)?delta*(percent?100:1):null,quantity?0:6,true)}${percent&&numeric(delta)?' pp':''}</td></tr>`;}).join('')}</tbody></table></div>${!a.coverageComplete||!b.coverageComplete?'<p class="comparison-missing">部分账户的角色信息缺失，角色合计未计算。</p>':''}</article>`;
    }).join('')}</div><p class="comparison-footnote">权重与分值按各路径的决策前净资产加权。股数为买卖合计，方向与账户依据见下方。两条路径的账户状态可能已不同，差值包含先前成交和价格反馈，不是固定状态的纯文本效应。</p></section>`;
  }
  return {buildStep, buildComparison, renderComparison, formatNumber, describeOrder};
});

/* Shared replay navigation. Information timing comes from the displayed archive. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports.ReplayControl=api;else root.MarketReplayControl=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const position=(step,total)=>total>1?(step-1)/(total-1)*100:0;
  const clamp=(step,total)=>Math.max(1,Math.min(total,Math.round(Number(step)||1)));
  function viewModel(rows,step){
    const total=Math.max(1,rows.length),current=clamp(step,total),row=rows[current-1]||{};
    const entry=rows.findIndex(r=>r.visible===true);
    const intervals=[];
    rows.forEach((r,i)=>{if(r.active===true){const last=intervals.at(-1);if(last&&last.end===i)last.end=i+1;else intervals.push({start:i+1,end:i+1});}});
    const futureActive=rows.slice(current).some(r=>r.active===true),pastActive=rows.slice(0,current).some(r=>r.active===true);
    return {total,step:current,date:row.date||'',entry:entry<0?null:entry+1,intervals,
      progress:position(current,total),completion:total?current/total*100:100,status:row.active===true?'消息作用中':row.visible===true?(futureActive?'消息已可见 · 待作用':pastActive?'消息作用已结束':'消息已可见 · 无作用输入'):row.visible===false?'消息尚未进入':'信息时点未存档',
      tone:row.active===true?'active':row.visible===true?'ended':'pending',previousDisabled:current===1,nextDisabled:current===total};
  }
  function render({id,step=1,rows=[],title='决策回放',label='选择决策步',unit='步',context=''}){
    if(!/^[a-z][a-z0-9-]*$/.test(id))throw new Error('Invalid replay control ID');
    const m=viewModel(rows,step),ticks=[...new Set([1,...Array.from({length:3},(_,i)=>Math.round(1+(m.total-1)*(i+1)/4)),m.total])];
    const bands=m.intervals.map(r=>{const half=m.total>1?50/(m.total-1):0,left=Math.max(0,position(r.start,m.total)-half),right=Math.min(100,position(r.end,m.total)+half);return `<span class="replay-window" style="left:${left}%;width:${Math.max(0,right-left)}%" title="消息作用：第 ${r.start} — ${r.end} ${esc(unit)}"></span>`;}).join('');
    const cells=rows.map((row,index)=>{
      const n=index+1,classes=[n===m.step?'current':'',row.visible===true?'visible':'',row.active===true?'active':'',n===m.entry?'entry':''].filter(Boolean).join(' ');
      const status=row.active===true?'消息作用中':row.visible===true?'消息已可见':row.visible===false?'消息尚未进入':'信息时点未存档';
      return `<button type="button" class="replay-step-cell ${classes}" data-replay-to="${n}" data-replay-cell aria-label="第 ${n} ${esc(unit)} · ${status}" title="第 ${n} ${esc(unit)}${row.date?' · '+esc(row.date):''} · ${status}" ${n===m.step?'aria-current="step"':''}><span>${n}</span></button>`;
    }).join('');
    const intervalText=m.intervals.length?m.intervals.map(r=>r.start===r.end?`第 ${r.start} ${unit}`:`第 ${r.start} — ${r.end} ${unit}`).join('、'):'本窗口无消息作用';
    return `<div class="replay-control" data-replay="${id}" data-unit="${esc(unit)}" style="--replay-progress:${m.progress}%;--replay-current:${m.progress}%;--replay-total:${m.total}"><div class="replay-heading"><div><span class="replay-eyebrow">DECISION REPLAY</span><h3>${esc(title)}</h3>${context?`<p>${esc(context)}</p>`:''}</div><div class="replay-position"><strong id="${id}-label" data-replay-position>第 ${m.step} / ${m.total} ${esc(unit)}</strong><span data-replay-date>${esc(m.date||'逐步查看决策与执行')}</span></div></div>
      <div class="replay-track"><div class="replay-rail" aria-hidden="true">${bands}<span class="replay-fill"></span>${m.entry?`<span class="replay-entry" style="left:${position(m.entry,m.total)}%"></span>`:''}<span class="replay-current"><span data-replay-current>第 ${m.step} ${esc(unit)}</span></span></div><label class="sr-only" for="${id}">${esc(label)}</label><input class="replay-range" id="${id}" type="range" min="1" max="${m.total}" step="1" value="${m.step}" aria-valuetext="第 ${m.step} ${esc(unit)}${m.date?' · '+esc(m.date):''} · ${m.status}" data-replay-input>
       <div class="replay-ticks">${ticks.map((n,i)=>`<button type="button" class="replay-tick ${i===0?'first':i===ticks.length-1?'last':''}" data-replay-to="${n}" style="left:${position(n,m.total)}%" aria-label="跳到第 ${n} ${esc(unit)}"><i></i><span>${n}</span>${rows[n-1]?.date?`<small>${esc(rows[n-1].date.slice(5))}</small>`:''}</button>`).join('')}</div><div class="replay-step-grid-wrap" aria-label="逐步刻度，可直接跳转"><div class="replay-step-grid">${cells}</div></div></div>
       <div class="replay-progress-caption" aria-live="polite"><span><strong data-replay-progress>当前进度 ${m.step} / ${m.total} ${esc(unit)}</strong><small data-replay-completion>已定位 ${Math.round(m.completion)}%</small><small class="replay-progress-help">拖动滑块或点击步骤</small></span><span class="replay-progress-note" data-replay-progress-note>${m.entry?`消息进入第 ${m.entry} ${esc(unit)} · 作用区间 ${esc(intervalText)}`:'本窗口没有已存档的消息作用区间'}</span></div>
      <div class="replay-footer"><div class="replay-legend"><span class="replay-status ${m.tone}" data-replay-status>${m.status}</span><span class="replay-window-key"><i></i>消息作用区间 · ${esc(intervalText)}</span></div><div class="replay-actions"><button class="replay-jump" type="button" data-replay-to="${m.entry||1}" ${m.entry?'':'disabled'}>${m.entry?`消息进入 · 第 ${m.entry} ${esc(unit)}`:'本窗口消息未进入'}</button><div class="replay-step-buttons"><button type="button" data-replay-delta="-1" aria-label="上一步" ${m.previousDisabled?'disabled':''}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg><span>上一步</span></button><button type="button" data-replay-delta="1" aria-label="下一步" ${m.nextDisabled?'disabled':''}><span>下一步</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 6 6 6-6 6"/></svg></button></div></div></div>
      <script type="application/json" data-replay-rows>${JSON.stringify(rows).replace(/</g,'\\u003c')}</script></div>`;
  }
  function sync(control,step){
    const rows=JSON.parse(control.querySelector('[data-replay-rows]').textContent),m=viewModel(rows,step),unit=control.dataset.unit,input=control.querySelector('[data-replay-input]');
    input.value=String(m.step);input.setAttribute('aria-valuetext',`第 ${m.step} ${unit}${m.date?' · '+m.date:''} · ${m.status}`);
    control.style.setProperty('--replay-progress',`${m.progress}%`);control.style.setProperty('--replay-current',`${m.progress}%`);
    control.querySelector('[data-replay-position]').textContent=`第 ${m.step} / ${m.total} ${unit}`;
    control.querySelector('[data-replay-date]').textContent=m.date||'逐步查看决策与执行';
    const current=control.querySelector('[data-replay-current]');if(current)current.textContent=`第 ${m.step} ${unit}`;
    const progress=control.querySelector('[data-replay-progress]');if(progress)progress.textContent=`当前进度 ${m.step} / ${m.total} ${unit}`;
    const completion=control.querySelector('[data-replay-completion]');if(completion)completion.textContent=`已定位 ${Math.round(m.completion)}%`;
    const note=control.querySelector('[data-replay-progress-note]');if(note)note.textContent=m.entry?`消息进入第 ${m.entry} ${unit} · 作用区间 ${m.intervals.length?m.intervals.map(r=>r.start===r.end?`第 ${r.start} ${unit}`:`第 ${r.start} — ${r.end} ${unit}`).join('、'):'本窗口无消息作用'}`:'本窗口没有已存档的消息作用区间';
    control.querySelectorAll('[data-replay-cell]').forEach(cell=>{
      const current=Number(cell.dataset.replayTo)===m.step;
      cell.classList.toggle('current',current);
      if(current)cell.setAttribute('aria-current','step');else cell.removeAttribute('aria-current');
    });
    const strip=control.querySelector('.replay-step-grid-wrap'),selected=control.querySelector('[data-replay-cell][aria-current="step"]');
    if(strip&&selected){
      const viewport=strip.getBoundingClientRect(),box=selected.getBoundingClientRect();
      if(box.left<viewport.left)strip.scrollLeft+=box.left-viewport.left;
      else if(box.right>viewport.right)strip.scrollLeft+=box.right-viewport.right;
    }
    const status=control.querySelector('[data-replay-status]');status.textContent=m.status;status.className=`replay-status ${m.tone}`;
    control.querySelector('[data-replay-delta="-1"]').disabled=m.previousDisabled;
    control.querySelector('[data-replay-delta="1"]').disabled=m.nextDisabled;
    return m;
  }
  const boundRoots=new WeakSet();
  function bind(root){
    if(boundRoots.has(root))return;boundRoots.add(root);
    root.addEventListener('input',event=>{if(!event.target.matches?.('[data-replay-input]'))return;sync(event.target.closest('[data-replay]'),event.target.value);});
    root.addEventListener('click',event=>{
      const button=event.target.closest?.('[data-replay-to],[data-replay-delta]');if(!button||button.disabled)return;
      const control=button.closest('[data-replay]');if(!control)return;
      const input=control.querySelector('[data-replay-input]'),target=button.dataset.replayTo===undefined?Number(input.value)+Number(button.dataset.replayDelta):Number(button.dataset.replayTo);
      input.value=String(clamp(target,Number(input.max)));input.dispatchEvent(new Event('input',{bubbles:true}));
    });
  }
  return {render,viewModel,sync,bind};
});

/* Compare immutable experiment exports; never rerun or synthesize missing data. */
(function(root,factory){const api=factory(typeof module==='object'&&module.exports?module.exports:root.MarketDecisionView);if(typeof module==='object'&&module.exports)module.exports.ExperimentComparison=api;else root.MarketExperimentComparison=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(decisions){
  'use strict';
  const roles=['aggressive','conservative','institutional'],names={aggressive:'激进型',conservative:'保守型',institutional:'机构型'},assets=['A','B','C'];
  const fields=['text_sensitivity','base_weight','risk_budget'],labels={text_sensitivity:'文本敏感度',base_weight:'基础股票权重',risk_budget:'风险预算'};
  const numeric=v=>typeof v==='number'&&Number.isFinite(v),integer=v=>Number.isSafeInteger(v)&&v>=0;
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const clone=v=>JSON.parse(JSON.stringify(v));
  const hash=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v),id=v=>typeof v==='string'&&/^[a-f0-9]{32}$/.test(v);
  const canonical=v=>JSON.stringify(object(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(canonical(v[k]))])):Array.isArray(v)?v.map(x=>JSON.parse(canonical(x))):v);
  const equal=(a,b)=>canonical(a)===canonical(b);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=(v,d=4,signed=false)=>decisions.formatNumber(v,d,signed);
  const percent=v=>numeric(v)?number(v*100,2)+'%':'未存档';
  async function verifySources(records,cryptoApi=globalThis.crypto){
    if(!cryptoApi?.subtle)throw new Error('当前浏览器无法核验原文标识');
    await Promise.all(records.map(async record=>{
      if(typeof record?.source!=='string'||!hash(record.source_sha256))throw new Error('原文依据未完整存档');
      const bytes=await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(record.source));
      const digest=Array.from(new Uint8Array(bytes),v=>v.toString(16).padStart(2,'0')).join('');
      if(digest!==record.source_sha256)throw new Error('原文内容与保存的标识不一致');
    }));
  }
  function assumptions(record){return record.market_assumptions??{scope:'A',market:0,volatility:.01};}
  function validateRecord(record){
    const r=record?.backendResult,p=r?.provenance;
    if(!id(record?.id)||typeof record.title!=='string'||typeof record.source!=='string'||typeof record.type!=='string'||typeof record.published_at!=='string'||record.run_status!=='completed'
      ||r?.mode!=='synthetic_market'||r.status!=='completed'||r.audit?.passed!==true||!Number.isInteger(record.sessions)||record.sessions<1||record.sessions>60
      ||p?.experiment_id!==record.id||!id(record.result_id)||p.result_id!==record.result_id||r.audit.days_checked!==record.sessions*2)
      throw new Error('须选择运行完成、版本明确且审计通过的撮合实验');
    for(const key of ['source_sha256','strategy_parameters_sha256'])if(!hash(record[key])||p[key]!==record[key])throw new Error('原文或策略依据不完整，无法可靠比较');
    if(!hash(record.mechanism_config_sha256)||r.mechanism_config_sha256!==record.mechanism_config_sha256||r.strategy_parameters_sha256!==record.strategy_parameters_sha256)
      throw new Error('运行配置标识与实验记录不一致');
    for(const key of ['analysis_sha256','market_assumptions_sha256','decision_preview_sha256'])if(record[key]!=null&&p[key]!==record[key])throw new Error('运行依据与实验快照不一致');
    if(!equal(record.strategy_parameters,r.strategy_parameters)||!roles.every(role=>fields.every(key=>numeric(record.strategy_parameters?.[role]?.[key]))))throw new Error('三类策略参数未完整存档');
    for(const key of ['signal','uncertainty','duration','seed','cash'])if(!numeric(record[key]))throw new Error('实验条件未完整存档');
    if(![-1,1].every((bound,i)=>i?record.signal<=bound:record.signal>=bound)||record.uncertainty<0||record.uncertainty>1||![3,6,9].includes(record.duration)
      ||!Number.isInteger(record.seed)||record.seed<0||record.seed>999999||record.cash<10000||record.cash>100000000)throw new Error('实验条件超出平台允许范围');
    const a=assumptions(record);
    if(!object(a)||!['A','public'].includes(a.scope)||!numeric(a.market)||a.market< -1||a.market>1||!numeric(a.volatility)||a.volatility<.001||a.volatility>.5
      ||(record.market_assumptions!=null&&!equal(a,r.market_assumptions)))throw new Error('市场假设与运行快照不一致');
    if(!Number.isInteger(r.assumptions?.accounts_per_role)||r.assumptions.accounts_per_role<1)throw new Error('账户初始条件未存档');
    const metrics={};
    for(const group of ['baseline','with_message']){
      const path=r.paths?.[group],summary=path?.summary;
      if(!Array.isArray(path?.trace)||path.trace.length!==record.sessions||summary?.sessions!==record.sessions||!equal(summary.assets,assets)||summary.use_text!==(group==='with_message'))throw new Error('实验路径或信息条件不完整');
      const roster=Object.keys(path.participant_specs||{}).filter(n=>roles.includes(path.participant_specs[n]?.parameters?.role));
      if(!roles.every(role=>roster.filter(n=>path.participant_specs[n].parameters.role===role).length===r.assumptions.accounts_per_role))throw new Error('策略账户来源不完整');
      const totals=Object.fromEntries(roles.map(role=>[role,{requested:0,accepted:0,filled:0,initial:0,final:0}]));
      for(let step=0;step<path.trace.length;step++){
        const day=path.trace[step];
        if(!object(day.decisions)||!equal(Object.keys(day.decisions).sort(),roster.slice().sort()))throw new Error('逐步决策缺少策略账户');
        for(const name of roster){
          const spec=path.participant_specs[name],d=day.decisions[name];
          if(!fields.every(k=>spec.parameters[k]===record.strategy_parameters[spec.parameters.role][k])||!numeric(d?.nav_minor)||d.nav_minor<=0
            ||!assets.every(asset=>numeric(d.current_weights?.[asset])&&d.current_weights[asset]>=0&&d.current_weights[asset]<=1
              &&numeric(d.desired_weights?.[asset])&&d.desired_weights[asset]>=0&&d.desired_weights[asset]<=1
              &&numeric(d.order_weight_changes?.[asset])&&numeric(d.beliefs?.[asset])))throw new Error('账户决策数值或参数来源不完整');
        }
        for(const asset of assets){
          const o=day.observations?.[asset],orders=day.portfolio_auction?.asset_calls?.[asset]?.orders;
          const exposed=group==='with_message'&&(a.scope==='public'||asset==='A')&&step>=4&&step<4+record.duration;
          if(o?.text_signal!==(exposed?record.signal:0)||o?.text_uncertainty!==(exposed?record.uncertainty:0)
            ||(o?.scenario_shock??0)!==a.market||!numeric(day.covariance?.[asset]?.[asset])||day.covariance[asset][asset]+1e-15<a.volatility**2||!Array.isArray(orders))throw new Error('已存档输入或波动率与实验条件不一致');
          for(const order of orders){
            if(!integer(order.quantity)||!integer(order.accepted_quantity)||!integer(order.filled_quantity)||order.filled_quantity>order.accepted_quantity||order.accepted_quantity>order.quantity)throw new Error('订单执行字段缺失或数量不一致');
            if(roster.includes(order.owner)){
              const total=totals[path.participant_specs[order.owner].parameters.role];
              total.requested+=order.quantity;total.accepted+=order.accepted_quantity;total.filled+=order.filled_quantity;
            }else if(!path.participant_specs?.[order.owner])throw new Error('订单账户来源未存档');
          }
        }
      }
      for(const name of roster){
        const account=summary.accounts?.[name],role=path.participant_specs[name].parameters.role;
        if(account?.role!==role||!integer(account.initial_wealth_minor)||account.initial_wealth_minor<=0||!integer(account.final_wealth_minor)
          ||!object(account.wallets)||!Object.values(account.wallets).every(integer)||!assets.every(a=>integer(account.shares?.[a])&&integer(summary.final_prices_minor?.[a])))throw new Error('期末账户账本不完整');
        const wealth=Object.values(account.wallets).reduce((s,v)=>s+v,0)+assets.reduce((s,a)=>s+account.shares[a]*summary.final_prices_minor[a],0);
        if(wealth!==account.final_wealth_minor)throw new Error('期末现金、持仓与净资产不一致');
        totals[role].initial+=account.initial_wealth_minor;totals[role].final+=wealth;
      }
      for(const key of ['requested','accepted','filled'])if(roles.reduce((s,role)=>s+totals[role][key],0)!==summary['strategy_'+key])throw new Error('订单汇总与逐步账本不一致');
      for(const role of roles){
        const total=totals[role],wealth=total.final/total.initial;
        if(!numeric(summary.role_wealth_multiple?.[role])||Math.abs(summary.role_wealth_multiple[role]-wealth)>1e-12)throw new Error('策略收益汇总与期末账户不一致');
        total.return_pp=(wealth-1)*100;
      }
      metrics[group]=totals;
    }
    return metrics;
  }
  function configuration(record){
    const a=assumptions(record),r=record.backendResult;
    const roster={};
    for(const group of ['baseline','with_message'])roster[group]=Object.fromEntries(Object.entries(r.paths[group].participant_specs).map(([name,spec])=>[name,{...spec,parameters:spec.parameters?Object.fromEntries(Object.entries(spec.parameters).filter(([key])=>!fields.includes(key))):null}]));
    return {source:record.source,type:record.type,published_at:record.published_at,source_sha256:record.source_sha256,analysis_sha256:record.analysis_sha256??null,
      signal:record.signal,uncertainty:record.uncertainty,duration:record.duration,sessions:record.sessions,seed:record.seed,cash:record.cash,
      scope:a.scope,market:a.market,volatility:a.volatility,mechanism_config_sha256:record.mechanism_config_sha256,
      assumptions:record.backendResult.assumptions?Object.fromEntries(Object.entries(record.backendResult.assumptions).filter(([k])=>!['exposed_asset','common_market_offset','volatility_floor','market_offset_first_step','memory_initial_state','volatility_is_floor','price_feedback_enabled'].includes(k))):null,
      roster,initial_accounts:Object.fromEntries(Object.entries(r.paths.baseline.summary.accounts).map(([name,a])=>[name,a.initial_wealth_minor]))};
  }
  function compare(left,right){
    const leftMetrics=validateRecord(left),rightMetrics=validateRecord(right);
    if(left.id===right.id)throw new Error('请选择两份不同的实验');
    const a=configuration(left),b=configuration(right);
    const differences=Object.keys(a).filter(key=>!equal(a[key],b[key])).map(key=>({key,left:a[key],right:b[key]}));
    const parameters=roles.flatMap(role=>fields.filter(key=>left.strategy_parameters[role][key]!==right.strategy_parameters[role][key])
      .map(key=>({role,key,left:left.strategy_parameters[role][key],right:right.strategy_parameters[role][key]})));
    return {left_metrics:leftMetrics,right_metrics:rightMetrics,conditions_match:differences.length===0,differences,parameters,
      left_version:left.result_id,right_version:right.result_id};
  }
  class ComparisonState{
    constructor(){this.leftId='';this.rightId='';this.generation=0;this.status='idle';this.error='';this.left=null;this.right=null;this.comparison=null;this.asset='A';this.group='with_message';this.step=5;this.loadedAt=null;}
    select(side,value){if(!['left','right'].includes(side)||value!==''&&!id(value))return false;this[side+'Id']=value;this.invalidate();return true;}
    invalidate(){this.generation++;this.status='idle';this.error='';this.left=null;this.right=null;this.comparison=null;this.loadedAt=null;}
    swap(){[this.leftId,this.rightId]=[this.rightId,this.leftId];this.invalidate();}
    begin(){if(!this.leftId||!this.rightId)return null;if(this.leftId===this.rightId){this.status='error';this.error='请选择两份不同的实验';return null;}this.status='loading';this.error='';return {generation:this.generation,leftId:this.leftId,rightId:this.rightId};}
    accept(ticket,left,right){if(ticket.generation!==this.generation)return false;try{if(left?.id!==ticket.leftId||right?.id!==ticket.rightId)throw new Error('返回的快照不属于所选实验');const l=clone(left),r=clone(right),c=compare(l,r);this.left=l;this.right=r;this.comparison=c;this.step=Math.max(1,Math.min(this.step,left.sessions,right.sessions));this.status='ready';this.loadedAt=new Date().toISOString();return true;}catch(e){this.fail(ticket,e.message);return false;}}
    fail(ticket,message){if(ticket.generation!==this.generation)return false;this.status='error';this.error=message;this.left=null;this.right=null;this.comparison=null;return true;}
  }
  const conditionLabels={source:'消息原文',type:'消息类型',published_at:'发布时间',source_sha256:'原文标识',analysis_sha256:'事实参考',signal:'文本方向',uncertainty:'不确定性',duration:'消息持续步数',sessions:'总步数',seed:'随机种子',cash:'每类初始现金',scope:'消息影响范围',market:'共同市场偏移',volatility:'波动率下限',mechanism_config_sha256:'基础配置',assumptions:'初始条件',roster:'账户与固定规则',initial_accounts:'账户初始净资产'};
  function conditionValue(key,value){if(value==null)return '未关联';if(key==='source')return `${value.length} 字符 · ${value.slice(0,60)}${value.length>60?'…':''}`;if(key.endsWith('sha256'))return String(value).slice(0,12);if(key==='scope')return value==='public'?'全部三资产':'仅资产 A';if(key==='volatility')return percent(value);if(object(value))return '详见导出的完整快照';return String(value);}
  function renderPicker(state,records){
    const choices=records.filter(r=>!r.corrupt&&r.run_status==='completed'&&r.backendResult?.mode==='synthetic_market');
    return `<section class="panel experiment-compare-picker"><div class="panel-header"><div><h2>选择两份已完成实验</h2><p class="panel-subtitle">左侧作为基准，右侧作为待比较配置。读取的是已保存的撮合版本。</p></div></div><div class="summary-content"><div class="form-two">${[['left','基准实验',state.leftId],['right','比较实验',state.rightId]].map(([side,label,selected])=>`<div class="form-field"><label for="compare-${side}">${label}</label><select id="compare-${side}" data-comparison-select="${side}"><option value="">选择实验</option>${choices.map(r=>`<option value="${esc(r.id)}" ${selected===r.id?'selected':''}>${esc(r.title)} · ${r.sessions} 步 / 种子 ${r.seed}</option>`).join('')}</select></div>`).join('')}</div><div class="comparison-picker-actions"><span class="form-hint">${state.status==='loading'?'正在读取并核对两份快照…':choices.length<2?'至少需要两份已完成的撮合实验。':'比较不调用模型，也不重新运行市场。'}</span><div><button class="btn compact" type="button" data-comparison-action="swap" ${!state.leftId||!state.rightId?'disabled':''}>交换左右</button><button class="btn compact" type="button" data-comparison-action="refresh" ${!state.leftId||!state.rightId?'disabled':''}>刷新所选版本</button></div></div></div></section>`;
  }
  function renderStepCards(state){
    if(state.status!=='ready')return '';
    const selected=decisions.buildStep(state.left.backendResult,state.group,state.asset,state.step),candidate=decisions.buildStep(state.right.backendResult,state.group,state.asset,state.step);
    return roles.map((role,i)=>{const a=selected.roles[i],b=candidate.roles[i];return `<article class="experiment-comparison-step ${role}"><h3>${names[role]}</h3><div class="comparison-target-row"><span>基准</span><div class="comparison-weight-track"><i style="width:${a.target*100}%"></i></div><strong>${percent(a.target)}</strong></div><div class="comparison-target-row"><span>比较</span><div class="comparison-weight-track candidate"><i style="width:${b.target*100}%"></i></div><strong>${percent(b.target)}</strong></div><p>目标差 ${number((b.target-a.target)*100,6,true)} pp</p><div class="comparison-step-values"><span>分值 ${number(a.belief,4,true)} → ${number(b.belief,4,true)}</span><span>实际成交 ${a.filled} → ${b.filled} 股</span></div></article>`;}).join('');
  }
  function renderBody(state){
    if(state.status!=='ready')return `<section class="panel"><div class="summary-content comparison-empty" role="status"><h2>${state.status==='loading'?'正在核对实验依据':state.status==='error'?'这两份记录暂时无法比较':'从一次参数调整开始'}</h2><p>${esc(state.error||'先选择基准实验，再选择另一份已完成实验。也可以在结果页使用“调整策略再跑”，保持消息和市场条件继续实验。')}</p></div></section>`;
    const {left,right,comparison:c,group,asset,step}=state;
    const cfg=record=>{const a=assumptions(record);return `<article><small>${record===left?'基准实验':'比较实验'}</small><h3>${esc(record.title)}</h3><p>种子 ${record.seed} · ${record.sessions} 步 · 信号 ${record.signal} · 不确定性 ${record.uncertainty}</p><p>${a.scope==='public'?'全部三资产':'仅资产 A'} · 市场 ${a.market} · 波动下限 ${percent(a.volatility)}</p><span>结果版本 ${esc(record.result_id.slice(0,10))}</span><button class="subtle-link" type="button" data-comparison-open="${record.id}">打开完整账本 →</button></article>`;};
    return `<section class="panel comparison-conditions"><div class="panel-header"><div><h2>先核对比较条件</h2><p class="panel-subtitle">${c.conditions_match?'消息、种子、市场条件与固定账户规则一致':'两份实验的输入条件存在差异'}</p></div><span class="badge ${c.conditions_match?'neutral':'orange'}">${c.conditions_match?'条件一致':'条件有差异'}</span></div><div class="summary-content"><div class="comparison-run-cards">${cfg(left)}${cfg(right)}</div>${c.differences.length?`<div class="table-wrap comparison-config-diff"><table><thead><tr><th>不同条件</th><th>基准</th><th>比较</th></tr></thead><tbody>${c.differences.map(d=>`<tr><th>${conditionLabels[d.key]}</th><td>${esc(conditionValue(d.key,d.left))}</td><td>${esc(conditionValue(d.key,d.right))}</td></tr>`).join('')}</tbody></table></div><p class="comparison-note">下方为两次运行的描述性差异，不能只归因于策略参数。可以复制基准实验，保持这些条件后重新运行。</p>`:'<p class="comparison-note">可观察本次参数调整对应的仿真变化。价格、成交与后续仓位存在反馈；单个种子的差异不能证明普遍收益改善。</p>'}<details class="comparison-parameter-diff"><summary>策略参数变化 · ${c.parameters.length} 项</summary>${c.parameters.length?`<div class="table-wrap"><table><thead><tr><th>策略</th><th>参数</th><th>基准</th><th>比较</th></tr></thead><tbody>${c.parameters.map(d=>`<tr><td>${names[d.role]}</td><td>${labels[d.key]}</td><td>${d.key==='text_sensitivity'?number(d.left,5):percent(d.left)}</td><td>${d.key==='text_sensitivity'?number(d.right,5):percent(d.right)}</td></tr>`).join('')}</tbody></table></div>`:'<p>三类可调策略参数相同。</p>'}</details></div></section>`
      +`<section class="panel comparison-view-controls"><div class="panel-header"><div><h2>三类策略 · 全程结果</h2><p class="panel-subtitle">收益从期末现金和持仓核对，订单从逐步账本累计</p></div><div class="segmented" role="group" aria-label="实验比较信息条件">${[['with_message','有消息'],['baseline','无消息对照']].map(([key,label])=>`<button type="button" data-comparison-group="${key}" class="${group===key?'active':''}" aria-pressed="${group===key}">${label}</button>`).join('')}</div></div><div class="experiment-comparison-role-grid">${roles.map(role=>{const a=c.left_metrics[group][role],b=c.right_metrics[group][role];return `<article class="experiment-comparison-role ${role}"><h3>${names[role]}</h3><span class="comparison-metric-label">收益变化 · 比较 − 基准</span><strong class="comparison-primary-metric">${number(b.return_pp-a.return_pp,6,true)}<small> pp</small></strong><div class="comparison-return-values"><span>基准 ${number(a.return_pp,4,true)}%</span><span>比较 ${number(b.return_pp,4,true)}%</span></div><table><thead><tr><th>全程股数</th><th>基准</th><th>比较</th><th>差值</th></tr></thead><tbody>${[['requested','请求'],['accepted','接受'],['filled','成交']].map(([key,label])=>`<tr><th>${label}</th><td>${a[key].toLocaleString('zh-CN')}</td><td>${b[key].toLocaleString('zh-CN')}</td><td>${number(b[key]-a[key],0,true)}</td></tr>`).join('')}</tbody></table></article>`;}).join('')}</div></section>`
      +`<section class="panel comparison-step-panel"><div class="panel-header"><div><h2>同一步目标仓位 · 第 ${step} 步</h2><p class="panel-subtitle">按各次运行的决策前净资产加权；账户状态可能已不同</p></div><div class="segmented" role="group" aria-label="比较资产">${assets.map(a=>`<button type="button" data-comparison-asset="${a}" class="${asset===a?'active':''}" aria-pressed="${asset===a}">资产 ${a}</button>`).join('')}</div></div><div class="summary-content"><div class="comparison-step-input"><label for="comparison-step">查看决策步</label><input id="comparison-step" type="number" min="1" max="${Math.min(left.sessions,right.sessions)}" step="1" value="${step}" data-comparison-step aria-describedby="comparison-step-error"><span>共有 ${Math.min(left.sessions,right.sessions)} 步可并列查看</span></div><p id="comparison-step-error" class="form-error" role="alert"></p><div class="experiment-comparison-role-grid" data-comparison-step-cards>${renderStepCards(state)}</div></div></section>`;
  }
  function exportSnapshot(state){if(state.status!=='ready')return null;return clone({artifact:'MarketMirror experiment comparison',schema_version:'platform-comparison-v1',read_at:state.loadedAt,
    conditions_match:state.comparison.conditions_match,differences:state.comparison.differences,parameter_changes:state.comparison.parameters,metrics:{left:state.comparison.left_metrics,right:state.comparison.right_metrics},
    view:{group:state.group,asset:state.asset,step:state.step},left:state.left,right:state.right,interpretation:'descriptive_simulation_comparison'});}
  return {ComparisonState,validateRecord,verifySources,compare,assumptions,configuration,renderPicker,renderBody,renderStepCards,exportSnapshot};
});
