from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from coc_auto.dashboard_metrics import Statistics
from coc_auto.discord_hooks import DiscordHooks, HookRefused
from coc_auto import remote

URL='https://discord.com/api/webhooks/123456789012345678/'+ 'x'*64

class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.now=1000000
        self.stats=Statistics(clock=lambda:self.now)
        self.run=self.stats.start(['--walls','home'])
    def consume(self,line):return self.stats.consume(line,self.run)
    def test_loot_offers_are_not_collected_resources(self):
        self.consume("INFO: Loot terbaca: {'gold': 5000000, 'elixir': 6000000}")
        self.assertEqual(self.stats.snapshot()['totals']['gold'],0)
        self.assertEqual(self.stats.snapshot()['totals']['searches'],1)
    def test_storage_deltas_reset_after_spending_and_do_not_count_baseline(self):
        self.consume("INFO: Gudang home: {'gold': '1,000/2,000', 'dark':'100/500'}")
        self.consume("INFO: Gudang home: {'gold': '1,500/2,000', 'dark':'150/500'}")
        self.consume('INFO: Tembok di-upgrade dengan gold')
        self.consume("INFO: Gudang home: {'gold': '200/2,000'}")
        self.consume("INFO: Gudang home: {'gold': '800/2,000'}")
        totals=self.stats.snapshot()['totals']
        self.assertEqual(totals['gold'],1100)
        self.assertEqual(totals['dark'],50)
        self.assertEqual(totals['walls'],1)
    def test_another_accounts_fuller_storages_are_not_counted_as_a_gain(self):
        self.consume("INFO: Gudang home: {'gold': '1,000/2,000'}")
        self.consume("INFO: Gudang home: {'gold': '1,400/2,000'}")
        # Still the same village after a recovery: its gains go on counting.
        self.consume('INFO: Akun MainVillage (#PPPPPPPP) sudah dimuat')
        self.consume("INFO: Gudang home: {'gold': '1,500/2,000'}")
        for loaded in ('INFO: Akun SecondOne (#QQQQQQQQ) dimuat', 'INFO: Account MainVillage (#PPPPPPPP) loaded'):
            self.consume(loaded)
            self.consume("INFO: Gudang home: {'gold': '9,000,000/10,000,000'}")
            self.consume("INFO: Gudang home: {'gold': '9,000,300/10,000,000'}")
        self.assertEqual(self.stats.snapshot()['totals']['gold'],1100)
    def test_confirmed_bulk_counts_and_recoveries(self):
        self.consume('INFO: 12 tembok di-upgrade sekaligus dengan elixir')
        self.consume('INFO: Serangan selesai: home (12)')
        self.consume('WARNING: Layar macet; pemulihan 1/20')
        self.consume('WARNING: Number unreadable')
        totals=self.stats.snapshot()['totals']
        self.assertEqual([totals[k] for k in ('walls','attacks','recoveries')],[12,1,1])
    def test_building_upgrades_are_counted_in_either_language(self):
        run=self.stats.start(['--buildings','--live'])
        self.assertEqual(self.stats.consume('INFO: Bangunan di-upgrade: Tambang Emas ke level 12',run),'upgrade')
        self.assertEqual(self.stats.consume('INFO: Building upgraded: Archer Tower to level 8',run),'upgrade')
        snapshot=self.stats.snapshot()
        self.assertEqual(snapshot['runs'][0]['task'],'Building upgrades')
        self.assertEqual(snapshot['totals']['upgrades'],2)
        self.assertEqual(snapshot['recent'][0]['detail'],'Archer Tower upgraded to level 8')
        fields=DiscordHooks.embed('summary',snapshot)['embeds'][0]['fields']
        self.assertIn(('🏗️ Upgrades','**2**'),[(f['name'],f['value']) for f in fields])
    def test_research_pet_and_equipment_upgrades_count_like_buildings(self):
        run=self.stats.start(['--buildings','--live'])
        for line,detail in [('INFO: Riset di-upgrade: Valkyrie ke level 6','Valkyrie upgraded to level 6'),
                            ('INFO: Research upgraded: Valkyrie to level 7','Valkyrie upgraded to level 7'),
                            ('INFO: Hewan di-upgrade: L.A.S.S.I ke level 3','L.A.S.S.I upgraded to level 3'),
                            ('INFO: Pet upgraded: Mighty Yak to level 2','Mighty Yak upgraded to level 2'),
                            ('INFO: Peralatan di-upgrade: Kitab Mahasakti ke level 9','Kitab Mahasakti upgraded to level 9'),
                            ('INFO: Equipment upgraded: Giant Gauntlet to level 4','Giant Gauntlet upgraded to level 4')]:
            self.assertEqual(self.stats.consume(line,run),'upgrade')
            self.assertEqual(self.stats.snapshot()['recent'][0]['detail'],detail)
        snapshot=self.stats.snapshot()
        self.assertEqual((snapshot['totals']['upgrades'],snapshot['totals']['walls']),(6,0))
        fields=DiscordHooks.embed('summary',snapshot)['embeds'][0]['fields']
        self.assertIn(('🏗️ Upgrades','**6**'),[(f['name'],f['value']) for f in fields])
        self.assertIsNone(self.stats.consume('INFO: Riset dilewati: Valkyrie ke level 6',run))
    def test_history_and_session_isolation(self):
        self.consume('INFO: Serangan selesai: home (1)')
        self.now+=120
        self.stats.finish(self.run,0)
        second=self.stats.start(['--until-full'])
        self.stats.consume('INFO: Serangan selesai: builder (1)',second)
        self.assertEqual(self.stats.snapshot()['totals']['attacks'],1)
        self.assertEqual(self.stats.snapshot('24h')['totals']['attacks'],2)
        self.assertEqual(self.stats.snapshot(run_id=self.run)['totals']['attacks'],1)
    def test_database_survives_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'stats.db'
            store=Statistics(path,clock=lambda:self.now)
            run=store.start(['--walls','home']);store.record(run,'walls',walls=5);store.finish(run,0)
            store.db.close()
            other=Statistics(path,clock=lambda:self.now)
            self.assertEqual(other.snapshot('7d')['totals']['walls'],5)
            other.db.close()  # Windows cannot delete an open database file

