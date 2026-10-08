/* Derive role portfolio metrics from saved closing accounts and auction orders. */
(function(root,factory){
  const api=factory(typeof module==='object'&&module.exports?require('./decision-view.js'):root.MarketDecisionView);
  if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketPortfolioMetrics=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(decisions){
  'use strict';
  const roles=['aggressive','conservative','institutional'];
  const names={aggressive:'激进型',conservative:'保守型',institutional:'机构型'};
  const colors={aggressive:'orange',conservative:'blue',institutional:'purple'};
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const integer=v=>Number.isSafeInteger(v)&&v>=0;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=(v,d=4,signed=false)=>decisions.formatNumber(v,d,signed);
  const percent=v=>finite(v)?number(v*100)+'%':'未存档';
  const empty=role=>({role,accountCount:null,sessions:null,initialWealthMinor:null,wealthSeries:null,
    returnFraction:null,maxDrawdown:null,feesMinor:null,requested:null,accepted:null,filled:null,
    requestedFillFraction:null,acceptedFillFraction:null,riskExceedance:null,concentrationExceedance:null,issues:[]});

  function closingState(auction,accountName,assets){
    const account=auction?.accounts?.[accountName],calls=auction?.asset_calls;
    if(!object(account?.wallets)||!Object.keys(account.wallets).length||!object(account.shares)
      ||Object.keys(account.shares).length!==assets.length||!Object.values(account.wallets).every(integer))return null;
    const values=assets.map(asset=>{
      const shares=account.shares[asset],price=calls?.[asset]?.price_after_minor;
      return integer(shares)&&integer(price)&&price>0&&integer(shares*price)?shares*price:null;
    });
    if(values.some(v=>v===null))return null;
    const nav=Object.values(account.wallets).reduce((sum,v)=>sum+v,0)+values.reduce((sum,v)=>sum+v,0);
    return integer(nav)&&nav>0?{nav,weights:Object.fromEntries(assets.map((a,i)=>[a,values[i]/nav]))}:null;
  }

  function riskExceeded(state,covariance,parameters,assets){
    if(!state||!object(covariance)||!finite(parameters?.risk_budget)||parameters.risk_budget<=0
      ||!finite(parameters?.max_weight)||parameters.max_weight<0)return null;
    let variance=0;
    for(const a of assets)for(const b of assets){
      const value=covariance[a]?.[b];if(!finite(value))return null;
      variance+=state.weights[a]*value*state.weights[b];
    }
    if(!finite(variance))return null;
    const risk=Math.sqrt(Math.max(0,variance));
    return risk>parameters.risk_budget+1e-9||Object.values(state.weights).reduce((sum,v)=>sum+v,0)>parameters.max_weight+1e-9;
  }

  function buildPath(path){
    const output=roles.map(empty),specs=path?.participant_specs,summary=path?.summary,trace=path?.trace;
    const assets=summary?.assets;
    if(!object(specs)||!Array.isArray(trace)||!Number.isSafeInteger(summary?.sessions)||summary.sessions<1
      ||trace.length!==summary.sessions||!Array.isArray(assets)||!assets.length||new Set(assets).size!==assets.length
      ||!assets.every(a=>typeof a==='string'&&a.length)){
      output.forEach(row=>row.issues.push('完整账户、资产或步骤账本未存档。'));return output;
    }
    if(!object(summary.accounts)||Object.keys(specs).length!==Object.keys(summary.accounts).length
      ||Object.entries(specs).some(([name,s])=>!object(s)||!['strategy','background'].includes(s.kind)
      ||summary.accounts[name]?.kind!==s.kind||s.kind==='strategy'&&(!roles.includes(s.parameters?.role)||summary.accounts[name]?.role!==s.parameters.role))){
      output.forEach(row=>row.issues.push('参与账户的角色信息不完整，未计算角色合计。'));return output;
    }
    for(const row of output){
      const accountNames=Object.keys(specs).filter(n=>specs[n].kind==='strategy'&&specs[n].parameters.role===row.role);
      if(!accountNames.length){row.issues.push('此角色没有可核对的账户。');continue;}
      row.accountCount=accountNames.length;row.sessions=trace.length;
      const selected=new Set(accountNames),initials=accountNames.map(n=>summary.accounts?.[n]?.initial_wealth_minor);
      const initial=initials.every(v=>integer(v)&&v>0)?initials.reduce((sum,v)=>sum+v,0):null;
      row.initialWealthMinor=integer(initial)&&initial>0?initial:null;
      let wealthValid=row.initialWealthMinor!==null,quantityValid=true,feeValid=true,riskValid=true,concentrationValid=true;
      const series=wealthValid?[{step:0,wealthMinor:initial}]:[];
      const riskBreaches=[],concentrationBreaches=[];
      let requested=0,accepted=0,filled=0,fees=0;
      for(const [index,day] of trace.entries()){
        const auction=day?.portfolio_auction,states=Object.fromEntries(accountNames.map(n=>[n,closingState(auction,n,assets)]));
        const validStates=accountNames.every(n=>states[n]);
        if(validStates){
          const wealth=accountNames.reduce((sum,n)=>sum+states[n].nav,0);
          if(!integer(wealth))wealthValid=false;else series.push({step:index+1,wealthMinor:wealth});
        }else wealthValid=false;
        const riskAccounts=[],concentrationAccounts=[];
        for(const name of accountNames){
          const state=states[name],risk=riskExceeded(state,day?.covariance,specs[name].parameters,assets);
          if(risk===null)riskValid=false;else if(risk)riskAccounts.push(name);
          const cap=day?.decisions?.[name]?.asset_weight_cap;
          if(!state||!finite(cap)||cap<=0||cap>1)concentrationValid=false;
          else if(Object.values(state.weights).some(w=>w>cap+1e-9))concentrationAccounts.push(name);
        }
        if(riskAccounts.length)riskBreaches.push({step:index+1,accounts:riskAccounts});
        if(concentrationAccounts.length)concentrationBreaches.push({step:index+1,accounts:concentrationAccounts});
        for(const asset of assets){
          const orders=auction?.asset_calls?.[asset]?.orders;
          if(!Array.isArray(orders)){quantityValid=false;feeValid=false;continue;}
          for(const order of orders){
            if(!object(order)||!Object.hasOwn(specs,order.owner)){quantityValid=false;feeValid=false;continue;}
            if(!selected.has(order.owner))continue;
            if(!integer(order.quantity)||!integer(order.accepted_quantity)||!integer(order.filled_quantity)
              ||order.filled_quantity>order.accepted_quantity||order.accepted_quantity>order.quantity)quantityValid=false;
            else {requested+=order.quantity;accepted+=order.accepted_quantity;filled+=order.filled_quantity;}
            if(!integer(order.fee_minor))feeValid=false;else fees+=order.fee_minor;
          }
        }
      }
      if(wealthValid&&accountNames.some(n=>summary.accounts?.[n]?.final_wealth_minor!==closingState(trace.at(-1).portfolio_auction,n,assets).nav)){
        wealthValid=false;row.issues.push('期末账户汇总与末步账本不一致。');
      }
      if(wealthValid){
        row.wealthSeries=series;row.returnFraction=series.at(-1).wealthMinor/initial-1;
        let peak=initial,drawdown=0;
        for(const point of series){peak=Math.max(peak,point.wealthMinor);drawdown=Math.max(drawdown,1-point.wealthMinor/peak);}
        row.maxDrawdown=drawdown;
      }else row.issues.push('初始或逐步收盘净值无法核对，收益与组合回撤未计算。');
      if(quantityValid&&[requested,accepted,filled].every(integer)){
        Object.assign(row,{requested,accepted,filled,requestedFillFraction:requested>0?filled/requested:null,
          acceptedFillFraction:accepted>0?filled/accepted:null});
      }else row.issues.push('订单数量缺失或不一致，成交比例未计算。');
      if(feeValid&&integer(fees))row.feesMinor=fees;else row.issues.push('账户订单费用未完整存档。');
      const diagnostic=breaches=>({accountSteps:breaches.reduce((sum,b)=>sum+b.accounts.length,0),
        totalAccountSteps:accountNames.length*trace.length,stepCount:breaches.length,totalSteps:trace.length,breaches});
      if(riskValid)row.riskExceedance=diagnostic(riskBreaches);else row.issues.push('风险预算、总仓位上限或协方差未完整存档。');
      if(concentrationValid)row.concentrationExceedance=diagnostic(concentrationBreaches);else row.issues.push('单资产集中度上限或收盘持仓未完整存档。');
    }
    return output;
  }

  function buildOverview(result){
    return {schema:'portfolio-risk-overview-v1',units:{wealth:'minor_model_currency',fees:'minor_model_currency',quantity:'shares',returns:'fraction',exceedance:'account_steps'},
      sourceResultId:result?.provenance?.result_id??null,
      sourceProvenance:object(result?.provenance)?JSON.parse(JSON.stringify(result.provenance)):null,
      sourceMechanismSha256:result?.mechanism_config_sha256??null,
      sourceStrategyParametersSha256:result?.strategy_parameters_sha256??null,
      sourceTraceSha256:{with_message:result?.paths?.with_message?.summary?.trace_sha256??null,baseline:result?.paths?.baseline?.summary?.trace_sha256??null},
      paths:{with_message:buildPath(result?.paths?.with_message),baseline:buildPath(result?.paths?.baseline)}};
  }

  function renderChart(active,baseline,activeLabel,baselineLabel){
    if(!active.wealthSeries||!baseline.wealthSeries)return '<p class="risk-chart-missing">两组完整净值未存档，未绘制对照曲线。</p>';
    const paths=[active,baseline].map(row=>row.wealthSeries.map(p=>p.wealthMinor/row.initialWealthMinor)),values=paths.flat();
    const span=Math.max(...values)-Math.min(...values),padding=Math.max(span*.15,.0001),low=Math.min(...values)-padding,high=Math.max(...values)+padding;
    const x=(i,n)=>28+i/(n-1)*284,y=v=>18+(high-v)/(high-low)*94;
    const lines=paths.map((values,i)=>`<polyline class="risk-nav-line ${i?'baseline':''}" points="${values.map((v,j)=>`${x(j,values.length).toFixed(2)},${y(v).toFixed(2)}`).join(' ')}"/>`).join('');
    return `<svg class="risk-nav-chart" viewBox="0 0 340 134" role="img" aria-label="${esc(activeLabel)}与${esc(baselineLabel)}的组合净值，初始值为 1"><path class="risk-chart-grid" d="M28 18H312M28 65H312M28 112H312"/><text x="28" y="11">${number(high,5)}</text><text x="28" y="129">初始</text><text x="312" y="129" text-anchor="end">期末</text>${lines}</svg>`;
  }

  function renderOverview(result,{activeLabel='有消息',baselineLabel='无消息'}={}){
    const model=buildOverview(result),aLabel=esc(activeLabel),bLabel=esc(baselineLabel);
    const exceed=v=>v?`<strong class="${v.accountSteps?'warning-text':''}">${v.accountSteps} / ${v.totalAccountSteps}</strong><small>${v.stepCount} / ${v.totalSteps} 步有超限</small>`:'未存档';
    const fill=row=>row.requested===0?'未下单':row.requestedFillFraction===null?'未存档':percent(row.requestedFillFraction);
    return `<section class="panel portfolio-risk-overview"><div class="panel-header"><div><h2>三类策略 · 收益、风险与执行</h2><p class="panel-subtitle">整个三资产组合 · 全程账本 · 收益已含费用</p></div><button class="btn compact" type="button" data-action="export-risk-overview">导出指标 JSON</button></div><div class="risk-role-grid">${roles.map((role,index)=>{
      const active=model.paths.with_message[index],baseline=model.paths.baseline[index],delta=finite(active.returnFraction)&&finite(baseline.returnFraction)?(active.returnFraction-baseline.returnFraction)*100:null;
      const metrics=[['组合最大回撤',percent(active.maxDrawdown),percent(baseline.maxDrawdown)],
        ['账户费用 · 模型元',number(finite(active.feesMinor)?active.feesMinor/100:null,2),number(finite(baseline.feesMinor)?baseline.feesMinor/100:null,2)],
        ['成交 / 请求',fill(active),fill(baseline)],['风险 / 总仓位超限',exceed(active.riskExceedance),exceed(baseline.riskExceedance)],
        ['单资产集中度超限',exceed(active.concentrationExceedance),exceed(baseline.concentrationExceedance)]];
      const jumps=[['with_message',active,activeLabel],['baseline',baseline,baselineLabel]].flatMap(([group,row,label])=>{
        const steps=[...(row.riskExceedance?.breaches||[]),...(row.concentrationExceedance?.breaches||[])].map(b=>b.step);
        return steps.length?[`<button type="button" class="subtle-link" data-risk-step="${Math.min(...steps)}" data-risk-group="${group}">${esc(label)}首个超限 · 第 ${Math.min(...steps)} 步</button>`]:[];
      });
      const issues=[...new Set([...active.issues,...baseline.issues])];
      return `<article class="risk-role" data-risk-role="${role}" style="--role:var(--${colors[role]})"><div class="risk-role-heading"><h3><i aria-hidden="true"></i>${names[role]}</h3><span>${active.accountCount===null?'账户未存档':active.accountCount+' 个账户'}</span></div><div class="risk-return-pair"><div><small>${aLabel}收益</small><strong>${finite(active.returnFraction)?number(active.returnFraction*100,4,true)+'%':'未存档'}</strong></div><div><small>${bLabel}收益</small><strong>${finite(baseline.returnFraction)?number(baseline.returnFraction*100,4,true)+'%':'未存档'}</strong></div></div><p class="risk-return-delta">收益差 ${number(delta,6,true)}${finite(delta)?' pp（百分点）':''}</p>${renderChart(active,baseline,activeLabel,baselineLabel)}<div class="risk-chart-legend"><span><i></i>${aLabel}</span><span><i class="baseline"></i>${bLabel}</span><small>组合净值 · 初始 1</small></div><div class="risk-metric-table"><table><thead><tr><th>指标</th><th>${aLabel}</th><th>${bLabel}</th></tr></thead><tbody>${metrics.map(([label,a,b])=>`<tr><th scope="row">${label}</th><td>${a}</td><td>${b}</td></tr>`).join('')}</tbody></table></div>${jumps.length?`<div class="risk-step-links">${jumps.join('')}</div>`:''}${issues.length?`<details class="risk-missing-details"><summary>部分指标无法核对</summary>${issues.map(text=>`<p>${esc(text)}</p>`).join('')}</details>`:''}</article>`;
    }).join('')}</div><details class="risk-method"><summary>指标计算方式与超限含义</summary><p>组合净值按该类账户的现金与全部资产收盘市值合计，初始净值包含初始库存。最大回撤从初始净值及各步收盘净值的历史最高点计算，不是各账户回撤的平均值；收益和费用均来自归档。</p><p>成交比例的分母为全程请求股数，包括未被接受的请求。没有请求时显示“未下单”，不填成 0% 或 100%。费用只合计该角色订单，不含背景账户。</p><p>超限计数为收盘后超过当步协方差下的风险预算、总仓位上限，或单资产集中度上限的账户 × 步；另列至少一个账户超限的步骤数。同一账户可在多步超限。未成交的减仓请求可能使收盘持仓继续超限，账本审计通过不代表没有超限。</p><p>两组净值各自从 1 开始，反映此前成交和价格反馈。这里是预设规则与合成情景的描述性结果，不评价真实市场预测能力。</p></details></section>`;
  }
  return {buildPath,buildOverview,renderOverview};
});
