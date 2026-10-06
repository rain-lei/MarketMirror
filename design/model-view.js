/* Read backend readiness; only an explicit check sends synthetic text. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketModelView=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  class ConnectionState{
    constructor(){this.configuration=null;this.loading=false;this.checking=false;this.error='';this.version=0;}
    beginLoad(){if(this.checking)return null;this.loading=true;this.error='';return ++this.version;}
    beginCheck(){if(this.checking||this.loading||this.configuration?.checking||this.configuration?.credential_status!=='configured')return null;this.checking=true;this.error='';return ++this.version;}
    accept(ticket,configuration){
      if(ticket!==this.version)return false;
      if(!configuration||typeof configuration.model!=='string'||!configuration.model.trim()||typeof configuration.base_url!=='string'||!configuration.base_url.trim()||typeof configuration.prompt_version!=='string'||typeof configuration.checking!=='boolean'||!['configured','missing','unavailable'].includes(configuration.credential_status))throw new Error('模型配置响应不完整');
      this.configuration=configuration;this.loading=false;this.checking=false;this.error='';return true;
    }
    fail(ticket,message){if(ticket!==this.version)return false;this.loading=false;this.checking=false;this.error=message;return true;}
  }
  function render(state){
    const config=state.configuration,error=state.error?`<div class="model-error" role="alert">${esc(state.error)}${config?'。以下保留上次读取的配置，请刷新确认。':''}</div>`:'';
    if(!config)return error+`<p class="model-note" role="status">${state.loading?'正在读取后端模型配置…':'模型配置尚未读取。'}</p><button class="btn" data-action="refresh-model" ${state.loading?'disabled':''}>重新读取配置</button>`;
    const busy=state.checking||config.checking,ready=config.credential_status==='configured',result=config.check_result||config.last_check;
    const credential=ready?'本机凭据可用':config.credential_status==='missing'?'尚未保存本机凭据':'本机凭据无法读取';
    let outcome='';
    if(result){
      const date=new Date(result.checked_at),when=Number.isNaN(date.getTime())?'时间未记录':date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
      const title=result.status==='passed'?'最近检测通过':result.status==='configuration_changed'?'检测时配置已改变':'最近检测未通过';
      outcome=`<div class="model-check-result ${result.status==='passed'?'passed':'failed'}" role="status"><strong>${title}</strong><p>${esc(result.message)}</p><small>${esc(when)} · 北京时间${Number.isFinite(result.elapsed_ms)?` · ${(result.elapsed_ms/1000).toFixed(2)} 秒`:''}</small>${result.status==='passed'&&Number.isInteger(result.facts_count)?`<p>合成测试文字提取 ${result.facts_count} 条事实，引文结构已核对。</p>`:''}</div>`;
    }
    return error+`<div class="eyebrow">MODEL CONNECTION · 后端实际配置</div><div class="form-field"><label for="model-name">模型</label><input id="model-name" value="${esc(config.model)}" readonly></div><div class="form-field"><label for="model-endpoint">接口地址</label><input id="model-endpoint" value="${esc(config.base_url)}" readonly></div><div class="model-readiness"><span class="badge ${ready?'':'neutral'}">${credential}</span><span>事实提取规则 ${esc(config.prompt_version)}</span></div>${ready?'<p class="model-note">使用已保存在本机的密钥，无需每次输入。密钥不返回浏览器。</p>':'<p class="model-note">请在项目的 .env.local 中保存 MARKETMIRROR_LLM_API_KEY，再刷新配置。合成市场实验可继续使用。</p>'}${state.loading?'<p class="model-note" role="status">正在刷新配置…</p>':''}${busy?'<p class="model-note" role="status">正在发送合成文字，检测实际请求与引文结构…关闭此窗口不会中断检测。</p>':outcome||'<p class="model-note">尚未检测接口连接。读取配置不会调用模型。</p>'}<div class="model-actions"><button class="btn primary" type="button" data-action="check-model" ${busy||!ready||state.loading?'disabled':''}>${busy?'正在检测…':'检测连接与事实提取'}</button><button class="btn" type="button" data-action="refresh-model" ${state.loading||state.checking?'disabled':''}>刷新配置</button></div><p class="model-note model-boundary">连接检测仅发送固定合成文字，不发送你的草稿或研究资料。检测通过表示请求与引文结构可用，不代表事实判断或市场预测已验证。</p>`;
  }
  return {ConnectionState,render};
});
