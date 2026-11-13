import {t as tr} from './i18n.js?v=5';
const $=id=>document.getElementById(id);
const GOALS=['loot','trophy','stars'];
const EFFORTS=['','low','medium','high'];
const field=(id,key,control,note='')=>`<label class="discord-field"><span data-i18n="${key}">${tr(key)}</span>${control}${note}</label>`;
const note=key=>`<small data-i18n="${key}">${tr(key)}</small>`;
/** The AI deploy settings of the Settings tab: endpoint, model and key stay on the device; the key is never shown again. */
export class AiPanel {
  constructor(send,{local=true}={}){
    this.send=send;this.local=local;this.saved=null;this.dirty=false;this.lastCode='';
    $('ai-settings').innerHTML=`<div class="panel-heading"><div><h2 data-i18n="ai_title">${tr('ai_title')}</h2><p data-i18n="ai_note">${tr('ai_note')}</p></div><span id="ai-status" class="discord-state">${tr('ai_not_set')}</span></div>
<form id="ai-form" class="ai-form">
${field('ai-url','ai_base_url','<input id="ai-url" type="url" autocomplete="off" spellcheck="false" placeholder="https://openrouter.ai/api/v1">')}
${field('ai-model','ai_model','<input id="ai-model" type="text" autocomplete="off" spellcheck="false" placeholder="provider/model-name">')}
<label class="discord-field"><span data-i18n="ai_key">${tr('ai_key')}</span><div class="secret-field"><input id="ai-key" type="password" autocomplete="off" spellcheck="false"><button id="ai-reveal" type="button">${tr('dashboard_show')}</button></div>${note('ai_key_note')}</label>
<div class="ai-grid">
${field('ai-goal','ai_goal',`<select id="ai-goal">${GOALS.map(goal=>`<option value="${goal}" data-i18n="ai_goal_${goal}">${tr('ai_goal_'+goal)}</option>`).join('')}</select>`)}
${field('ai-probe-points','ai_probe_points','<input id="ai-probe-points" type="number" min="2" max="12" step="1" inputmode="numeric">',note('ai_probe_points_note'))}
${field('ai-probe-max','ai_probe_max','<input id="ai-probe-max" type="number" min="2" max="20" step="1" inputmode="numeric">',note('ai_probe_max_note'))}
</div>
${field('ai-effort','ai_effort',`<select id="ai-effort">${EFFORTS.map(effort=>`<option value="${effort}" data-i18n="ai_effort_${effort||'default'}">${tr('ai_effort_'+(effort||'default'))}</option>`).join('')}</select>`,note('ai_effort_note'))}
${field('ai-hint','ai_hint','<textarea id="ai-hint" rows="2" maxlength="500" spellcheck="false"></textarea>')}
<div class="discord-enabled"><label class="toggle"><input type="checkbox" id="ai-enabled"><span data-i18n="ai_enable">${tr('ai_enable')}</span></label></div>
<div class="discord-actions"><button type="submit" class="primary" data-i18n="ai_save">${tr('ai_save')}</button><button id="ai-test" type="button" class="secondary" disabled data-i18n="ai_test">${tr('ai_test')}</button></div>
<p id="ai-feedback" class="hint" role="status"></p>
</form>`;
    for(const type of ['input','change'])$('ai-form').addEventListener(type,()=>{this.dirty=true;$('ai-test').disabled=true;});
    $('ai-form').addEventListener('submit',e=>{e.preventDefault();this.save();});
    $('ai-test').addEventListener('click',()=>{$('ai-feedback').textContent=tr('ai_testing');this.send('test_ai');});
    $('ai-reveal').addEventListener('click',()=>{const input=$('ai-key');const show=input.type==='password';input.type=show?'text':'password';$('ai-reveal').textContent=show?tr('dashboard_hide'):tr('dashboard_show');});
    if(!local)for(const control of $('ai-form').querySelectorAll('input,button,select,textarea'))control.disabled=true;
    this.localize();
  }
  localize(){
    for(const element of $('ai-settings').querySelectorAll('[data-i18n]'))element.textContent=tr(element.dataset.i18n);
    $('ai-reveal').textContent=$('ai-key').type==='text'?tr('dashboard_hide'):tr('dashboard_show');
    this.status();this.feedback();
  }
  status(){
    const data=this.saved;
    $('ai-status').textContent=!data||!data.key_set?tr('ai_not_set'):data.enabled?tr('dashboard_enabled'):tr('dashboard_paused');
    $('ai-status').classList.toggle('enabled',Boolean(data?.enabled&&data.key_set));
    $('ai-key').placeholder=data?.key_set?tr('ai_key_saved'):'';
  }
  feedback(){
    const code=this.lastCode;
    $('ai-feedback').textContent=!this.local?tr('ai_local_only'):code==='ai_tested'?tr('ai_tested_ok',{seconds:this.saved?.tested_seconds??''}):code==='ai_saved'?tr('ai_saved'):'';
  }
  /** Settings sent to the device; the key goes only when a new one was typed, so a saved key is never overwritten by blank. */
  values(){
    const body={base_url:$('ai-url').value.trim(),model:$('ai-model').value.trim(),goal:$('ai-goal').value,hint:$('ai-hint').value,
      probe_points:Number($('ai-probe-points').value),probe_max:Number($('ai-probe-max').value),reasoning_effort:$('ai-effort').value,enabled:$('ai-enabled').checked};
    const key=$('ai-key').value.trim();
    if(key)body.api_key=key;
    return body;
  }
  save(){$('ai-feedback').textContent=tr('dashboard_saving');this.send('set_ai',this.values());}
  load(data,code){
    if(!data||(code==='ai_loaded'&&this.dirty))return;
    this.dirty=false;this.saved=data;this.lastCode=code;
    $('ai-url').value=data.base_url||'';$('ai-model').value=data.model||'';$('ai-key').value='';
    $('ai-goal').value=GOALS.includes(data.goal)?data.goal:'loot';$('ai-hint').value=data.hint||'';
    $('ai-effort').value=EFFORTS.includes(data.reasoning_effort)?data.reasoning_effort:'';
    $('ai-probe-points').value=String(data.probe_points??6);$('ai-probe-max').value=String(data.probe_max??12);
    $('ai-enabled').checked=Boolean(data.enabled);
    $('ai-test').disabled=!this.local||!data.key_set||!data.base_url||!data.model;
    this.status();this.feedback();
  }
}
