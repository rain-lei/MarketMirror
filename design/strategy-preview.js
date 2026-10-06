/* Preview decisions from the backend rule; never compute replacement targets. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketStrategyPreview=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const roleKeys=['aggressive','conservative','institutional'],assets=['A','B','C'];
  const roleLabels={aggressive:'激进型',conservative:'保守型',institutional:'机构型'};
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const copy=value=>JSON.parse(JSON.stringify(value));
  const canonical=value=>JSON.stringify(value&&typeof value==='object'?Array.isArray(value)?value.map(v=>JSON.parse(canonical(v))):Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))])):value);
  const fields=[
    {key:'signal',label:'文本方向',min:-1,max:1,scale:1,step:.05},
    {key:'uncertainty',label:'消息不确定性',min:0,max:1,scale:1,step:.05},
    {key:'market',label:'共同市场信号',min:-1,max:1,scale:1,step:.05},
    {key:'volatility',label:'各资产假设日波动率',min:.001,max:.5,scale:100,step:.1}
  ];
  const presets={
    positive:{label:'正向消息',signal:.6,uncertainty:.05,volatility:.03},
    negative:{label:'负向消息',signal:-.6,uncertainty:.05,volatility:.03},
    uncertain:{label:'高不确定性',signal:0,uncertainty:.8,volatility:.03},
    risk:{label:'高波动',signal:.6,uncertainty:.05,volatility:.3}
  };
  const historyLabels={first:'首次观察 · 第 1 步',positive:'此前连续正向 2 步 · 第 4 步',negative:'此前连续负向 2 步 · 第 4 步'};
  const reasonLabels={portfolio_risk:'采用组合风险约束',institutional_concentration:'采用机构集中度约束',portfolio_allocation:'按分值分配三资产',risk_liquidation_priority:'超限优先减仓',confirmation_or_rebalance_wait:'等待连续确认或再平衡',actual_portfolio_risk_cap:'目标组合风险超限，按比例缩减',minimum_portfolio_trade:'变化小于最小调仓幅度',portfolio_turnover_cap:'达到单次调仓上限'};
  const number=(v,digits=4,signed=false)=>{if(typeof v!=='number'||!Number.isFinite(v))return '未返回';const text=Math.abs(v)>0&&Math.abs(v)<.5*10**(-digits)?v.toExponential(2):v.toFixed(digits);return signed&&v>=0?'+'+text:text;};
  const percent=v=>number(v*100,2)+'%';
  function validResult(value,input){
    if(value?.mode!=='strategy_decision_preview'||value.schema_version!=='fixed-state-decision-v1'||canonical(value.input)!==canonical(input)
      ||value.assumptions?.identical_state_before!==true||value.assumptions?.fills_simulated!==false||value.assumptions?.llm_called!==false)return false;
    return roleKeys.every(role=>['baseline','with_message'].every(name=>{
      const row=value.roles?.[role]?.[name];
      return row&&Number.isFinite(row.total_target_weight)&&row.total_target_weight>=0&&row.total_target_weight<=1+1e-9
        &&Number.isFinite(row.planned_turnover)&&row.planned_turnover>=0&&Array.isArray(row.reasons)&&row.reasons.every(v=>typeof v==='string')
        &&assets.every(a=>Number.isFinite(row.beliefs?.[a])&&Number.isFinite(row.order_weight_changes?.[a])
          &&Number.isFinite(row.current_weights?.[a])&&Number.isFinite(row.desired_weights?.[a])
          &&row.desired_weights[a]>=0&&row.desired_weights[a]<=1+1e-9
          &&['market','text','uncertainty'].every(k=>Number.isFinite(row.terms?.[a]?.[k])));
    }));
  }
  class PreviewState{
    constructor(){this.scenario={signal:.6,uncertainty:.05,market:0,volatility:.03,scope:'A',history:'first'};this.errors={};this.raw={};this.generation=0;this.status='idle';this.result=null;this.error='';}
    invalidate(message=''){this.generation++;this.result=null;this.error=message;this.status=message||Object.keys(this.errors).length?'invalid':'idle';}
    edit(key,raw){
      const field=fields.find(f=>f.key===key);let value,message='';
      if(field){value=typeof raw==='string'&&raw.trim()?Number(raw)/field.scale:NaN;if(!Number.isFinite(value)||value<field.min||value>field.max)message=`${field.label}允许范围 ${field.min*field.scale}–${field.max*field.scale}${field.scale===100?'%':''}`;}
      else{value=raw;if(key==='scope'?!['A','public'].includes(value):key==='history'?!Object.hasOwn(historyLabels,value):true)message='预览条件无效';}
      if(message){this.errors[key]=message;this.raw[key]=raw;}else{delete this.errors[key];delete this.raw[key];this.scenario[key]=value;}
      this.invalidate();return message;
    }
    preset(key){if(!presets[key])return false;const p=presets[key];this.scenario={signal:p.signal,uncertainty:p.uncertainty,market:0,volatility:p.volatility,scope:'A',history:'first'};this.errors={};this.raw={};this.invalidate();return true;}
    begin(parameters){if(Object.keys(this.errors).length||this.status==='invalid')return null;const ticket={version:this.generation,input:copy({parameters,scenario:this.scenario})};this.status='loading';this.error='';return ticket;}
    accept(ticket,result){if(ticket.version!==this.generation)return false;if(!validResult(result,ticket.input)){this.fail(ticket,'返回的预览与当前参数不一致或缺少决策字段');return false;}this.result=result;this.status='ready';return true;}
    fail(ticket,message){if(ticket.version!==this.generation)return false;this.result=null;this.status='error';this.error=message;return true;}
  }
  function status(state){return state.status==='ready'?'当前参数已计算 · 有消息与无文本共享决策前状态':state.status==='loading'?'正在按当前参数重新计算…':state.status==='invalid'?state.error||Object.values(state.errors)[0]:state.status==='error'?'预览未完成：'+state.error:'准备计算当前参数…';}
  function renderControls(state){
    return `<section class="panel strategy-preview-controls"><div class="panel-header"><div><h2>同状态决策预览</h2><p class="panel-subtitle">选择一个情景，观察下面三类策略怎样响应。修改策略参数后自动重算。</p></div><span class="badge neutral">现有决策引擎</span></div><div class="strategy-preview-body"><div class="strategy-preview-presets" role="group" aria-label="决策预览情景">${Object.entries(presets).map(([key,p])=>`<button class="btn compact" type="button" data-preview-preset="${key}">${p.label}</button>`).join('')}</div><div class="strategy-preview-fields">${fields.map(f=>{const id='preview-'+f.key;return `<div class="form-field"><label for="${id}">${f.label}${f.scale===100?'（%）':''}</label><input id="${id}" data-preview-field="${f.key}" type="number" min="${f.min*f.scale}" max="${f.max*f.scale}" step="${f.step}" value="${esc(state.raw[f.key]??Number((state.scenario[f.key]*f.scale).toPrecision(15)))}" aria-invalid="${Boolean(state.errors[f.key])}" aria-describedby="strategy-preview-status" required></div>`;}).join('')}<div class="form-field"><label for="preview-scope">消息影响范围</label><select id="preview-scope" data-preview-field="scope">${[['A','仅资产 A'],['public','全部三资产']].map(([k,v])=>`<option value="${k}" ${state.scenario.scope===k?'selected':''}>${v}</option>`).join('')}</select></div><div class="form-field"><label for="preview-history">共同观察状态</label><select id="preview-history" data-preview-field="history">${Object.entries(historyLabels).map(([k,v])=>`<option value="${k}" ${state.scenario.history===k?'selected':''}>${v}</option>`).join('')}</select></div></div><p class="strategy-preview-assumption">每类账户净资产 100 万元，现金 85%，A / B / C 各持仓 5%。两条件使用相同价格、独立资产波动和决策前记忆；观察状态为假设。</p><div class="strategy-preview-status-row"><p id="strategy-preview-status" class="strategy-preview-status ${state.status}" role="status" aria-live="polite">${esc(status(state))}</p><button class="subtle-link" type="button" data-preview-retry>重新计算</button></div></div></section>`;
  }
  function renderRole(state,role){
    if(state.status!=='ready'||!state.result)return `<div class="strategy-rule-preview placeholder"><span class="decision-stage-label">${roleLabels[role]} · 决策预览</span><p>${esc(status(state))}</p></div>`;
    const pair=state.result.roles[role],row=pair.with_message,baseline=pair.baseline;
    const buy=assets.some(a=>row.order_weight_changes[a]>1e-12),sell=assets.some(a=>row.order_weight_changes[a]<-1e-12),action=buy&&sell?'调整配置':buy?'拟加仓':sell?'拟减仓':'等待';
    return `<div class="strategy-rule-preview"><div class="strategy-preview-role-heading"><span class="decision-stage-label">${roleLabels[role]} · 决策预览</span><span class="badge neutral">${action}</span></div><div class="strategy-preview-score"><strong>${number(row.beliefs.A,4,true)}</strong><span>资产 A 判断分值</span></div><p class="strategy-preview-terms">市场 ${number(row.terms.A.market,3,true)} · 文本 ${number(row.terms.A.text,3,true)} · 不确定性 ${number(row.terms.A.uncertainty,3,true)}</p><div class="strategy-preview-target"><span>组合目标股票权重</span><strong>${percent(row.total_target_weight)}</strong></div><div class="strategy-preview-weight-bar" role="img" aria-label="目标股票权重 ${percent(row.total_target_weight)}，其余为现金">${assets.map(a=>`<span class="asset-${a}" style="width:${row.desired_weights[a]*100}%" title="资产 ${a} · ${percent(row.desired_weights[a])}"></span>`).join('')}</div><p class="strategy-preview-baseline">同状态无文本 ${percent(baseline.total_target_weight)} <span>目标差 ${number((row.total_target_weight-baseline.total_target_weight)*100,4,true)} pp</span></p><div class="strategy-preview-assets"><table><thead><tr><th>资产</th><th>目标</th><th>拟调整</th></tr></thead><tbody>${assets.map(a=>`<tr><th scope="row">${a}</th><td>${percent(row.desired_weights[a])}</td><td>${number(row.order_weight_changes[a]*100,2,true)} pp</td></tr>`).join('')}</tbody></table></div><p class="strategy-preview-turnover">拟调仓幅度 <strong>${number(row.planned_turnover*100,2)} pp</strong></p><div class="strategy-preview-reasons">${row.reasons.map(r=>`<span>${esc(reasonLabels[r]||r)}</span>`).join('')}</div><p class="strategy-preview-limit">仅计算目标与拟调仓量；实际订单和成交需运行完整实验。</p></div>`;
  }
  return {PreviewState,renderControls,renderRole,status,validResult};
});
