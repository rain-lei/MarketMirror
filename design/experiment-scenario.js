/* Optional full-run assumptions; old drafts retain their original payload. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketExperimentScenario=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const defaults={scope:'A',market:0,volatility:.01};
  const copy=value=>JSON.parse(JSON.stringify(value));
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const numeric=value=>typeof value==='number'&&Number.isFinite(value);
  const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const roles=['aggressive','conservative','institutional'],names=['激进型','保守型','机构型'];
  const display=value=>numeric(value)?String(Number(value.toPrecision(12))):'未填写';
  const scopeName=value=>value==='public'?'全部三资产':'仅资产 A';
  const histories={first:'首次观察 · 第 1 步',positive:'此前连续正向 2 步 · 第 4 步',negative:'此前连续负向 2 步 · 第 4 步'};
  function validate(value){
    return exact(value,['scope','market','volatility'])&&['A','public'].includes(value.scope)
      &&numeric(value.market)&&value.market>=-1&&value.market<=1
      &&numeric(value.volatility)&&value.volatility>=.001&&value.volatility<=.5;
  }
  function validReference(value){
    if(!exact(value,['parameters','scenario','mechanism_config_sha256'])||typeof value.mechanism_config_sha256!=='string'||!/^[a-f0-9]{64}$/.test(value.mechanism_config_sha256))return false;
    const p=value.parameters,s=value.scenario;
    return exact(p,roles)&&roles.every(role=>exact(p[role],['text_sensitivity','base_weight','risk_budget'])&&Object.values(p[role]).every(numeric))
      &&exact(s,['signal','uncertainty','market','volatility','scope','history'])&&numeric(s.signal)&&s.signal>=-1&&s.signal<=1
      &&numeric(s.uncertainty)&&s.uncertainty>=0&&s.uncertainty<=1&&Object.hasOwn(histories,s.history)
      &&validate({scope:s.scope,market:s.market,volatility:s.volatility});
  }
  const resolve=draft=>draft.market_assumptions??copy(defaults);
  function matches(a,b){
    if(a==null||b==null)return a==null&&b==null;
    if(validate(a)&&validate(b))return Object.keys(defaults).every(key=>a[key]===b[key]);
    if(!validReference(a)||!validReference(b))return false;
    return a.mechanism_config_sha256===b.mechanism_config_sha256&&Object.keys(a.scenario).every(key=>a.scenario[key]===b.scenario[key])
      &&roles.every(role=>Object.keys(a.parameters[role]).every(key=>a.parameters[role][key]===b.parameters[role][key]));
  }
  function fromPreview(state){
    if(state.status!=='ready'||!state.result)return null;
    const result=state.result,reference={...copy(result.input),mechanism_config_sha256:result.mechanism_config_sha256};
    if(!validReference(reference))return null;
    const s=reference.scenario;
    return {signal:s.signal,uncertainty:s.uncertainty,strategy_parameters:copy(reference.parameters),
      market_assumptions:{scope:s.scope,market:s.market,volatility:s.volatility},preview_reference:reference};
  }
  function renderFields(draft){
    const a=resolve(draft);
    return `<div class="experiment-market-fields"><div class="form-field"><label for="draft-scope">消息影响范围</label><select id="draft-scope" data-assumption="scope">${[['A','仅资产 A'],['public','全部三资产']].map(([k,v])=>`<option value="${k}" ${a.scope===k?'selected':''}>${v}</option>`).join('')}</select><small>消息从第 5 步进入，持续期内向所选资产提供文本输入。</small></div><div class="form-field"><label for="draft-market">共同市场偏移</label><input id="draft-market" data-assumption="market" type="number" min="-1" max="1" step="any" value="${esc(a.market)}" required><small>−1 至 +1；从第 1 步作用于两组全部资产，与各账户的价格反馈叠加。</small></div><div class="form-field"><label for="draft-volatility">各资产日波动率下限（%）</label><input id="draft-volatility" data-assumption="volatility" type="number" min="0.1" max="50" step="any" value="${esc(numeric(a.volatility)?Number((a.volatility*100).toPrecision(15)):'')}" required><small>0.1% 至 50%；协方差还会随价格历史更新。预览中的假设波动率在这里作为下限使用。</small></div></div>`;
  }
  function renderSignalFields(draft){
    return `<div class="form-two">${[['signal','情景方向与强度',-1,1,'−1 负向 · 0 中性 · +1 正向'],['uncertainty','信息不确定性',0,1,'0 低不确定性 · 1 高不确定性']].map(([key,label,min,max,hint])=>{
      const v=draft[key],fill=numeric(v)?(v-min)/(max-min)*100:0;
      return `<div class="form-field scenario-parameter" style="--role:var(--teal)"><label for="draft-${key}">${label}</label><div class="parameter-input-row"><div class="parameter-slider-wrap"><input class="parameter-range" type="range" data-signal-slider="${key}" min="${min}" max="${max}" step=".001" value="${esc(v)}" style="--parameter-fill:${fill}%" aria-label="调节${label}"><div class="parameter-bounds"><span>${min}</span><span>${max}</span></div></div><div class="parameter-number-wrap"><input id="draft-${key}" data-draft="${key}" data-scenario-number="${key}" type="number" min="${min}" max="${max}" step="any" value="${esc(v)}" required></div></div><small>${hint}；也可以输入精确数值。</small></div>`;
    }).join('')}</div>`;
  }
  function syncSignalInput(input,document){
    const key=input.dataset.signalSlider??input.dataset.scenarioNumber,min=key==='signal'?-1:0;
    const value=input.value.trim()?Number(input.value):NaN,valid=numeric(value)&&value>=min&&value<=1;
    input.setAttribute('aria-invalid',valid?'false':'true');
    if(valid){
      const number=document.getElementById('draft-'+key),slider=document.querySelector(`[data-signal-slider="${key}"]`);
      if(number&&number!==input){number.value=input.value;number.setAttribute('aria-invalid','false');}
      if(slider){if(slider!==input)slider.value=String(value);slider.style.setProperty('--parameter-fill',`${(value-min)/(1-min)*100}%`);}
    }
    return value;
  }
  function renderSummary(record){
    const a=resolve(record);
    return `<p class="experiment-market-summary">消息范围：${scopeName(a.scope)} · 共同市场偏移 ${esc(display(a.market))} · 日波动率下限 ${esc(display(a.volatility*100))}%</p>`;
  }
  function renderReference(record){
    const ref=record.preview_reference;if(ref==null)return '';
    if(!validReference(ref))return '<div class="review-box" role="alert">预览参考信息不完整，请重新计算并创建实验。</div>';
    const s=ref.scenario,a=resolve(record),snapshot=record.decision_preview;
    const changed=record.signal!==s.signal||record.uncertainty!==s.uncertainty||!matches(a,{scope:s.scope,market:s.market,volatility:s.volatility})
      ||roles.some(role=>Object.keys(ref.parameters[role]).some(key=>record.strategy_parameters?.[role]?.[key]!==ref.parameters[role][key]));
    return `<details class="experiment-preview-reference"><summary>同状态预览参考${changed?' · 当前实验已调整':''}</summary><p>原预览：信号 ${esc(display(s.signal))} · 不确定性 ${esc(display(s.uncertainty))} · ${scopeName(s.scope)} · 市场 ${esc(display(s.market))} · 波动率 ${esc(display(s.volatility*100))}%</p><p>预览观察记忆：${histories[s.history]}。预览每类使用一个净资产 100 万元、现金 85% 的固定账户。</p><p>完整实验从第 1 步重新积累观察记忆，每类 4 个账户，以本次设置的初始现金和库存开始撮合；第 5 步接收文本。预览的固定账户与人工记忆保留为核对参考。</p>${snapshot?.roles?`<div class="table-wrap"><table><thead><tr><th>预览目标股票权重</th><th>有消息</th><th>同状态无文本</th></tr></thead><tbody>${roles.map((role,i)=>`<tr><th scope="row">${names[i]}</th>${['with_message','baseline'].map(k=>`<td>${esc(numeric(snapshot.roles[role]?.[k]?.total_target_weight)?(snapshot.roles[role][k].total_target_weight*100).toFixed(2):'未存档')}%</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p>提交时会由本地决策引擎重建并保存这一预览快照，连同本次配置导出。</p>'}</details>`;
  }
  return {defaults,validate,validReference,resolve,matches,fromPreview,renderFields,renderSignalFields,syncSignalInput,renderSummary,renderReference};
});
