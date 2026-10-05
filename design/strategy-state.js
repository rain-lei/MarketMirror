/* A saved workspace profile and an experiment draft always own separate copies. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.StrategyWorkspace = api.StrategyWorkspace; root.parametersFromExperiment = api.parametersFromExperiment; }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  function parametersFromExperiment(record) {
    if (record.strategy_parameters) return copy(record.strategy_parameters);
    if (record.backendResult?.strategy_parameters) return copy(record.backendResult.strategy_parameters);
    const specs = record.backendResult?.paths?.with_message?.participant_specs;
    if (!specs) return null;
    const parameters = {};
    for (const spec of Object.values(specs)) {
      if (spec.kind !== 'strategy') continue;
      const p = spec.parameters;
      if (!parameters[p.role]) parameters[p.role] = Object.fromEntries(
        ['text_sensitivity','base_weight','risk_budget'].map(key=>[key,p[key]]));
    }
    return ['aggressive','conservative','institutional'].every(role=>parameters[role]) ? parameters : null;
  }
  class StrategyWorkspace {
    constructor() {
      this.parameters = null;
      this.draft = null;
      this.defaults = null;
      this.limits = null;
      this.fixedRules = null;
      this.updatedAt = null;
      this.loading = true;
      this.saving = false;
      this.error = '';
    }
    load(profile) {
      this.parameters = copy(profile.parameters);
      this.draft = copy(profile.parameters);
      this.defaults = copy(profile.defaults);
      this.limits = copy(profile.limits);
      this.fixedRules = copy(profile.fixed_rules);
      this.updatedAt = profile.updated_at;
      this.loading = false;
      this.error = '';
    }
    validate(role, key, value) {
      const bound = this.limits?.[role]?.[key];
      if (!bound || typeof value !== 'number' || !Number.isFinite(value) || value < bound[0] || value > bound[1]) {
        throw new Error('参数超出当前策略允许范围');
      }
      return value;
    }
    edit(role, key, value) {
      if (this.saving) throw new Error('正在保存策略参数');
      this.draft[role][key] = this.validate(role, key, value);
    }
    snapshot() {
      if (!this.parameters) throw new Error('策略参数尚未加载');
      return copy(this.parameters);
    }
    restoreDefaults() {
      if (this.saving) throw new Error('正在保存策略参数');
      this.draft = copy(this.defaults);
    }
    get dirty() { return JSON.stringify(this.parameters) !== JSON.stringify(this.draft); }
  }
  return { StrategyWorkspace, parametersFromExperiment };
});