class DiscordTests(unittest.TestCase):
    def options(self,**changes):return dict(url=URL,ping_id='123456789012345678',enabled=True,events=['ended','recovery','summary'],interval=30,**changes)
    def test_only_the_explicit_user_can_be_pinged(self):
        payload=DiscordHooks.embed('failed',{'totals':{'gold':1000}},'123456789012345678')
        self.assertEqual(payload['allowed_mentions'],{'parse':[],'users':['123456789012345678']})
        self.assertEqual(payload['content'],'<@123456789012345678>')
        self.assertEqual(DiscordHooks.embed('summary',{},'123456789012345678')['content'],'')
    def test_secret_is_saved_privately_and_not_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'discord.json';hook=DiscordHooks(path)
            result=hook.save(self.options())
            self.assertNotIn(URL,json.dumps(result))
            self.assertNotIn('url',result)
            if os.name != 'nt':  # Windows has no owner-only mode bits
                self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertTrue(DiscordHooks(path).public()['configured'])
    def test_arbitrary_hosts_and_invalid_ids_are_rejected(self):
        for url in ['https://evil.com/api/webhooks/123/test',URL+'?redirect=https://evil.com','http://discord.com/api/webhooks/123/test']:
            with self.assertRaises(HookRefused):DiscordHooks().save(self.options()|{'url':url})
        with self.assertRaises(HookRefused):DiscordHooks().save(self.options()|{'ping_id':'@everyone'})
    def test_dispatch_is_async_and_recovery_pings_are_cooled_down(self):
        delivered=[];hook=DiscordHooks(transport=lambda url,payload:delivered.append(payload))
        hook.save(self.options())
        self.assertTrue(hook.notify('recovery',{'totals':{}}))
        self.assertFalse(hook.notify('recovery',{'totals':{}}))
        hook.jobs.join()
        self.assertEqual(len(delivered),1)
        self.assertEqual(hook.public()['status'],'Last message delivered')
    def test_errors_do_not_leak_webhook_tokens(self):
        def fail(url,payload):raise ValueError(url)
        hook=DiscordHooks(transport=fail);hook.save(self.options());hook.notify('test',{},force=True);hook.jobs.join()
        self.assertNotIn('x'*64,json.dumps(hook.public()))
    def test_remote_channel_cannot_configure_webhook(self):
        key='coc_test_dashboard_0123456789abcdefghijklmnop'
        supervisor=remote.Supervisor(key,[],Mock())
        body=json.dumps({'action':'set_discord','args':self.options(),'ts':time.time(),'nonce':'local-only'})
        result=supervisor.command(body,remote.sign(key,body))
        self.assertFalse(result['ok'])
        self.assertFalse(supervisor.discord.public()['configured'])
    def test_local_command_saves_without_echoing_the_secret(self):
        key='coc_test_dashboard_0123456789abcdefghijklmnop';supervisor=remote.Supervisor(key,[],Mock())
        body=json.dumps({'action':'set_discord','args':self.options()|{'enabled':False},'ts':time.time(),'nonce':'save-local'})
        result=supervisor.command(body,remote.sign(key,body),local=True)
        self.assertTrue(result['ok'])
        self.assertNotIn(URL,json.dumps(result))
        self.assertTrue(supervisor.discord.public()['configured'])

