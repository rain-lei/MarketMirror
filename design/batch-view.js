/* Persisted batch state and presentation; statistics come from saved results. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MarketBatchView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const scenarios = {neutral:'中性', positive:'正向', negative:'负向', uncertain:'高不确定性'};
  const statuses = {ready:'待运行',pending:'待运行',running:'运行中',completed:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断'};
  const roles = [{key:'aggressive',name:'激进型',color:'var(--orange)'},
    {key:'conservative',name:'保守型',color:'var(--blue)'},
    {key:'institutional',name:'机构型',color:'var(--purple)'}];
  const number = value => typeof value === 'number' && Number.isFinite(value) ? value : null;
  const pp = value => number(value) === null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(6)}`;
  const quantity = value => number(value) === null ? '—' : value.toLocaleString('zh-CN');
  const badge = status => `<span class="batch-status ${esc(status)}">${esc(statuses[status] || '未知状态')}</span>`;
  const alert = message => message ? `<div class="callout" role="alert">${esc(message)}</div>` : '';

  function parseSeeds(text) {
    const tokens = String(text).trim().split(/[\s,，]+/).filter(Boolean);
    if (!tokens.length || tokens.length > 10 || tokens.some(s => !/^\d+$/.test(s) || Number(s) > 999999))
      throw new Error('请输入 1–10 个种子，以逗号分隔；每个须为 0–999999 的整数。');
    const seeds = tokens.map(Number);
    if (new Set(seeds).size !== seeds.length) throw new Error('种子不能重复。');
    return seeds;
  }

  class BatchState {
    constructor() {
      this.rows = []; this.selectedId = null; this.detail = null;
      this.loading = false; this.busy = false; this.error = ''; this.listError = '';
      this.generation = 0;
    }
    begin(id) {
      if (this.selectedId !== id) this.detail = null;
      this.selectedId = id; this.loading = true; this.error = '';
      return {id, generation: ++this.generation};
    }
    current(ticket) { return ticket.id === this.selectedId && ticket.generation === this.generation; }
    accept(ticket, record) {
      if (!this.current(ticket)) return false;
      if (record.id !== ticket.id) throw new Error('返回的批次编号不匹配，请重新加载。');
      this.detail = record; this.loading = false; this.error = '';
      const row = Object.fromEntries(['id','title','created_at','status','progress','sessions'].map(k => [k, record[k]]));
      this.rows = [row, ...this.rows.filter(r => r.id !== row.id)].sort((a,b) => b.created_at.localeCompare(a.created_at));
      return true;
    }
    fail(ticket, message) {
      if (!this.current(ticket)) return false;
      this.loading = false; this.error = message; return true;
    }
  }

  function renderForm(form, busy, error) {
    return `<section class="panel batch-form-panel"><div class="panel-header"><div><h2>创建对照批次</h2><p class="panel-subtitle">使用当前已保存的三类策略配置</p></div><span class="badge neutral">4 种固定情景</span></div>
      <form id="batch-create-form" class="summary-content">
        <div class="form-field"><label for="batch-title">批次名称</label><input id="batch-title" data-batch-field="title" value="${esc(form.title)}" maxlength="80" required></div>
        <div class="form-field"><label for="batch-seeds">随机种子</label><input id="batch-seeds" data-batch-field="seeds" value="${esc(form.seeds)}" required aria-describedby="batch-seeds-help"><small id="batch-seeds-help">逗号分隔，最多 10 个，例如 1, 7, 19。每个种子运行四种情景。</small></div>
        <div class="form-two"><div class="form-field"><label for="batch-sessions">实验总步数</label><input id="batch-sessions" type="number" data-batch-field="sessions" value="${esc(form.sessions)}" min="1" max="60" step="1" required></div><div class="form-field"><label for="batch-duration">消息持续步数</label><select id="batch-duration" data-batch-field="duration">${[3,6,9].map(n => `<option value="${n}" ${Number(form.duration)===n?'selected':''}>${n} 步</option>`).join('')}</select></div></div>
        <div class="form-field"><label for="batch-cash">每类初始资金（模型元）</label><input id="batch-cash" type="number" data-batch-field="cash" value="${esc(form.cash)}" min="10000" max="100000000" required></div>
        <p class="batch-help">消息从第 5 步作用于资产 A。创建时冻结已保存参数；策略页未保存的修改不进入本批次。</p>
        <div id="batch-form-error" role="alert" class="form-error">${esc(error)}</div>
        <button type="submit" class="btn primary" id="batch-create-button" ${busy?'disabled':''}>${busy?'正在提交…':'创建并运行批次'}</button>
      </form></section>`;
  }

  function renderHistory(rows, selectedId, loading, error) {
    return `<div class="panel-header"><div><h2>本机批次</h2><p class="panel-subtitle">已完成结果保留，未完成项可继续运行</p></div><button class="btn compact" data-action="refresh-batches">刷新</button></div>
      <div class="summary-content">${alert(error)}${rows.length ? `<div class="batch-history-list">${rows.map(row => `<button type="button" class="batch-history-item ${row.id===selectedId?'active':''}" data-batch-select="${esc(row.id)}" aria-pressed="${row.id===selectedId}"><span class="batch-history-top"><strong>${esc(row.title)}</strong>${badge(row.status)}</span><span class="batch-history-meta">完成 ${row.progress.completed}/${row.progress.total} · ${row.sessions} 步${row.progress.failed?` · ${row.progress.failed} 项失败`:''}</span><small>${esc(new Date(row.created_at).toLocaleString('zh-CN'))}</small></button>`).join('')}</div>` : `<div class="batch-empty">${loading?'正在读取批次…':'尚无批次。创建后，完整实验会自动保存到本机。'}</div>`}</div>`;
  }

  function roleChart(items, extent, role) {
    const x = value => 212 + value / extent * 66;
    return `<svg class="batch-range-chart" viewBox="0 0 360 180" role="img" aria-label="${role.name}四种情景的平均收益差和最小最大范围，以百分点表示">
      <line x1="212" y1="8" x2="212" y2="150" stroke="var(--line)" stroke-dasharray="3 3"/>
      ${Object.keys(scenarios).map((key,i) => {
        const item = items.find(r => r.scenario === key), y = 22 + i*36;
        const valid = item && ['mean_pp','min_pp','max_pp'].every(k => number(item[k]) !== null);
        return `<text x="8" y="${y+4}" fill="var(--muted)" font-size="12">${scenarios[key]}</text>${valid?`<line x1="${x(item.min_pp)}" x2="${x(item.max_pp)}" y1="${y}" y2="${y}" stroke="${role.color}" stroke-width="3" opacity=".4"/><circle cx="${x(item.mean_pp)}" cy="${y}" r="4" fill="${role.color}"/>`:''}<text x="352" y="${y+4}" text-anchor="end" fill="var(--ink)" font-size="11">${pp(item?.mean_pp)}</text>`;
      }).join('')}
      <text x="146" y="173" text-anchor="middle" fill="var(--muted)" font-size="10">${(-extent).toFixed(3)}</text><text x="212" y="173" text-anchor="middle" fill="var(--muted)" font-size="10">0</text><text x="278" y="173" text-anchor="middle" fill="var(--muted)" font-size="10">+${extent.toFixed(3)}</text>
    </svg>`;
  }

  function renderDetail(record, options = {}) {
    const {busy=false, loading=false, error=''} = options;
    if (!record) return alert(error)+`<section class="panel batch-empty">${loading?'正在读取所选批次…':'选择一个批次，查看进度与三类策略对照。'}</section>`;
    const progress = record.progress, summary = record.summary, records = record.records;
    const extent = Math.max(.001, ...summary.flatMap(r => [r.min_pp,r.max_pp]).filter(v => number(v)!==null).map(Math.abs));
    const finished = progress.completed + progress.failed;
    const runnable = ['ready','partial','failed','interrupted'].includes(record.status);
    const values = row => ['mean_pp','min_pp','max_pp'].map(k => `<td class="number">${pp(row[k])}</td>`).join('');
    return alert(error && record ? error+'。保留上次读取的数据，以下状态可能尚未更新。' : error)
      +`<section class="panel batch-progress-panel"><div class="panel-header"><div><h2>${esc(record.title)}</h2><p class="panel-subtitle">${record.seeds.length} 个种子 × 4 种情景 · 每项 ${record.sessions} 步</p></div>${badge(record.status)}</div><div class="summary-content"><div class="batch-progress-title"><strong>${progress.completed}<small> / ${progress.total} 项完成</small></strong><div class="batch-progress-actions">${runnable?`<button class="btn primary" data-action="run-batch" ${busy||loading?'disabled':''}>${record.status==='ready'?'开始运行':'重试未完成项'}</button>`:''}<a class="btn" href="/api/platform/batches/${esc(record.id)}/report" download>下载报告</a></div></div><progress max="${progress.total}" value="${finished}" aria-label="已完成或失败的批次项">${finished}/${progress.total}</progress><p class="batch-help">${progress.running?'当前正在运行 1 项 · ':''}${progress.pending} 项待运行 · ${progress.failed} 项失败 · ${progress.interrupted} 项中断。关闭页面后，任务继续在本机服务中执行；停止服务后未完成项可重试。</p>${alert(record.error?.message)}<div class="batch-config-line"><span>种子 ${record.seeds.join(' / ')}</span><span>消息持续 ${record.duration} 步</span><span>每类资金 ${quantity(record.cash)} 模型元</span><span>策略版本 ${esc(record.strategy_parameters_sha256.slice(0,10))}</span></div><details class="batch-snapshot" data-batch-disclosure="snapshot"><summary>查看冻结的三类策略参数</summary><div class="table-wrap"><table><thead><tr><th>策略</th><th>文本敏感度</th><th>基础股票权重</th><th>风险预算</th></tr></thead><tbody>${roles.map(role=>{const p=record.strategy_parameters[role.key];return `<tr><td>${role.name}</td><td>${p.text_sensitivity}</td><td>${(p.base_weight*100).toFixed(2)}%</td><td>${(p.risk_budget*100).toFixed(2)}%</td></tr>`;}).join('')}</tbody></table></div></details></div></section>`
      +`<div class="section-title batch-section-title"><h2>三类策略 · 跨种子对照</h2><span>点为均值，线为最小值至最大值 · 收益差 pp</span></div><div class="batch-role-grid">${roles.map(role => {
        const items=summary.filter(r=>r.role===role.key);
        return `<article class="panel batch-role-card" style="--role:${role.color}"><div class="panel-header"><h2>${role.name}</h2><span class="badge neutral">有消息 − 无消息</span></div>${roleChart(items,extent,role)}<div class="batch-samples">${items.map(r=>`<span>${scenarios[r.scenario]} ${r.completed}/${r.planned}</span>`).join('')}</div></article>`;
      }).join('')}</div><p class="batch-help">仅统计完成项，缺失项保持空白。各卡片使用同一坐标尺度；范围是种子间的描述性差异，不是置信区间。</p>`
      +`<section class="panel batch-result-panel"><div class="panel-header"><h2>逐项结果与执行</h2><span class="badge neutral">固定批次归档</span></div><div class="table-wrap" tabindex="0" aria-label="逐项结果表，可横向滚动"><table><thead><tr><th>情景 / 种子</th><th>状态</th><th>激进收益差 pp</th><th>保守收益差 pp</th><th>机构收益差 pp</th><th>请求 / 接受 / 成交（股）</th><th>结果与原因</th></tr></thead><tbody>${records.map(row=>`<tr><td><strong>${scenarios[row.scenario]}</strong><small class="batch-table-seed">seed ${row.seed} · 第 ${row.attempts} 次尝试</small></td><td>${badge(row.status)}</td>${roles.map(role=>`<td class="number">${row.status==='completed'?pp(row.role_return_difference_pp[role.key]):'—'}</td>`).join('')}<td class="number">${row.status==='completed'?[row.requested,row.accepted,row.filled].map(quantity).join(' / '):'—'}</td><td>${row.status==='completed'?`<button class="btn compact" data-batch-experiment="${esc(row.experiment_id)}" data-batch-id="${esc(record.id)}">查看决策</button>`:row.error?`<span class="batch-row-error">${esc(row.error.type)} · ${esc(row.error.message)}</span>`:'—'}</td></tr>`).join('')}</tbody></table></div></section>`
      +`<details class="panel batch-summary-table" data-batch-disclosure="summary"><summary>展开描述性统计明细</summary><div class="table-wrap" tabindex="0" aria-label="跨种子统计表，可横向滚动"><table><thead><tr><th>情景</th><th>策略</th><th>完成 / 计划</th><th>平均收益差 pp</th><th>最小值 pp</th><th>最大值 pp</th></tr></thead><tbody>${summary.map(row=>`<tr><td>${scenarios[row.scenario]}</td><td>${roles.find(r=>r.key===row.role).name}</td><td>${row.completed}/${row.planned}</td>${values(row)}</tr>`).join('')}</tbody></table></div></details>`;
  }
  return {BatchState, parseSeeds, renderForm, renderHistory, renderDetail};
});
