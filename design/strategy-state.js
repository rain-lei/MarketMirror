/* A saved workspace profile and an experiment draft always own separate copies. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.StrategyWorkspace = api.StrategyWorkspace; root.parametersFromExperiment = api.parametersFromExperiment; root.StrategyParameterControls = api.StrategyParameterControls; }
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
  const escapeHTML=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display=(value,field)=>Number((value*field.scale).toPrecision(15)).toString();
  const format=(value,field)=>{if(typeof value!=='number'||!Number.isFinite(value))return '未存档';const number=Number(display(value,field)),text=number!==0&&Math.abs(number)<.5e-8?number.toExponential(2):number.toLocaleString('en-US',{useGrouping:false,minimumFractionDigits:field.key==='base_weight'?0:2,maximumFractionDigits:8});return text+(field.scale===100?'%':'');};
  function parseDisplay(raw,field,bounds){
    if(typeof raw!=='string'||!raw.trim())throw new Error('请输入参数数值');
    const value=Number(raw)/field.scale;
    if(!Number.isFinite(value))throw new Error('请输入有限数值');
    if(value<bounds[0]||value>bounds[1])throw new Error(`允许范围为 ${format(bounds[0],field)} — ${format(bounds[1],field)}`);
    return value;
  }
  function fill(value,bounds){return bounds[1]>bounds[0]?Math.max(0,Math.min(100,(value-bounds[0])/(bounds[1]-bounds[0])*100)):0;}
  function render({field,role,scope,value,bounds,disabled=false}){
    const id=`strategy-${scope}-${role}-${field.key}`,esc=escapeHTML,attrs=`data-strategy-scope="${esc(scope)}" data-strategy-role="${esc(role)}" data-strategy-key="${esc(field.key)}"`;
    return `<div class="form-field strategy-parameter" data-parameter-control style="--parameter-fill:${fill(value,bounds)}%"><label for="${id}">${esc(field.label)}<span class="role-parameter-value" id="${id}-value" data-parameter-label>${format(value,field)}</span></label><div class="parameter-input-row"><div class="parameter-slider-wrap"><input class="parameter-range" id="${id}" type="range" ${attrs} data-parameter-range data-parameter-step="${field.step}" min="${display(bounds[0],field)}" max="${display(bounds[1],field)}" step="any" value="${display(value,field)}" aria-valuetext="${format(value,field)}" aria-describedby="${id}-hint" ${disabled?'disabled':''}><div class="parameter-bounds" aria-hidden="true"><span>${format(bounds[0],field)}</span><span>${format(bounds[1],field)}</span></div></div><div class="parameter-number-wrap"><label class="sr-only" for="${id}-number">精确输入${esc(field.label)}</label><input id="${id}-number" type="number" ${attrs} data-parameter-number min="${display(bounds[0],field)}" max="${display(bounds[1],field)}" step="any" value="${display(value,field)}" aria-invalid="false" aria-describedby="${id}-hint ${id}-error" required ${disabled?'disabled':''}>${field.scale===100?'<span aria-hidden="true">%</span>':''}</div></div><small id="${id}-hint">${esc(field.hint)}</small><p class="parameter-error" id="${id}-error" data-parameter-error role="status" aria-live="polite"></p></div>`;
  }
  function showError(input,message){
    const control=input.closest('[data-parameter-control]');
    control.querySelector('[data-parameter-number]').setAttribute('aria-invalid',message?'true':'false');
    control.classList.toggle('invalid',Boolean(message));
    control.querySelector('[data-parameter-error]').textContent=message;
  }
  function sync(input,value,field,bounds){
    const control=input.closest('[data-parameter-control]'),range=control.querySelector('[data-parameter-range]'),number=control.querySelector('[data-parameter-number]');
    range.value=display(value,field);range.setAttribute('aria-valuetext',format(value,field));
    if(input!==number)number.value=display(value,field);
    control.style.setProperty('--parameter-fill',`${fill(value,bounds)}%`);
    control.querySelector('[data-parameter-label]').textContent=format(value,field);
    showError(number,'');
  }
  const boundRoots=new WeakSet();
  function bind(root){
    if(boundRoots.has(root))return;boundRoots.add(root);
    root.addEventListener('keydown',event=>{
      const range=event.target;if(!range.matches?.('[data-parameter-range]')||range.disabled)return;
      const direction={ArrowRight:1,ArrowUp:1,ArrowLeft:-1,ArrowDown:-1}[event.key];if(!direction)return;
      event.preventDefault();
      range.value=String(Math.max(Number(range.min),Math.min(Number(range.max),Number((Number(range.value)+direction*Number(range.dataset.parameterStep)*(event.shiftKey?10:1)).toPrecision(15)))));
      range.dispatchEvent(new Event('input',{bubbles:true}));
    });
  }
  const StrategyParameterControls={display,format,parseDisplay,fill,render,sync,showError,bind};
  return { StrategyWorkspace, parametersFromExperiment, StrategyParameterControls };
});
