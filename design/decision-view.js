/* Read archived execution data; never substitute the experiment's input knobs. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MarketDecisionView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const roleKeys = ['aggressive', 'conservative', 'institutional'];
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
    return {group, asset, step, observation, roles: roleKeys.map(role => {
      const names = Object.keys(day.decisions || {}).filter(name => path.participant_specs?.[name]?.parameters?.role === role);
      const accounts = names.map(name => {
        const spec = path.participant_specs[name], decision = day.decisions[name];
        return {name, parameters: spec.parameters, nav: decision.nav_minor,
          before: decision.current_weights?.[asset], target: decision.desired_weights?.[asset],
          orderChange: decision.order_weight_changes?.[asset], belief: decision.beliefs?.[asset],
          terms: beliefTerms(observation, spec, decision.beliefs?.[asset]),
          reasons: decision.reasons || []};
      });
      const selected = Array.isArray(orders) ? orders.filter(o => names.includes(o.owner)) : null;
      const nav = accounts.reduce((sum, a) => sum + a.nav, 0);
      const mean = getter => accounts.length && numeric(nav) && nav > 0 && accounts.every(a => numeric(getter(a)) && numeric(a.nav) && a.nav > 0)
        ? accounts.reduce((sum, a) => sum + getter(a) * a.nav, 0) / nav : null;
      const total = key => selected && selected.every(o => numeric(o[key])) ? selected.reduce((sum, o) => sum + o[key], 0) : null;
      const sideTotal = side => selected ? selected.filter(o => o.side === side).reduce((sum, o) => sum + o.quantity, 0) : null;
      const reasons = [...new Set(accounts.flatMap(a => a.reasons))];
      const rules = reasons.filter(r => r in ruleLabels).map(code => ({code, label: ruleLabels[code]}));
      const constraints = reasons.filter(r => !(r in ruleLabels)).map(code => ({code,
        label: constraintLabels[code] || `未识别决策记录：${code}`,
        accounts: accounts.filter(a => a.reasons.includes(code)).length}));
      const terms = accounts.length && accounts.every(a => a.terms)
        ? Object.fromEntries(['market','text','uncertainty'].map(key => [key, mean(a => a.terms[key])])) : null;
      return {role, accounts, before: mean(a => a.before), target: mean(a => a.target),
        orderChange: mean(a => a.orderChange), belief: mean(a => a.belief), terms, rules, constraints,
        orders: selected, buys: sideTotal('buy'), sells: sideTotal('sell'),
        requested: total('quantity'), accepted: total('accepted_quantity'), filled: total('filled_quantity')};
    })};
  }
  return {buildStep, describeOrder};
});
