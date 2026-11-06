import {t as tr} from './i18n.js';
import {refreshSelects} from './select.js';
import { VisiblePoller } from './visible-poller.js?v=2';

const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString('en-US');
const compact = n => Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(n||0);
const esc = s => String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels={gold:'dashboard_home_gold',elixir:'dashboard_home_elixir',dark:'dashboard_dark_elixir',builder_gold:'dashboard_builder_gold',builder_elixir:'dashboard_builder_elixir'};
const resourceKeys=Object.keys(labels);
const resourceIcon=key=>key.startsWith('builder_')?`${key}.webp`:`${key==='dark'?'dark_elixir':key}.png`;
const colors={gold:'#c28b18',elixir:'#b649cb',dark:'#615497',builder_gold:'#b68624',builder_elixir:'#7644b8'};
export const duration = n => `${Math.floor(n/3600)}${document.documentElement.lang==='id'?'j':'h'} ${String(Math.floor(n/60)%60).padStart(2,'0')}m`;

export function chartIndex(x,left,width,count,banded=false){
  const ratio=(x-left)/width;
  return Math.max(0,Math.min(count-1,banded?Math.floor(ratio*count):Math.round(ratio*(count-1))));
}
export function tooltipPosition(x,y,width,height,hostWidth,hostHeight){
  const gap=12,pad=8;
  return {
    left:Math.max(pad,Math.min(x+gap+width>hostWidth-pad?x-width-gap:x+gap,hostWidth-width-pad)),
    top:Math.max(pad,Math.min(y-height/2,hostHeight-height-pad)),
  };
}