class WorkerStatsIntegrationTests(unittest.TestCase):
    def test_worker_events_reach_the_signed_stats_endpoint(self):
        import sys
        key='coc_test_dashboard_0123456789abcdefghijklmnop'
        script="\n".join([
            "print(\"INFO: Gudang home: {'gold': '1,000/2,000'}\")",
            "print(\"INFO: Loot terbaca: {'gold': 9000000}\")",
            "print(\"INFO: Gudang home: {'gold': '1,500/2,000'}\")",
            "print('INFO: Serangan selesai: home (1)')",
            "print('INFO: 12 tembok di-upgrade sekaligus dengan elixir')",
        ])
        supervisor=remote.Supervisor(key,[sys.executable,'-c',script],Mock())
        supervisor.start([])
        supervisor.child.wait(timeout=5)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            data=supervisor.statistics.snapshot()
            if data['runs'] and data['runs'][0]['ended'] is not None:break
            time.sleep(.01)
        body=json.dumps({'action':'get_stats','args':{'period':'run'},'ts':time.time(),'nonce':'read-stats'})
        result=supervisor.command(body,remote.sign(key,body))
        self.assertTrue(result['ok'])
        self.assertEqual(result['data']['totals']['gold'],500)
        self.assertEqual(result['data']['totals']['attacks'],1)
        self.assertEqual(result['data']['totals']['walls'],12)
        self.assertEqual(result['data']['measured'],['gold'])
        self.assertEqual(result['data']['runs'][0]['status'],'completed')
        supervisor.child.stdout.close()

class SeparateVillageResourcesTests(unittest.TestCase):
    def test_home_and_builder_totals_and_chart_buckets_stay_separate(self):
        stats=Statistics(clock=lambda:1000000)
        run=stats.start(['--until-full'])
        for line in [
            "INFO: Gudang home: {'gold': '1,000/5,000', 'elixir':'2,000/6,000', 'dark':'100/500'}",
            "INFO: Gudang builder: {'gold': '3,000/9,000', 'elixir':'4,000/9,000'}",
            "INFO: Gudang home: {'gold': '1,500/5,000', 'elixir':'2,600/6,000', 'dark':'150/500'}",
            "INFO: Gudang builder: {'gold': '4,700/9,000', 'elixir':'5,800/9,000'}",
        ]:stats.consume(line,run)
        data=stats.snapshot()
        expected={'gold':500,'elixir':600,'dark':50,'builder_gold':1700,'builder_elixir':1800}
        for key,value in expected.items():
            self.assertEqual(data['totals'][key],value)
            self.assertEqual(data['buckets'][0][key],value)
        self.assertEqual(set(data['measured']),set(expected))

    def test_webhook_fields_name_both_villages_explicitly(self):
        payload=DiscordHooks.embed('summary',{'totals':{'gold':500,'builder_gold':1700,'elixir':600,'builder_elixir':1800}})
        fields={f['name']:f['value'] for f in payload['embeds'][0]['fields']}
        self.assertEqual(fields['🪙 Home Gold'],'**500**')
        self.assertEqual(fields['🟨 Builder Gold'],'**1,700**')
        self.assertEqual(fields['💧 Home Elixir'],'**600**')
        self.assertEqual(fields['🟣 Builder Elixir'],'**1,800**')

    def test_ambiguous_legacy_resource_sums_are_not_assigned_to_home(self):
        stats=Statistics(clock=lambda:1000000);run=stats.start(['--until-full'])
        stats.record(run,'resources',gold=9000,elixir=8000)
        stats.record(run,'attack',attacks=1)
        stats.db.execute('UPDATE events SET resource_version=0');stats.db.commit()
        data=stats.snapshot()
        self.assertEqual(data['totals']['gold'],0)
        self.assertEqual(data['totals']['builder_gold'],0)
        self.assertEqual(data['totals']['attacks'],1)


class DashboardAssetTests(unittest.TestCase):
    def test_stats_icons_are_served_by_the_device_dashboard(self):
        from urllib.request import urlopen
        web = REPO_ROOT / 'web'
        server = remote.serve_dashboard(Mock(), web, 0)
        try:
            for asset, content_type in [('attacks.png', 'image/png'), ('walls.webp', 'image/webp')]:
                with self.subTest(asset=asset):
                    with urlopen(f'http://127.0.0.1:{server.server_port}/icons/stats/{asset}') as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), content_type)
                        self.assertEqual(response.read(), (web / 'icons/stats' / asset).read_bytes())
        finally:
            server.shutdown()
            server.server_close()
