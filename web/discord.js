import {t as tr} from './i18n.js';
const $=id=>document.getElementById(id);
export class DiscordPanel {
  constructor(send,{local=true,preview=false}={}){
    this.send=send;this.preview=preview;this.saved=null;this.dirty=false;
    $('discord-settings').innerHTML=`<div class="panel-heading"><div><h2 data-i18n="dashboard_discord_notifications">${tr('dashboard_discord_notifications')}</h2></div><span id="discord-status" class="discord-state">Not connected</span></div><div class="discord-layout"><form id="discord-form"><label class="discord-field"><span data-i18n="dashboard_webhook_url">${tr('dashboard_webhook_url')}</span><div class="secret-field"><input id="discord-url" type="password" autocomplete="off" spellcheck="false" placeholder="https://discord.com/api/webhooks/…"><button id="discord-reveal" type="button" aria-label="Show webhook URL">Show</button></div><small id="discord-url-note" data-i18n="dashboard_stored_on_this_device">${tr('dashboard_stored_on_this_device')}</small></label><label class="discord-field"><span><span data-i18n="dashboard_ping_user_id">${tr('dashboard_ping_user_id')}</span> <small data-i18n="dashboard_optional">${tr('dashboard_optional')}</small></span><input id="discord-ping" type="text" inputmode="numeric" autocomplete="off" spellcheck="false" pattern="[0-9]{17,22}" placeholder="Your Discord user ID"><small data-i18n="dashboard_pings_on_completion_errors_and_recovery">${tr('dashboard_pings_on_completion_errors_and_recovery')}</small></label><div class="discord-enabled"><label class="toggle"><input type="checkbox" id="discord-enabled"><span data-i18n="dashboard_enable_notifications">${tr('dashboard_enable_notifications')}</span></label></div><fieldset class="discord-events"><legend data-i18n="dashboard_send_updates_for">${tr('dashboard_send_updates_for')}</legend>${[['started','dashboard_run_started'],['ended','dashboard_run_ended_or_failed'],['recovery','dashboard_recovery_alerts'],['summary','dashboard_progress_reports']].map(([value,label])=>`<label><input type="checkbox" name="discord-event" value="${value}" ${value!=='started'?'checked':''}><span data-i18n="${label}">${tr(label)}</span></label>`).join('')}</fieldset><label class="discord-field"><span data-i18n="dashboard_progress_report_interval">${tr('dashboard_progress_report_interval')}</span><select id="discord-interval"><option value="15" data-i18n="dashboard_every_15_minutes">${tr('dashboard_every_15_minutes')}</option><option value="30" selected data-i18n="dashboard_every_30_minutes">${tr('dashboard_every_30_minutes')}</option><option value="60" data-i18n="dashboard_every_hour">${tr('dashboard_every_hour')}</option></select></label><div class="discord-actions"><button type="submit" class="primary" data-i18n="dashboard_save_webhook">${tr('dashboard_save_webhook')}</button><button id="discord-test" type="button" class="secondary" disabled data-i18n="dashboard_send_test">${tr('dashboard_send_test')}</button><button id="discord-remove" type="button" class="text-button" disabled data-i18n="dashboard_remove">${tr('dashboard_remove')}</button></div><p id="discord-feedback" class="hint" role="status"></p></form><div class="discord-preview"><div class="discord-preview-caption" data-i18n="dashboard_message_preview">${tr('dashboard_message_preview')}</div><div class="discord-message"><div class="discord-avatar">⚔</div><div class="discord-message-content"><div class="discord-author">CoC Auto <span>APP</span></div><div id="discord-ping-preview" class="discord-mention" hidden></div><div class="discord-embed"><strong class="embed-title" data-i18n="dashboard_run_complete">${tr('dashboard_run_complete')}</strong><div class="discord-embed-fields">${[['dashboard_home_gold','gold'],['dashboard_home_elixir','elixir'],['dashboard_dark_elixir','dark'],['dashboard_builder_gold','builder_gold'],['dashboard_builder_elixir','builder_elixir'],['dashboard_attacks','attacks'],['dashboard_walls','walls'],['dashboard_recoveries','recoveries']].map(([label,key])=>`<div><span data-i18n="${label}">${tr(label)}</span><strong data-embed-value="${key}">0</strong></div>`).join('')}<div><span data-i18n="dashboard_run_time">${tr('dashboard_run_time')}</span><strong id="embed-duration">0h 00m</strong></div><div><span data-i18n="dashboard_pace">${tr('dashboard_pace')}</span><strong id="embed-pace">0 attacks / hr</strong></div></div><small>CoC Auto • <span data-i18n="dashboard_measured_storage_increases">${tr('dashboard_measured_storage_increases')}</span></small></div></div></div></div></div>`;
    for (const type of ['input','change']) $('discord-form').addEventListener(type,()=>{this.dirty=true;$('discord-test').disabled=true;});
    $('discord-form').addEventListener('submit',e=>{e.preventDefault();this.save();});
    $('discord-test').addEventListener('click',()=>this.send('test_discord'));
    $('discord-remove').addEventListener('click',()=>this.send('set_discord',{remove:true,enabled:false,ping_id:'',events:[],interval:30}));
    $('discord-reveal').addEventListener('click',()=>{const input=$('discord-url');const show=input.type==='password';input.type=show?'text':'password';$('discord-reveal').textContent=show?tr('dashboard_hide'):tr('dashboard_show');$('discord-reveal').setAttribute('aria-label',show?tr('dashboard_hide_webhook_url'):tr('dashboard_show_webhook_url'));});
    $('discord-ping').addEventListener('input',()=>this.updateMention());
    $('discord-url').addEventListener('input',()=>this.validate());
    if(!local){for(const input of $('discord-form').querySelectorAll('input,button,select'))input.disabled=true;$('discord-feedback').textContent=tr('dashboard_configure_discord_from_the_local_dashboard_on_your_device');}
    this.local=local;this.localize();
  }
  localize(){
    for(const e of $('discord-settings').querySelectorAll('[data-i18n]'))e.textContent=tr(e.dataset.i18n);
    $('discord-ping').placeholder=tr('dashboard_your_discord_user_id');
    const show=$('discord-url').type==='text';
    $('discord-reveal').textContent=show?tr('dashboard_hide'):tr('dashboard_show');
    $('discord-reveal').setAttribute('aria-label',show?tr('dashboard_hide_webhook_url'):tr('dashboard_show_webhook_url'));
    if(this.saved){
      $('discord-status').textContent=this.saved.enabled&&this.saved.configured?tr('dashboard_enabled'):this.saved.configured?tr('dashboard_paused'):tr('dashboard_not_connected');
      $('discord-url').placeholder=this.saved.configured?tr('dashboard_webhook_saved_paste_a_new_url_to_replace'):'https://discord.com/api/webhooks/…';
    }else $('discord-status').textContent=tr('dashboard_not_connected');
    this.feedback();this.validate();this.stats(this.lastStats||{duration:0,totals:{},attacks_per_hour:0});
  }
  feedback(){
    const status=this.saved?.status;
    const keys={'Sending…':'dashboard_sending','Queue full; update skipped':'dashboard_queue_full','Last message delivered':'dashboard_delivered','Delivery failed. Check the webhook and connection.':'dashboard_delivery_failed'};
    $('discord-feedback').textContent=!this.local?tr('dashboard_configure_discord_from_the_local_dashboard_on_your_device'):this.lastCode==='discord_tested'?tr(this.preview?'dashboard_message_preview_updated_connect_a_device_to_send_to_discord':'dashboard_test_message_queued'):this.lastCode==='discord_saved'?tr('dashboard_saved_notification_changes_apply_immediately'):keys[status]?tr(keys[status]):'';
  }
  validate(){const input=$('discord-url');const valid=!input.value.trim()||/^https:\/\/discord\.com\/api(?:\/v\d+)?\/webhooks\/\d{17,22}\/[\w.-]{20,200}$/.test(input.value.trim());input.setCustomValidity(valid?'':tr('dashboard_paste_a_webhook_url_from_discord_com'));return valid;}
  save(){if(!this.validate())return;$('discord-feedback').textContent=tr('dashboard_saving');this.send('set_discord',{url:$('discord-url').value.trim(),ping_id:$('discord-ping').value.trim(),enabled:$('discord-enabled').checked,events:[...document.querySelectorAll('[name="discord-event"]:checked')].map(x=>x.value),interval:Number($('discord-interval').value)});}
  load(data,code){
    if(!data || (code==='discord_loaded'&&this.dirty))return;this.dirty=false;this.saved=data;this.lastCode=code;
    $('discord-status').textContent=data.enabled&&data.configured?tr('dashboard_enabled'):data.configured?tr('dashboard_paused'):tr('dashboard_not_connected');
    $('discord-status').classList.toggle('enabled',data.enabled&&data.configured);
    $('discord-url').value='';$('discord-url').placeholder=data.configured?tr('dashboard_webhook_saved_paste_a_new_url_to_replace'):'https://discord.com/api/webhooks/…';
    $('discord-ping').value=data.ping_id||'';$('discord-enabled').checked=Boolean(data.enabled);
    for(const input of document.querySelectorAll('[name="discord-event"]'))input.checked=(data.events||[]).includes(input.value);
    $('discord-interval').value=String(data.interval||30);
    $('discord-test').disabled=!this.local||!data.configured;$('discord-remove').disabled=!this.local||!data.configured;
    this.feedback();
    this.updateMention();
  }
  updateMention(){const value=$('discord-ping').value.trim(),element=$('discord-ping-preview');element.hidden=!value;element.textContent=value?`@${value}`:'';}
  stats(data){if(!data)return;this.lastStats=data;for(const e of document.querySelectorAll('[data-embed-value]'))e.textContent=Number(data.totals?.[e.dataset.embedValue]||0).toLocaleString('en-US');const n=data.duration||0;$('embed-duration').textContent=`${Math.floor(n/3600)}${document.documentElement.lang==='id'?'j':'h'} ${String(Math.floor(n/60)%60).padStart(2,'0')}m`;$('embed-pace').textContent=`${data.attacks_per_hour||0} ${tr('dashboard_attacks_hr')}`;}
}