export class StatsPanel {
  constructor(request, visible) {
    this.period='run'; this.resource='gold'; this.data=null; this.request=request;
    $('view-stats').innerHTML=`
      <div class="stats-toolbar"><p id="stats-updated"></p><label class="chart-filter"><span class="sr-only" data-i18n="dashboard_period">${tr('dashboard_period')}</span><select id="stats-period"><option value="run" data-i18n="dashboard_this_run">${tr('dashboard_this_run')}</option><option value="24h" data-i18n="dashboard_24_hours">${tr('dashboard_24_hours')}</option><option value="7d" data-i18n="dashboard_7_days">${tr('dashboard_7_days')}</option></select></label></div>
      <div class="village-resource-groups">${[['dashboard_home_village',['gold','elixir','dark']],['dashboard_builder_base',['builder_gold','builder_elixir']]].map(([village,keys])=>`<section class="village-resource-group"><h2 data-i18n="${village}">${tr(village)}</h2><div class="resource-totals ${keys.length===2?'builder-totals':''}">${keys.map(key=>`<article class="panel resource-total ${key.includes('elixir')?'elixir':key==='dark'?'dark':'gold'}"><div class="stat-label"><span class="stat-currency"><img src="icons/resources/${resourceIcon(key)}" alt="" width="26" height="26"></span><span data-i18n="${key==='dark'?'dashboard_dark_elixir':key.endsWith('gold')?'dashboard_gold':'dashboard_elixir'}">${tr(key==='dark'?'dashboard_dark_elixir':key.endsWith('gold')?'dashboard_gold':'dashboard_elixir')}</span></div><strong id="stat-${key}">—</strong><small id="stat-rate-${key}">${tr('dashboard_measured_storage_gains')}</small></article>`).join('')}</div></section>`).join('')}</div>
      <div class="performance-totals">${[['attacks','⚔','dashboard_bases_attacked'],['walls','▥','dashboard_walls_upgraded'],['recoveries','↻','dashboard_recoveries'],['pace','↗','dashboard_attacks_hour']].map(([key,icon,label])=>`<article class="panel performance-total"><span class="stat-mini-icon ${key==='attacks'||key==='walls'?'stat-game-icon':''}" aria-hidden="true">${key==='attacks'||key==='walls'?`<img src="icons/stats/${key}.${key==='walls'?'webp':'png'}" alt="" width="36" height="36">`:icon}</span><div><strong id="stat-${key}">—</strong><span data-i18n="${label}">${tr(label)}</span></div></article>`).join('')}</div>
      <div class="stats-facts"><span><strong id="stat-searches">—</strong> <span data-i18n="dashboard_opponents_checked">${tr('dashboard_opponents_checked')}</span></span><span><strong id="stat-errors">—</strong> <span data-i18n="dashboard_errors">${tr('dashboard_errors')}</span></span><span><strong id="stat-run-count">—</strong> <span data-i18n="dashboard_runs">${tr('dashboard_runs')}</span></span></div><div class="stats-charts"><section class="panel resources-chart"><div class="panel-heading"><div><h2 data-i18n="dashboard_resource_gains">${tr('dashboard_resource_gains')}</h2></div><div class="chart-filters"><label class="chart-filter"><span class="sr-only" data-i18n="dashboard_village">${tr('dashboard_village')}</span><select id="chart-village"><option value="home" data-i18n="dashboard_home_village">${tr('dashboard_home_village')}</option><option value="builder" data-i18n="dashboard_builder_base">${tr('dashboard_builder_base')}</option></select></label><label class="chart-filter"><span class="sr-only" data-i18n="dashboard_resource">${tr('dashboard_resource')}</span><select id="chart-resource"><option value="gold" data-i18n="dashboard_gold">${tr('dashboard_gold')}</option><option value="elixir" data-i18n="dashboard_elixir">${tr('dashboard_elixir')}</option><option value="dark" data-i18n="dashboard_dark_elixir">${tr('dashboard_dark_elixir')}</option></select></label></div></div><div class="chart-host" id="resource-chart"></div><details class="chart-info"><summary data-i18n="dashboard_how_gains_are_measured">${tr('dashboard_how_gains_are_measured')}</summary><p data-i18n="dashboard_storage_increases_only_opponent_offers_are_excluded_full_storages_may_hide_gains">${tr('dashboard_storage_increases_only_opponent_offers_are_excluded_full_storages_may_hide_gains')}</p></details></section>
      <section class="panel activity-chart"><div class="panel-heading"><div><h2 data-i18n="dashboard_village_activity">${tr('dashboard_village_activity')}</h2></div></div><div class="chart-key"><span><i class="attack-key"></i> <span data-i18n="dashboard_attacks">${tr('dashboard_attacks')}</span></span><span><i class="wall-key"></i> <span data-i18n="dashboard_walls">${tr('dashboard_walls')}</span></span></div><div class="chart-host" id="activity-chart"></div></section></div>
      <div class="stats-bottom"><section class="panel"><div class="panel-heading"><h2 data-i18n="dashboard_recent_runs">${tr('dashboard_recent_runs')}</h2><span id="stats-runtime" class="capture-tag">—</span></div><div class="runs-table-wrap"><table class="runs-table"><thead><tr><th data-i18n="dashboard_run">${tr('dashboard_run')}</th><th data-i18n="dashboard_duration">${tr('dashboard_duration')}</th><th data-i18n="dashboard_attacks">${tr('dashboard_attacks')}</th><th data-i18n="dashboard_walls">${tr('dashboard_walls')}</th><th data-i18n="dashboard_status">${tr('dashboard_status')}</th></tr></thead><tbody id="stats-runs"></tbody></table></div></section><section class="panel"><div class="panel-heading"><h2 data-i18n="dashboard_milestones">${tr('dashboard_milestones')}</h2></div><ol class="stats-events" id="stats-events"></ol></section></div>`;
    $('stats-period').addEventListener('change',()=>{this.period=$('stats-period').value;this.refresh();});
    for(const id of ['chart-village','chart-resource']) $(id).addEventListener('change',()=>{
      const builder=$('chart-village').value==='builder';
      $('chart-resource').options[2].disabled=builder;
      $('chart-resource').options[2].hidden=builder;
      if(builder&&$('chart-resource').value==='dark')$('chart-resource').value='gold';
      this.resource=(builder?'builder_':'')+$('chart-resource').value;
      refreshSelects();this.renderCharts();
    });
    this.poller=new VisiblePoller({visible,request:()=>this.refresh(),interval:5000});
    document.addEventListener('visibilitychange',()=>this.poller.sync());
    window.addEventListener('pagehide',()=>this.poller.sync());
    window.addEventListener('pageshow',()=>this.poller.sync());
    this.render();
    this.chartResize=new ResizeObserver(()=>{
      const host=$('activity-chart');
      if(host.clientWidth&&host.clientHeight&&!this.preserveChart(host))this.barChart(host,this.data?.buckets||[]);
    });
    this.chartResize.observe($('activity-chart'));
  }
  localize(){for(const e of $('view-stats').querySelectorAll('[data-i18n]'))e.textContent=tr(e.dataset.i18n);this.render();}
  translateDetail(value){const keys={"Wall upgrades": "dashboard_wall_upgrades", "Farming": "dashboard_farming", "Attacks": "dashboard_attacks", "running": "dashboard_running", "completed": "dashboard_completed", "stopped": "dashboard_stopped", "failed": "dashboard_failed", "interrupted": "dashboard_interrupted", "Home Village attack": "dashboard_home_village_attack", "Builder Base attack": "dashboard_builder_base_attack", "Storage increased": "dashboard_storage_increased", "Home Village storage increased": "dashboard_home_village_storage_increased", "Builder Base storage increased": "dashboard_builder_base_storage_increased", "Recovery attempt": "dashboard_recovery_attempt", "Worker error": "dashboard_worker_error"};const walls=/^(\d+) walls? upgraded$/.exec(value);return walls?tr('dashboard_count_walls_upgraded',{count:fmt(walls[1])}):keys[value]?tr(keys[value]):value;}
  refresh(){return this.request(this.period);}
  sync(){this.poller.sync();}
  load(data){if(data?.period!==this.period)return;this.data=data;this.render();}
  render(){
    const d=this.data,t=d?.totals||{};
    for(const key of [...resourceKeys,'attacks','walls','recoveries']) $(`stat-${key}`).textContent=d?(resourceKeys.includes(key)&&!d.measured?.includes(key)&&!t[key]?'—':fmt(t[key])):'—';
    $('stat-pace').textContent=d?fmt(d.attacks_per_hour):'—';
    $('stat-searches').textContent=d?fmt(t.searches):'—';
    $('stat-errors').textContent=d?fmt(t.errors):'—';
    $('stat-run-count').textContent=d?fmt(d.run_count??d.runs?.length):'—';
    for(const key of resourceKeys) $(`stat-rate-${key}`).textContent=d&&!d.measured?.includes(key)&&!t[key]?tr('dashboard_not_measured'):d&&d.duration?`${compact(t[key]*3600/d.duration)} / ${tr('dashboard_hour')}`:tr('dashboard_measured_storage_gains');
    $('stats-updated').textContent=d?`${tr('dashboard_updated')} ${new Date(d.updated_at*1000).toLocaleTimeString(document.documentElement.lang==='id'?'id-ID':'en-US', {hour:'2-digit',minute:'2-digit'})}`:tr('dashboard_waiting_for_your_device');
    $('stats-runtime').textContent=d?`${duration(d.duration)}`:'—';
    $('stats-runs').innerHTML=d?.runs?.length?d.runs.map(r=>`<tr><td><strong>${esc(this.translateDetail(r.task))}</strong><small>${new Date(r.started*1000).toLocaleString(document.documentElement.lang==='id'?'id-ID':'en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</small></td><td>${duration(r.duration)}</td><td>${fmt(r.attacks)}</td><td>${fmt(r.walls)}</td><td><span class="run-status ${esc(r.status)}">${esc(this.translateDetail(r.status))}</span></td></tr>`).join(''):`<tr><td colspan="5" class="stats-empty">${tr('dashboard_your_runs_will_appear_here')}</td></tr>`;
    $('stats-events').innerHTML=d?.recent?.length?d.recent.map(e=>`<li><span class="event-dot ${esc(e.kind)}"></span><div><strong>${esc(this.translateDetail(e.detail))}</strong><time>${new Date(e.ts*1000).toLocaleTimeString(document.documentElement.lang==='id'?'id-ID':'en-US',{hour:'2-digit',minute:'2-digit'})}</time></div></li>`).join(''):`<li class="stats-empty">${tr('dashboard_no_activity_recorded_yet')}</li>`;
    this.renderCharts();
  }
  renderCharts(){
    const buckets=this.data?.buckets||[];
    if(!this.preserveChart($('resource-chart')))this.lineChart($('resource-chart'),buckets,this.resource);
    if(!this.preserveChart($('activity-chart')))this.barChart($('activity-chart'),buckets);
  }
  preserveChart(host){return host.contains(document.activeElement)||host.matches(':hover');}
  frame(data,max,host=null,banded=false){
    const W=host?Math.max(280,host.clientWidth-24):760;
    const H=host?Math.max(160,host.clientHeight-19):255;
    const L=54,R=28,T=14,B=36,usableW=W-L-R,usableH=H-T-B;
    max=Math.max(max,1);
    // Category bands leave half a slot at either edge, like Recharts BarChart.
    const band=usableW/Math.max(1,data.length);
    const x=i=>banded?L+(i+.5)*band:L+(data.length<=1?usableW/2:i*usableW/(data.length-1));
    const y=v=>T+usableH-(v/max)*usableH;
    let grid='';
    for(let i=0;i<=4;i++){const v=max*i/4;grid+=`<path d="M${L},${y(v)}H${W-R}" class="chart-grid"/><text x="${L-10}" y="${y(v)+4}" text-anchor="end">${compact(v)}</text>`;}
    const indexes=[...new Set([0,Math.floor((data.length-1)/2),data.length-1])].filter(i=>i>=0);
    for(const i of indexes)grid+=`<text x="${x(i)}" y="${H-10}" text-anchor="middle">${new Date(data[i].ts*1000).toLocaleString(document.documentElement.lang==='id'?'id-ID':'en-US',{...(this.period==='7d'?{month:'short',day:'numeric'}:{hour:'2-digit',minute:'2-digit'})})}</text>`;
    return {W,H,L,R,T,B,band,x,y,grid};
  }
  lineChart(host,data,key){
    if(this.data&&!this.data.measured?.includes(key)&&!this.data.totals?.[key]){host.innerHTML=`<div class="chart-empty">${tr('dashboard_no_resource_readings_yet')}</div>`;return;}
    if(!data.length){host.innerHTML=`<div class="chart-empty">${tr('dashboard_start_a_run_to_see_statistics')}</div>`;return;}
    const max=Math.max(100,Math.max(...data.map(b=>b[key]||0))*1.2);
    const f=this.frame(data,max),points=data.map((b,i)=>`${f.x(i)},${f.y(b[key]||0)}`).join(' ');
    const base=f.H-f.B;
    host.innerHTML=`<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="${tr(labels[key])}"><defs><linearGradient id="resource-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${colors[key]}" stop-opacity=".26"/><stop offset="100%" stop-color="${colors[key]}" stop-opacity=".02"/></linearGradient></defs>${f.grid}<polygon points="${f.x(0)},${base} ${points} ${f.x(data.length-1)},${base}" fill="url(#resource-fill)"/><polyline points="${points}" stroke="${colors[key]}" fill="none" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>${data.map((b,i)=>`<circle cx="${f.x(i)}" cy="${f.y(b[key]||0)}" r="5" fill="${colors[key]}" stroke="#fff8e8" stroke-width="2" tabindex="0" data-point="${i}" aria-label="${fmt(b[key])} ${tr(labels[key])}"><title>${new Date(b.ts*1000).toLocaleString()}: ${fmt(b[key])} ${tr(labels[key])}</title></circle>`).join('')}</svg><div class="chart-tooltip" hidden></div>`;
    this.tooltips(host,data,[[key,labels[key],colors[key]]],f);
  }
  barChart(host,data){
    if(!data.length){host.innerHTML=`<div class="chart-empty">${tr('dashboard_no_attacks_or_upgrades_yet')}</div>`;return;}
    const max=Math.max(4,Math.ceil(Math.max(...data.flatMap(b=>[b.attacks||0,b.walls||0]))/4)*4);
    const f=this.frame(data,max,host,true),gap=Math.min(4,f.band*.08),width=Math.min(14,(f.band*.8-gap)/2);
    host.innerHTML=`<svg viewBox="0 0 ${f.W} ${f.H}" role="img" aria-label="${tr('dashboard_village_activity')}"><defs><clipPath id="activity-plot"><rect x="${f.L}" y="${f.T}" width="${f.W-f.L-f.R}" height="${f.H-f.T-f.B}"/></clipPath></defs>${f.grid}<g clip-path="url(#activity-plot)">${data.map((b,i)=>['attacks','walls'].map((key,k)=>`<rect x="${f.x(i)+(k?gap/2:-width-gap/2)}" y="${f.y(b[key]||0)}" width="${width}" height="${Math.max(0,f.H-f.B-f.y(b[key]||0))}" rx="3" fill="${k?'#c69a38':'#79a94d'}" tabindex="0" data-point="${i}" aria-label="${fmt(b[key])} ${tr(key==='attacks'?'dashboard_attacks':'dashboard_walls')}"><title>${fmt(b[key])} ${tr(key==='attacks'?'dashboard_attacks':'dashboard_walls')}</title></rect>`).join('')).join('')}</g></svg><div class="chart-tooltip" hidden></div>`;
    this.tooltips(host,data,[['attacks','dashboard_attacks','#79a94d'],['walls','dashboard_walls','#c69a38']],f,true);
  }
  tooltips(host,data,series,frame,banded=false){
    const tooltip=host.querySelector('.chart-tooltip'),svg=host.querySelector('svg');
    svg.setAttribute('tabindex','0');
    tooltip.id=`${host.id}-tooltip`;
    tooltip.setAttribute('role','tooltip');
    svg.setAttribute('aria-describedby',tooltip.id);
    // One plot-wide interaction surface, including empty space and zero-value bars.
    for(const point of svg.querySelectorAll('[data-point]')){
      point.removeAttribute('tabindex');point.style.pointerEvents='none';
    }
    // Native SVG titles otherwise compete with the formatted tooltip.
    for(const title of svg.querySelectorAll('title'))title.remove();
    const ns='http://www.w3.org/2000/svg';
    const cursor=document.createElementNS(ns,banded?'rect':'line');
    cursor.classList.add('chart-active-cursor');cursor.style.display='none';
    if(banded){cursor.setAttribute('y',frame.T);cursor.setAttribute('height',frame.H-frame.T-frame.B);cursor.setAttribute('width',frame.band);}
    else{cursor.setAttribute('y1',frame.T);cursor.setAttribute('y2',frame.H-frame.B);}
    svg.append(cursor);
    const surface=document.createElementNS(ns,'rect');
    for(const [key,value] of Object.entries({x:frame.L,y:frame.T,width:frame.W-frame.L-frame.R,height:frame.H-frame.T-frame.B,fill:'transparent'}))surface.setAttribute(key,value);
    surface.classList.add('chart-hit-area');svg.append(surface);
    let index=0;
    const hide=()=>{tooltip.hidden=true;cursor.style.display='none';};
    const show=(i,pointerY=null)=>{
      index=Math.max(0,Math.min(data.length-1,i));const b=data[index];
      tooltip.innerHTML=`<strong>${esc(new Date(b.ts*1000).toLocaleString(document.documentElement.lang==='id'?'id-ID':'en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</strong>${series.map(([key,label,color])=>`<div><i style="background:${color}"></i><span>${esc(tr(label))}</span><b>${fmt(b[key])}</b></div>`).join('')}`;
      tooltip.hidden=false;cursor.style.display='';
      const x=frame.x(index);
      if(banded)cursor.setAttribute('x',x-frame.band/2);
      else{cursor.setAttribute('x1',x);cursor.setAttribute('x2',x);}
      const sr=svg.getBoundingClientRect(),hr=host.getBoundingClientRect();
      const anchorX=sr.left-hr.left+x*sr.width/frame.W;
      const anchorY=pointerY??(sr.top-hr.top+frame.y(b[series[0][0]]||0)*sr.height/frame.H);
      const pos=tooltipPosition(anchorX,anchorY,tooltip.offsetWidth,tooltip.offsetHeight,host.clientWidth,host.clientHeight);
      tooltip.style.left=`${pos.left}px`;tooltip.style.top=`${pos.top}px`;
    };
    svg.addEventListener('pointermove',e=>{
      const sr=svg.getBoundingClientRect(),hr=host.getBoundingClientRect();
      const x=(e.clientX-sr.left)*frame.W/sr.width,y=(e.clientY-sr.top)*frame.H/sr.height;
      if(x<frame.L||x>frame.W-frame.R||y<frame.T||y>frame.H-frame.B){hide();return;}
      show(chartIndex(x,frame.L,frame.W-frame.L-frame.R,data.length,banded),e.clientY-hr.top);
    });
    svg.addEventListener('focus',()=>show(index));
    svg.addEventListener('keydown',e=>{
      if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
        e.preventDefault();show(e.key==='Home'?0:e.key==='End'?data.length-1:index+(e.key==='ArrowRight'?1:-1));
      }else if(e.key==='Escape'){e.preventDefault();hide();}
    });
    host.onpointerleave=hide;
    svg.addEventListener('blur',hide);
  }
}

