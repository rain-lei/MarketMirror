/* Keep one draft per browser tab; completed records remain on the server. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.MarketDraftCache=api;})
(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const KEY='marketmirror:draft-v1',SCHEMA='platform-draft-v1';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const id=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);
  const fields=['title','source','type','published','signal','uncertainty','duration','sessions','seed','cash','strategy_parameters'];
  function validDraft(value){
    if(!object(value)||Object.keys(value).some(key=>!fields.includes(key)))return false;
    if(typeof value.title!=='string'||value.title.length>120||typeof value.source!=='string'||value.source.length>12000)return false;
    if(!['政策消息','宏观消息','公司问答','其他消息'].includes(value.type)||typeof value.published!=='string'||value.published.length>48)return false;
    if(!['signal','uncertainty','duration','sessions','seed','cash'].every(key=>typeof value[key]==='number'&&Number.isFinite(value[key])))return false;
    const p=value.strategy_parameters;
    if(p==null)return true;
    return object(p)&&Object.keys(p).length===3&&['aggressive','conservative','institutional'].every(role=>
      object(p[role])&&Object.keys(p[role]).length===3&&['text_sensitivity','base_weight','risk_budget'].every(key=>typeof p[role][key]==='number'&&Number.isFinite(p[role][key])));
  }
  function validSubmission(value){
    if(value==null)return true;
    if(!object(value)||!id(value.id)||!object(value.payload)||typeof value.signature!=='string'||value.signature!==JSON.stringify(value.payload))return false;
    const p=value.payload;
    if(Object.keys(p).some(key=>!['title','source','type','published_at','signal','uncertainty','duration','sessions','seed','cash','analysis_id','strategy_parameters'].includes(key)))return false;
    const draft={...p,published:p.published_at};delete draft.published_at;delete draft.analysis_id;
    return validDraft(draft)&&(p.analysis_id==null||id(p.analysis_id));
  }
  function validate(value){
    if(!object(value)||value.schema!==SCHEMA||!validDraft(value.draft)||!Number.isInteger(value.wizard)||value.wizard<1||value.wizard>3||
       typeof value.saved_at!=='string'||!Number.isFinite(Date.parse(value.saved_at))||
       (value.analysis_id!=null&&!id(value.analysis_id))||typeof value.analysis_pending!=='boolean'||!validSubmission(value.submission))
      throw new Error('草稿记录无法读取，原缓存保留；可以新建实验。');
    return clone(value);
  }
  class DraftCache{
    constructor(storage){this.storage=storage;this.savedAt=null;this.error='';this.restored=false;}
    read(){
      try{
        if(!this.storage)throw new Error('storage unavailable');
        const raw=this.storage.getItem(KEY);if(raw==null)return null;
        const value=validate(JSON.parse(raw));this.savedAt=value.saved_at;this.restored=true;this.error='';return value;
      }catch(_){this.error='当前浏览器未能恢复草稿。已保存实验仍可从本机读取。';return null;}
    }
    clear(){
      try{if(!this.storage)throw new Error('storage unavailable');this.storage.removeItem(KEY);this.savedAt=null;this.restored=false;this.error='';return true;}
      catch(_){this.error='当前浏览器无法清除旧草稿，刷新后请核对内容。';return false;}
    }
    write({draft,wizard,analysisId=null,analysisPending=false,submission=null}){
      try{
        if(!this.storage)throw new Error('storage unavailable');
        const value=validate({schema:SCHEMA,saved_at:new Date().toISOString(),draft,wizard,analysis_id:analysisId,analysis_pending:analysisPending,
          submission:submission?{id:submission.id,payload:submission.payload,signature:submission.signature}:null});
        this.storage.setItem(KEY,JSON.stringify(value));this.savedAt=value.saved_at;this.error='';return true;
      }catch(_){this.error='当前浏览器无法保存草稿，刷新可能丢失未提交内容。已保存实验不受影响。';return false;}
    }
  }
  function matchesRecord(payload,record){
    if(!payload||!record||record.corrupt)return false;
    if(record.title!==payload.title.trim())return false;
    for(const key of ['source','type','published_at','signal','uncertainty','duration','sessions','seed','cash','analysis_id'])
      if((record[key]??null)!==(payload[key]??null))return false;
    if(payload.strategy_parameters!=null){
      for(const role of ['aggressive','conservative','institutional'])for(const key of ['text_sensitivity','base_weight','risk_budget'])
        if(record.strategy_parameters?.[role]?.[key]!==payload.strategy_parameters[role]?.[key])return false;
    }
    return true;
  }
  return {DraftCache,matchesRecord,KEY};
});
