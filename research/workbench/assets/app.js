(function () {
  "use strict";
  const data = window.MARKETMIRROR_DATA;
  if (!data) { document.body.textContent = "研究摘要缺失。请先运行工作台生成命令。"; return; }
  const $ = (id) => document.getElementById(id);
  const safe = (value) => String(value).replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
  const pct = (n, digits=2) => `${(n * 100).toFixed(digits)}%`;
  const mult = (n) => Number(n).toFixed(3);
  const eventNames = {
    "wuhan_date_only_conservative":"日期保守口径 · 2 月 3 日",
    "wuhan_effective_time_upper_bound":"生效时间代理 · 1 月 23 日"
  };
  const codes = {"000001":"平安银行 · 000001","000002":"万科 A · 000002","600519":"贵州茅台 · 600519"};
  const roles = {aggressive:"激进型", conservative:"保守型", institutional:"机构型"};
  const metrics = [
    ["问答来源行",data.overview.question_rows.toLocaleString("zh-CN"),"三份本地 Excel"],
    ["来源股票代码",data.overview.stocks.toLocaleString("zh-CN"),"身份仍待核实"],
    ["行情交易日",String(data.overview.market_sessions),"2019.06—2020.12"],
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
    $("event-caption").textContent = `等权三股累计异常收益路径；横轴是相对交易日。${eventNames[id] || id}。`;
    const activity = new Map(data.activity.filter(a=>a.event_id===id).map(a=>[a.stock_code,a]));
    $("event-table").innerHTML=`<table class="data-table"><thead><tr><th>股票</th><th>对齐日</th><th style="text-align:right">CAR</th><th style="text-align:right">成交额倍数</th></tr></thead><tbody>${rows.map(e=>`<tr><td>${safe(codes[e.stock_code]||e.stock_code)}</td><td>${safe(e.event_date)}</td><td class="num ${e.car>=0?'positive':'negative'}">${pct(e.car)}</td><td class="num">${activity.has(e.stock_code)?Number(activity.get(e.stock_code).amount_fold).toFixed(2)+'×':'—'}</td></tr>`).join("")}</tbody></table>`;
  }
  $("event-select").addEventListener("change",drawEvent);drawEvent();
  $("placebo-summary").textContent=`真实事件窗口 ${data.placebo.blackout_start}—${data.placebo.blackout_end} 被隔离后，事前 ${data.placebo.before} 日、事后 ${data.placebo.after} 日可作描述性日期对照；排名不是 p 值。`;
  const p=data.prediction;
  const predictionBars=[["纯行情 MAE",p.market_mae,false],["行情 + 文本 MAE",p.text_mae,true]];
  $("prediction-bars").innerHTML=predictionBars.map(([label,value,alt])=>`<div class="bar-row"><div class="bar-row-head"><span>${safe(label)}</span><strong>${pct(value,3)}</strong></div><div class="bar-track"><div class="bar-fill ${alt?'alt':''}" style="width:${Math.min(100,value/.03*100).toFixed(1)}%"></div></div></div>`).join("");
  $("prediction-interval").textContent=`${p.rows} 条测试预测。配对 MAE 差值（文本 − 行情）${pct(p.paired_difference,3)}；近似 95% 区间 [${pct(p.interval_95[0],3)}, ${pct(p.interval_95[1],3)}]，包含零。`;
  const periods=[...new Set(data.replays.map(r=>r.period))];
  const stocks=[...new Set(data.replays.map(r=>r.stock_code))];
  $("period-select").innerHTML=periods.map(x=>`<option>${safe(x)}</option>`).join("");
  $("stock-select").innerHTML=stocks.map(x=>`<option value="${safe(x)}">${safe(codes[x]||x)}</option>`).join("");
  function drawReplay(){
    const rows=data.replays.filter(r=>r.period===$("period-select").value && r.stock_code===$("stock-select").value);
    $("replay-table").innerHTML=`<table class="data-table"><thead><tr><th>角色</th><th style="text-align:right">市场信号</th><th style="text-align:right">零信号</th><th style="text-align:right">买入持有</th><th style="text-align:right">最大回撤</th><th style="text-align:right">交易</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${safe(roles[r.role]||r.role)}</td><td class="num">${mult(r.signal_multiple)}</td><td class="num">${mult(r.control_multiple)}</td><td class="num">${mult(r.buyhold_multiple)}</td><td class="num">${pct(r.max_drawdown,1)}</td><td class="num">${r.trades}</td></tr>`).join("")}</tbody></table>`;
  }
  $("period-select").addEventListener("change",drawReplay);$("stock-select").addEventListener("change",drawReplay);drawReplay();
  $("run-list").innerHTML=data.runs.map(r=>`<div class="run-item"><strong>${safe(r.id.replaceAll('_',' '))}</strong><span>${r.integrity==='passed'&&r.reexecution==='equivalent'?'✓ 已重跑':'待核验'}</span></div>`).join("");
  const s=data.semantic;
  $("semantic-status").innerHTML=`<strong>${s.dual_reviewed} / ${s.items}</strong><p>双人语义标注完成。${s.pending} 条待审；${s.gold_ready?'已有裁定标签':'尚无人工金标准'}。</p>`;
  $("evidence-links").innerHTML=data.evidence.map(e=>`<a href="${safe(e.url)}" target="_blank" rel="noopener noreferrer"><span>${safe(e.label)}</span><span>↗</span></a>`).join("");
  $("limitations").innerHTML=data.limitations.map(x=>`<li>${safe(x)}</li>`).join("");
})();
