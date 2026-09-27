(function () {
  "use strict";
  const data = window.MARKETMIRROR_DATA;
  if (!data) { document.body.textContent = "研究摘要缺失。请先运行工作台生成命令。"; return; }
  const $ = (id) => document.getElementById(id);
  const safe = (value) => String(value).replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
  const pct = (n, digits=2) => `${(n * 100).toFixed(digits)}%`;
  const mult = (n) => Number(n).toFixed(3);
  const eventNames = {
    "asset_management_guidance_date_only":"2018 资管新规 · 5 月 2 日",
    "wuhan_date_only_conservative":"2020 武汉通告 · 日期保守 · 2 月 3 日",
    "wuhan_effective_time_upper_bound":"2020 武汉通告 · 生效代理 · 1 月 23 日"
  };
  const codes = {"000001":"平安银行 · 000001","000002":"万科 A · 000002","600519":"贵州茅台 · 600519"};
  const roles = {aggressive:"激进型", conservative:"保守型", institutional:"机构型"};
  const metrics = [
    ["问答来源行",data.overview.question_rows.toLocaleString("zh-CN"),"三份本地 Excel"],
    ["来源股票代码",data.overview.stocks.toLocaleString("zh-CN"),"身份仍待核实"],
    ["行情交易日",String(data.overview.market_sessions),data.overview.market_periods.map(p=>`${p.start.slice(0,7)}—${p.end.slice(0,7)}`).join("；")],
    ["重跑通过",`${data.overview.reexecution_passed}/${data.overview.run_total}`,"已固定清单"],
  ];
  $("metrics").innerHTML = metrics.map(([label,value,note]) => `<div class="metric"><span>${safe(label)}</span><strong>${safe(value)}</strong><small>${safe(note)}</small></div>`).join("");
  const eventIds = [...new Set(data.events.map((e) => e.event_id))];
  $("event-select").innerHTML = eventIds.map(id => `<option value="${safe(id)}">${safe(eventNames[id] || id)}</option>`).join("");
  function drawEvent() {
    const id = $("event-select").value;
    const rows = data.events.filter(e => e.event_id === id).sort((a,b) => a.stock_code.localeCompare(b.stock_code));
    const avg = rows[0].daily.map((_,i) => rows.reduce((sum,row) => sum + row.daily[i].abnormal_return,0)/rows.length);
    let cum=0; const values=avg.map(n => (cum+=n));
    const W=620,H=220,L=42,R=16,T=17,B=35;
    const min=Math.min(0,...values),max=Math.max(0,...values),pad=Math.max(.005,(max-min)*.17);
    const low=min-pad,high=max+pad;
    const x=i=>L+i*(W-L-R)/Math.max(1,values.length-1);
    const y=v=>T+(high-v)*(H-T-B)/(high-low);
    const points=values.map((v,i)=>`${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const ticks=[low,0,high];
    $("event-chart").innerHTML=`<svg viewBox="0 0 ${W} ${H}" aria-hidden="true">${ticks.map(t=>`<line class="${t===0?'zero':'gridline'}" x1="${L}" x2="${W-R}" y1="${y(t)}" y2="${y(t)}"/><text x="${L-6}" y="${y(t)+3}" text-anchor="end">${pct(t,0)}</text>`).join("")}<polyline class="series" points="${points}"/>${values.map((v,i)=>`<circle class="dot" cx="${x(i)}" cy="${y(v)}" r="3"/>`).join("")}${rows[0].daily.map((d,i)=>`<text x="${x(i)}" y="${H-8}" text-anchor="middle">${d.relative_day > 0 ? '+' : ''}${d.relative_day}</text>`).join("")}</svg>`;
    $("event-caption").textContent = `等权三股累计异常收益；窗口 [-${rows[0].window_before}, +${rows[0].window_after}]，包括事件前交易日。${eventNames[id] || id}，不能解释为政策发布后的跌幅。`;
    const activity = new Map(data.activity.filter(a=>a.event_id===id).map(a=>[a.stock_code,a]));
    $("event-table").innerHTML=`<table class="data-table"><thead><tr><th>股票</th><th>对齐日</th><th style="text-align:right">CAR</th><th style="text-align:right">成交额倍数</th></tr></thead><tbody>${rows.map(e=>`<tr><td>${safe(codes[e.stock_code]||e.stock_code)}</td><td>${safe(e.event_date)}</td><td class="num ${e.car>=0?'positive':'negative'}">${pct(e.car)}</td><td class="num">${activity.has(e.stock_code)?Number(activity.get(e.stock_code).amount_fold).toFixed(2)+'×':'—'}</td></tr>`).join("")}</tbody></table>`;
    const quotes=(data.independent_quotes||[]).filter(e=>e.event_id===id).sort((a,b)=>a.stock_code.localeCompare(b.stock_code));
    $("independent-quote-table").innerHTML=quotes.length
      ? `<table class="data-table"><thead><tr><th>股票</th><th style="text-align:right">BaoStock CAR</th><th style="text-align:right">东方财富不复权 CAR</th><th style="text-align:right">差（百分点）</th><th style="text-align:right">窗口最大日差（百分点）</th><th>估计期差异</th></tr></thead><tbody>${quotes.map(e=>`<tr><td>${safe(codes[e.stock_code]||e.stock_code)}</td><td class="num">${pct(e.baostock_car)}</td><td class="num">${pct(e.eastmoney_unadjusted_car)}</td><td class="num">${Number(e.car_difference_pp).toFixed(4)}</td><td class="num">${Number(e.event_window_max_absolute_difference_pp).toFixed(4)}</td><td>${e.estimation_basis_exception?'有大差异日':'无大差异日'}</td></tr>`).join("")}</tbody></table>`
      : "<p class=\"note\">该事件尚无通过核验的第二行情源结果。</p>";
    const placebo=data.placebo[id];
    $("placebo-summary").textContent=placebo
      ? `${eventNames[id]||id}窗口 ${placebo.blackout_start}—${placebo.blackout_end} 被隔离后，事前 ${placebo.before} 日、事后 ${placebo.after} 日可作描述性日期对照；排名不是 p 值。`
      : "该事件尚无日期对照，表中 — 表示没有成交活动结果。";
  }
  $("event-select").addEventListener("change",drawEvent);drawEvent();
  const p=data.prediction;
  const predictionBars=[["纯行情 MAE",p.market_mae,false],["行情 + 文本 MAE",p.text_mae,true]];
  $("prediction-bars").innerHTML=predictionBars.map(([label,value,alt])=>`<div class="bar-row"><div class="bar-row-head"><span>${safe(label)}</span><strong>${pct(value,3)}</strong></div><div class="bar-track"><div class="bar-fill ${alt?'alt':''}" style="width:${Math.min(100,value/.03*100).toFixed(1)}%"></div></div></div>`).join("");
  $("prediction-interval").textContent=`${p.rows} 条测试预测。配对 MAE 差值（文本 − 行情）${pct(p.paired_difference,3)}；近似 95% 区间 [${pct(p.interval_95[0],3)}, ${pct(p.interval_95[1],3)}]，包含零。`;
  const visibilityLag=data.visibility_lag_series||[];
  $("visibility-lag-table").innerHTML=visibilityLag.length
    ? `<table class="data-table"><thead><tr><th>额外延迟</th><th style="text-align:right">文本特征改变行</th><th style="text-align:right">行情 MAE</th><th style="text-align:right">行情＋文本 MAE</th><th style="text-align:right">文本－行情差</th><th>近似 95% 区间</th></tr></thead><tbody>${visibilityLag.map(row=>`<tr><td>${safe(row.lag_days)} 日</td><td class="num">${safe(row.changed_text_feature_rows)}</td><td class="num">${pct(row.market_test_mae,4)}</td><td class="num">${pct(row.text_test_mae,4)}</td><td class="num">${pct(row.paired_mae_difference,4)}</td><td>[${pct(row.paired_interval_95[0],4)}, ${pct(row.paired_interval_95[1],4)}]</td></tr>`).join("")}</tbody></table>`
    : "<p class=\"note\">尚无通过核验的公开时间敏感性结果。</p>";
  const financial=data.financial_dictionary;
  if(financial){
    const fs=financial.scope;
    $("financial-summary").innerHTML=`<strong>${safe(fs.field_count)} 个字段</strong><p>快照 ${safe(fs.snapshot_field_count)} 个；行上下文 ${safe(fs.row_context_field_count)} 个；来源行 ${Number(fs.source_rows).toLocaleString("zh-CN")} 条。全部值状态为 <code>unverified</code>。</p>`;
    $("financial-unresolved").innerHTML=`<h4>待确认含义</h4><ul>${financial.unresolved_semantics.map(item=>`<li><strong>${safe(item.key)}</strong>：${safe(item.question)}</li>`).join("")}</ul>`;
    $("financial-boundaries").innerHTML=`<div class="boundary-block"><span>可以做</span>${financial.allowed_uses.map(item=>`<p>${safe(item)}</p>`).join("")}</div><div class="boundary-block blocked"><span>当前不能做</span>${financial.blocked_uses.map(item=>`<p>${safe(item)}</p>`).join("")}</div>`;
    $("financial-source-note").textContent=`${Number(fs.source_rows).toLocaleString("zh-CN")} 条来源行 · ${safe(financial.generated_from.source_file)}`;
    $("financial-fields-table").innerHTML=`<table class="data-table"><thead><tr><th>字段</th><th>层级</th><th style="text-align:right">缺失率</th><th style="text-align:right">数值</th><th style="text-align:right">非数值</th><th>数值范围</th><th>状态</th></tr></thead><tbody>${financial.fields.map(item=>{const range=item.numeric_min===null?'—':`${Number(item.numeric_min).toPrecision(6)} … ${Number(item.numeric_max).toPrecision(6)}`;return `<tr><td>${safe(item.field_name)}</td><td>${safe(item.role)}</td><td class="num">${item.missing_rate===null?'—':pct(item.missing_rate,2)}</td><td class="num">${Number(item.numeric_count).toLocaleString("zh-CN")}</td><td class="num">${Number(item.non_numeric_count).toLocaleString("zh-CN")}</td><td>${safe(range)}</td><td><span class="status-unverified">${safe(item.verification_status)}</span></td></tr>`;}).join("")}</tbody></table>`;
  }else{
    $("financial-summary").innerHTML="<p>财务字段字典尚未生成。</p>";
  }
  const periods=[...new Set(data.replays.map(r=>r.period))];
  const stocks=[...new Set(data.replays.map(r=>r.stock_code))];
  $("period-select").innerHTML=periods.map(x=>`<option>${safe(x)}</option>`).join("");
  $("stock-select").innerHTML=stocks.map(x=>`<option value="${safe(x)}">${safe(codes[x]||x)}</option>`).join("");
  function drawReplay(){
    const rows=data.replays.filter(r=>r.period===$("period-select").value && r.stock_code===$("stock-select").value);
    $("replay-table").innerHTML=`<table class="data-table"><thead><tr><th>角色</th><th style="text-align:right">市场信号</th><th style="text-align:right">零信号</th><th style="text-align:right">买入持有</th><th style="text-align:right">最大回撤</th><th style="text-align:right">交易</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${safe(roles[r.role]||r.role)}</td><td class="num">${mult(r.signal_multiple)}</td><td class="num">${mult(r.control_multiple)}</td><td class="num">${mult(r.buyhold_multiple)}</td><td class="num">${pct(r.max_drawdown,1)}</td><td class="num">${r.trades}</td></tr>`).join("")}</tbody></table>`;
    const capacity=(data.capacity_series||[]).find(r=>r.period===$("period-select").value && r.stock_code===$("stock-select").value);
    $("capacity-table").innerHTML=capacity
      ? `<table class="data-table"><thead><tr><th>假设资金</th><th style="text-align:right">事后参与比例 95 分位</th><th style="text-align:right">触及上限</th><th style="text-align:right">请求额完成比例</th><th style="text-align:right">激进型无限制</th><th style="text-align:right">激进型受限</th></tr></thead><tbody><tr><td>10 亿元 / Agent</td><td class="num">${pct(capacity.diagnostic_p95_fraction)}</td><td class="num">${safe(capacity.binding_days)}/${safe(capacity.sessions)}</td><td class="num">${pct(capacity.aggregate_fill_rate)}</td><td class="num">${mult(capacity.aggressive_uncapped_multiple)}</td><td class="num">${mult(capacity.aggressive_capped_multiple)}</td></tr></tbody></table>`
      : "<p class=\"note\">该组合尚无容量情景。</p>";
  }
  $("period-select").addEventListener("change",drawReplay);$("stock-select").addEventListener("change",drawReplay);drawReplay();
  const counterfactual=data.counterfactual_series||[];
  const counterfactualEvents=[...new Set(counterfactual.map(row=>row.event_id))];
  $("counterfactual-select").innerHTML=counterfactualEvents.map(id=>{
    const row=counterfactual.find(item=>item.event_id===id);
    return `<option value="${safe(id)}">${safe(row.period)} · ${safe(eventNames[id]||id)}</option>`;
  }).join("");
  function drawCounterfactual(){
    const rows=counterfactual.filter(row=>row.event_id===$("counterfactual-select").value);
    if(!rows.length){$("counterfactual-assumptions").textContent="尚无通过核验的情景。";$("counterfactual-table").textContent="";return;}
    const first=rows[0];
    $("counterfactual-assumptions").textContent=`首次受信号影响的收益日：${first.first_signal_trade_date}；每类 Agent 假设资金 ${(first.aum_cny_per_agent/1e8).toFixed(0)} 亿元；固定假设流动性 ${(first.liquidity_notional_cny/1e8).toFixed(0)} 亿元；手设事件信号 ${Number(first.scenario_signal).toFixed(2)}。两项来源清单与独立重跑已核对。`;
    $("counterfactual-table").innerHTML=`<table class="data-table"><thead><tr><th>冲击系数</th><th style="text-align:right">情景净订单差</th><th style="text-align:right">情景末日价格指数差</th><th style="text-align:right">全期末价格指数差</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${Number(row.impact_coefficient).toFixed(3)}</td><td class="num">${(row.event_window_net_order_delta_cny/1e8).toFixed(3)} 亿元</td><td class="num">${Number(row.event_end_price_delta).toFixed(4)}</td><td class="num">${Number(row.terminal_price_delta).toFixed(4)}</td></tr>`).join("")}</tbody></table>`;
  }
  const laggedImpact=data.lagged_impact_series||[];
  function drawLaggedImpact(){
    const rows=laggedImpact.filter(row=>row.event_id===$("counterfactual-select").value);
    if(!rows.length){$("lagged-impact-summary").textContent="该事件尚无通过核验的滞后成交额敏感性情景。";$("lagged-impact-table").textContent="";return;}
    const example=rows.filter(row=>row.impact_coefficient===0.03).map(row=>row.terminal_price_delta);
    const changeSign=example.some(value=>value<0)&&example.some(value=>value>0);
    $("lagged-impact-summary").textContent=`${rows[0].period} · ${rows.length*2} 条路径 / ${rows.length} 组有无信号配对。冲击系数 0.03 时，全期末指数差从 ${Math.min(...example).toFixed(4)} 到 ${Math.max(...example).toFixed(4)}${changeSign?'，随假设比例变号':''}。两项清单与独立重跑已核对。`;
    $("lagged-impact-table").innerHTML=`<table class="data-table"><thead><tr><th>容量比例</th><th>冲击分母比例</th><th>冲击系数</th><th style="text-align:right">情景末日指数差</th><th style="text-align:right">全期末指数差</th><th style="text-align:right">触及容量日（无/有信号）</th></tr></thead><tbody>${rows.map(row=>`<tr><td>${pct(row.participation_rate,0)}</td><td>${pct(row.impact_depth_fraction,0)}</td><td>${Number(row.impact_coefficient).toFixed(3)}</td><td class="num">${Number(row.event_end_price_delta).toFixed(4)}</td><td class="num">${Number(row.terminal_price_delta).toFixed(4)}</td><td class="num">${safe(row.control_binding_days)}/${safe(row.event_binding_days)}</td></tr>`).join("")}</tbody></table>`;
  }
  $("counterfactual-select").addEventListener("change",()=>{drawCounterfactual();drawLaggedImpact();});drawCounterfactual();drawLaggedImpact();
  const runLabels={
    unified_dataset:"统一问答数据集",observed_market:"公开行情导入",observed_event:"历史事件研究",
    event_date_diagnostic:"事件日期对照",observed_activity:"成交活动导入",activity_event:"事件成交活动",
    text_prediction:"文本预测对照",synthetic_stress:"合成 Agent 压力",historical_replay_q1:"历史回放 · 一季度",
    historical_replay_later:"历史回放 · 2020 后三季度",semantic_annotation:"语义抽样包",keyword_baseline:"关键词基线",
    observed_market_2018:"公开行情导入 · 2017–2018",observed_event_2018:"事件研究 · 2018 资管新规",
    historical_replay_2018:"历史回放 · 2018 上半年",
    observed_activity_2018:"成交活动导入 · 2018",activity_event_2018:"事件成交活动 · 2018",
    event_date_diagnostic_2018:"事件日期对照 · 2018"
  };
  $("run-list").innerHTML=data.runs.map(r=>`<div class="run-item"><strong>${safe(r.id.replaceAll('_',' '))}</strong><span>${r.integrity==='passed'&&r.reexecution==='equivalent'?'✓ 已重跑':'待核验'}</span></div>`).join("");
  $("run-details").innerHTML=data.runs.map(r=>{const artifacts=r.artifacts||[];return `<details class="evidence-run"><summary><span>${safe(runLabels[r.id]||r.id)}</span><small>${artifacts.length} 份产物 · ${r.hash_checks} 项哈希检查</small></summary><div class="evidence-artifacts">${artifacts.map(item=>`<div><code>${safe(item.name)}</code><span class="artifact-${item.status==='identical'?'ok':'normalized'}">${safe(item.status)}</span></div>`).join("")}</div></details>`;}).join("");
  const s=data.semantic;
  $("semantic-status").innerHTML=`<strong>${s.dual_reviewed} / ${s.items}</strong><p>双人语义标注完成。${s.pending} 条待审；${s.gold_ready?'已有裁定标签':'尚无人工金标准'}。</p>`;
  const mr=data.model_run||{status:"not_run"};
  if(mr.status==="not_run"){
    $("model-run-status").innerHTML="<strong>LLM 尚未运行</strong><p>DeepSeek 原始抽取尚未执行；没有模型准确率结论。</p>";
  }else{
    const raw=mr.raw||{}, norm=mr.normalized||{};
    const normalizedSummary=norm.rows===undefined?'尚未标准化':`标准化 ${safe(norm.rows)} 条，解析失败 ${safe(norm.parse_errors)} 条，缺失 ${safe(norm.missing_predictions)} 条`;
    const history=(data.model_runs||[]).filter(r=>r.normalized&&r.normalized.rows!==undefined);
    const comparison=history.length>1?`<p>${history.map(r=>`${safe(r.prompt_version)}：解析失败 ${safe(r.normalized.parse_errors)} 条`).join('；')}。v1 已按新增边界检查重验；此样本用于协议开发。</p>`:'';
    $("model-run-status").innerHTML=`<strong>开发样本 LLM 审计</strong><p>${safe(mr.model_id||"模型未知")} · ${safe(mr.prompt_version||"提示未知")}：原始 ${safe(raw.rows)}/${safe(mr.scope.requested_rows)} 条，请求失败 ${safe(raw.request_failures)} 条；${normalizedSummary}。</p>${comparison}<p>结构和证据校验不能代表语义准确率，尚需人工金标准。</p>`;
  }
  const hm=data.holdout_model;
  $("holdout-model-status").innerHTML=hm
    ? `<strong>下半年独立留出 · ${safe(hm.items)}/${safe(hm.items)}</strong><p>固定 ${safe(hm.prompt_version)}：请求失败 ${safe(hm.request_failures)}，解析失败 ${safe(hm.parse_errors)}；有效空事件 ${safe(hm.valid_empty_rows)} 条，含事件 ${safe(hm.valid_event_rows)} 条，共 ${safe(hm.validated_events)} 个抽取事件。</p><p>尚无独立双人裁定金标准；不能计算准确率或允许接入 Agent。</p>`
    : `<strong>下半年留出尚未审计</strong><p>没有可展示的留出模型结果。</p>`;
  const rr=data.review_readiness;
  const gate=data.agent_signal_gate;
  $("agent-signal-gate-status").innerHTML=gate
    ? `<strong>Agent 语义信号 · ${safe(gate.status)}</strong><p>适配器 ${safe(gate.adapter_version)}；已审 ${safe(gate.reviewed_items)}/${safe(gate.required_items)} 条。</p><p>${safe(gate.reason)}</p>`
    : `<strong>Agent 语义信号门槛尚未核验</strong><p>没有生成真实语义信号流。</p>`;
  $("review-readiness-status").innerHTML=rr
    ? `<strong>人工审核包 · ${safe(rr.status)}</strong><p>固定 ${safe(rr.items)} 条，${safe(rr.reviewer_slots)} 个独立审核位；当前已审核 ${safe(rr.reviewed_items)} 条；空白标签 ${safe(rr.blank_label_rows_per_reviewer)} 条/人，离线页面 ${safe(rr.interface_pages)} 个。</p><p>来源绑定、页面脱敏和哈希已通过；尚未产生人工金标准。</p>`
    : `<strong>人工审核包尚未核验</strong><p>没有可展示的审核交接状态。</p>`;
  $("evidence-links").innerHTML=data.evidence.map(e=>`<a href="${safe(e.url)}" target="_blank" rel="noopener noreferrer"><span>${safe(e.label)}</span><span>↗</span></a>`).join("");
  $("limitations").innerHTML=data.limitations.map(x=>`<li>${safe(x)}</li>`).join("");

  const runStates={queued:"排队中",running:"记录未结束",passed:"通过",different:"产物不同",
                   failed:"失败",failed_preflight:"输入核验失败",record_changed:"运行记录已变化"};
  const rerunSelect=$("rerun-select"),dataVersionSelect=$("data-version-select"),modelVersionSelect=$("model-version-select"),rerunButton=$("rerun-button"),rerunStatus=$("rerun-status");
  let versionCatalog={};
  function updateVersionSelectors(){
    const versions=versionCatalog[rerunSelect.value];
    if(!versions){dataVersionSelect.innerHTML="";modelVersionSelect.innerHTML="";dataVersionSelect.disabled=true;modelVersionSelect.disabled=true;rerunButton.disabled=true;return;}
    dataVersionSelect.innerHTML=`<option value="${safe(versions.data_version)}">${safe(versions.data_version)}</option>`;
    modelVersionSelect.innerHTML=`<option value="${safe(versions.model_version)}">${safe(versions.model_version)}</option>`;
    dataVersionSelect.disabled=false;modelVersionSelect.disabled=false;rerunButton.disabled=false;
  }
  rerunSelect.addEventListener("change",updateVersionSelectors);
  async function refreshHistory(){
    const response=await fetch("/api/jobs",{cache:"no-store"});
    if(!response.ok)throw new Error("history unavailable");
    const jobs=await response.json();
    $("rerun-history").innerHTML=jobs.length?jobs.map(job=>`<div class="history-item"><div><strong>${safe(runLabels[job.run_id]||job.run_id)}</strong><small>${safe(job.job_id.slice(0,8))} · ${safe(job.started_at||"刚刚")}</small></div><span>${safe(runStates[job.status]||job.status)}${job.compared_artifacts!==undefined?` · ${Number(job.compared_artifacts)} 份`:""}</span></div>`).join(""):'<p class="note">尚无本机重跑记录。</p>';
  }
  async function watchJob(jobId){
    try{
      const response=await fetch(`/api/jobs/${encodeURIComponent(jobId)}`,{cache:"no-store"});
      if(!response.ok)throw new Error("job unavailable");
      const job=await response.json();
      if(job.status==="queued"||job.status==="running"){
        rerunStatus.textContent=`${runLabels[job.run_id]||job.run_id}：正在重跑和比较，请保持服务运行。`;
        setTimeout(()=>watchJob(jobId),1200);return;
      }
      rerunStatus.textContent=`${runLabels[job.run_id]||job.run_id}：${runStates[job.status]||job.status}；比较 ${job.compared_artifacts||0} 份产物。完整记录保存在本机运行目录。`;
      rerunButton.disabled=false;
      await refreshHistory();
    }catch(_){rerunStatus.textContent="执行服务连接中断；请检查本机运行记录后刷新页面。";rerunButton.disabled=false;}
  }
  rerunButton.addEventListener("click",async()=>{
    rerunButton.disabled=true;
    rerunStatus.textContent="正在提交固定运行…";
    try{
      const response=await fetch("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({run_id:rerunSelect.value,data_version:dataVersionSelect.value,model_version:modelVersionSelect.value})});
      if(!response.ok){rerunStatus.textContent=response.status===409?"已有一项重跑正在执行，请稍后刷新。":"提交失败；请核对本地执行服务。";rerunButton.disabled=false;return;}
      const job=await response.json();
      await watchJob(job.job_id);
    }catch(_){rerunStatus.textContent="无法连接本地执行服务。";rerunButton.disabled=false;}
  });
  (async()=>{
    try{
      const response=await fetch("/api/capabilities",{cache:"no-store"});
      if(!response.ok)throw new Error("execution unavailable");
      const capabilities=await response.json();
      versionCatalog=capabilities.versions||{};
      rerunSelect.innerHTML=capabilities.runs.map(id=>`<option value="${safe(id)}">${safe(runLabels[id]||id)}</option>`).join("");
      rerunSelect.value=capabilities.runs.includes("observed_event")?"observed_event":capabilities.runs[0];
      rerunSelect.disabled=false;updateVersionSelectors();
      rerunStatus.textContent=`本地执行服务已连接；配置 ${capabilities.master_config_sha256.slice(0,12)}…`;
      await refreshHistory();
    }catch(_){rerunStatus.textContent="离线只读模式。启动本地执行服务后可选择固定实验重跑。";}
  })();
})();
