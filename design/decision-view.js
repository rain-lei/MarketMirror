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