// Deterministic preview data never enters the device database or notification sender.
export function previewStatistics(period='run'){
  const n=period==='7d'?21:period==='24h'?18:12, now=Date.now()/1000;
  const step=period==='7d'?21600:period==='24h'?3600:600;
  const buckets=Array.from({length:n},(_,i)=>({ts:now-(n-1-i)*step,gold:Math.round((430000+(i*187391)%970000)*(period==='run'?1:1.4)),elixir:Math.round((510000+(i*219371)%850000)*(period==='run'?1:1.4)),dark:5200+(i*1937)%9800,builder_gold:250000+(i*73191)%420000,builder_elixir:220000+(i*98671)%470000,attacks:3+i%5,walls:i%4===0?0:2+(i*7)%13,recoveries:i===5?1:0,searches:7+i%9,errors:0}));
  const totals=Object.fromEntries([...resourceKeys,'attacks','walls','recoveries','searches','errors'].map(k=>[k,buckets.reduce((sum,b)=>sum+b[k],0)]));
  const seconds=n*step;
  return {period,totals,buckets,duration:seconds,attacks_per_hour:Math.round(totals.attacks*36000/seconds)/10,updated_at:now,measured:resourceKeys,resource_basis:'storage_increases',run_count:period==='run'?1:3,recent:[{ts:now-60,kind:'walls',detail:'8 walls upgraded'},{ts:now-180,kind:'attack',detail:'Home Village attack'},{ts:now-390,kind:'resources',detail:'Storage increased'},{ts:now-630,kind:'attack',detail:'Builder Base attack'},{ts:now-900,kind:'walls',detail:'12 walls upgraded'}],runs:[{id:3,task:'Wall upgrades',started:now-seconds,duration:seconds,status:'running',attacks:totals.attacks,walls:totals.walls},...(period==='run'?[]:[{id:2,task:'Farming',started:now-18000,duration:4280,status:'completed',attacks:38,walls:0},{id:1,task:'Wall upgrades',started:now-28000,duration:5120,status:'stopped',attacks:41,walls:64}])]};
}
