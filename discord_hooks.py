"""Device-local Discord settings and a bounded background webhook sender. No game-loop network waits."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
from urllib import request, error

WEBHOOK = re.compile(r'https://discord\.com/api(?:/v\d+)?/webhooks/\d{17,22}/[\w.-]{20,200}\Z', re.ASCII)
EVENTS = ('started','ended','recovery','summary')

class HookRefused(ValueError): pass

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class DiscordHooks:
    def __init__(self, path=None, transport=None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock, self.lock = clock, threading.RLock()
        self.options = {'url':'','enabled':False,'ping_id':'','events':['ended','recovery','summary'],'interval':30}
        if self.path and self.path.is_file():
            try: self.options.update(json.loads(self.path.read_text()))
            except (ValueError,OSError): pass
        self.status = 'Not connected' if not self.options['url'] else 'Ready'
        self.jobs = queue.Queue(maxsize=8)
        self.transport = transport or self.post
        self.thread = None
        self.provider = None
        self.generation = 0
        self.last_recovery = float('-inf')
        self.last_summary = self.clock()

    def public(self):
        with self.lock: return {k:v for k,v in self.options.items() if k!='url'} | {'configured':bool(self.options['url']), 'status':self.status}

    def save(self, value):
        if not isinstance(value,dict): raise HookRefused('Invalid Discord settings')
        with self.lock:
            url = value.get('url', '').strip()
            if value.get('remove'): url=''
            elif not url: url=self.options['url']
            ping = value.get('ping_id','').strip()
            if url and not WEBHOOK.fullmatch(url): raise HookRefused('Use a Discord webhook URL from discord.com')
            if ping and not re.fullmatch(r'\d{17,22}',ping): raise HookRefused('Discord user ID must contain 17–22 digits')
            events = value.get('events', [])
            if not isinstance(events,list) or any(x not in EVENTS for x in events): raise HookRefused('Invalid notification events')
            interval=value.get('interval',30)
            if type(interval) is not int or interval not in (15,30,60): raise HookRefused('Choose a 15, 30, or 60 minute interval')
            enabled=value.get('enabled',False)
            if type(enabled) is not bool: raise HookRefused('Invalid enabled option')
            if enabled and not url: raise HookRefused('Add a webhook URL before enabling notifications')
            options={'url':url,'enabled':enabled,'ping_id':ping,'events':list(dict.fromkeys(events)),'interval':interval}
            if self.path:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temporary=self.path.with_suffix('.tmp')
                with open(temporary,'w',opener=lambda p,f:os.open(p,f,0o600)) as f: json.dump(options,f)
                os.chmod(temporary,0o600)
                temporary.replace(self.path)
            self.options=options
            self.generation += 1
            self.status='Ready' if url else 'Not connected'
            return self.public()

    @staticmethod
    def embed(kind, stats, ping_id=''):
        names={'test':'Webhook connected','started':'Run started','ended':'Run complete','stopped':'Run stopped','failed':'Run needs attention','recovery':'Recovering your village','summary':'Village progress'}
        totals=stats.get('totals',{})
        fields=[{'name':name,'value':f"**{int(totals.get(key,0)):,}**",'inline':True} for key,name in [('gold','🪙 Home Gold'),('elixir','💧 Home Elixir'),('dark','🌑 Dark elixir'),('builder_gold','🟨 Builder Gold'),('builder_elixir','🟣 Builder Elixir'),('attacks','⚔️ Attacks'),('walls','🧱 Walls'),('recoveries','🔧 Recoveries')]]
        duration=int(stats.get('duration',0)); hours,minutes=divmod(duration//60,60)
        fields += [{'name':'Run time','value':f'{hours}h {minutes:02d}m','inline':True}, {'name':'Pace','value':f"{stats.get('attacks_per_hour',0):g} attacks / hr",'inline':True}]
        ping=ping_id if kind in ('failed','recovery','ended','stopped','test') else ''
        return {'username':'CoC Auto','content':f'<@{ping}>' if ping else '',
            'allowed_mentions':{'parse':[],'users':[ping] if ping else []},
            'embeds':[{'title':names.get(kind,'Village update'),'description':'Your village report, straight from the bot.',
            'color':0xEDC45B if kind=='recovery' else 0xE46D5B if kind=='failed' else 0x79B653,
            'fields':fields,'footer':{'text':'CoC Auto • Resource gains use measured storage increases'},
            'timestamp':datetime.now(timezone.utc).isoformat()}]}

    def notify(self, kind, stats, force=False):
        with self.lock:
            options=dict(self.options)
            event='ended' if kind in ('failed','stopped') else kind
            if not options['url'] or (not force and (not options['enabled'] or event not in options['events'])): return False
            now=self.clock()
            if not force and kind=='recovery' and now-self.last_recovery<60: return False
            if not force and kind=='summary' and now-self.last_summary<options['interval']*60: return False
            payload=self.embed(kind,stats,options['ping_id'])
            if not WEBHOOK.fullmatch(options['url']): return False
            try: self.jobs.put_nowait((self.generation,options['url'],payload))
            except queue.Full: self.status='Queue full; update skipped'; return False
            if kind=='recovery': self.last_recovery=now
            if kind=='summary': self.last_summary=now
            self.status='Sending…'
            if self.thread is None:
                self.thread=threading.Thread(target=self.work,daemon=True);self.thread.start()
            return True

    def start_monitor(self, provider):
        with self.lock:
            self.provider = provider
            if self.options['enabled'] and self.options['url'] and self.thread is None:
                self.thread=threading.Thread(target=self.work,daemon=True);self.thread.start()

    def work(self):
        while True:
            try:
                generation,url,payload=self.jobs.get(timeout=30)
            except queue.Empty:
                try:
                    stats=self.provider() if self.provider else None
                    if stats is not None: self.notify('summary',stats)
                except Exception: pass
                continue
            try:
                with self.lock:
                    if generation != self.generation: continue
                self.transport(url,payload)
                with self.lock: self.status='Last message delivered'
            except Exception:
                # Never return/log exceptions that may contain the credential-bearing URL.
                with self.lock: self.status='Delivery failed. Check the webhook and connection.'
            finally: self.jobs.task_done()

    @staticmethod
    def post(url,payload):
        opener=request.build_opener(NoRedirect())
        data=json.dumps(payload).encode()
        for attempt in range(2):
            try:
                with opener.open(request.Request(url+'?wait=true',data=data,headers={'Content-Type':'application/json','User-Agent':'coc-auto-dashboard/1.0'}),timeout=8) as response:
                    response.read(8192)
                return
            except error.HTTPError as exc:
                if exc.code != 429 or attempt: raise
                try: delay=float(json.loads(exc.read(8192)).get('retry_after',1))
                except (ValueError,TypeError): delay=1
                time.sleep(max(.2,min(delay,60)))
