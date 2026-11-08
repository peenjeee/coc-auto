import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dashboard_metrics
import messages
import upgrade_history as history
import upgrades


def hero(target=None):
    return {'id':'royal_champion','name':'Jawara Royale','next_level':target,'time':'1h'}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def export(self, entries, section='heroes', timestamp=1000, tag='#AAA'):
        data = {'tag':tag,'timestamp':timestamp,section:entries}
        path = self.root/history.EXPORT
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data))
        os.utime(path,(timestamp,timestamp))
        return data

    def receipts(self):
        db = history.connect(self.root)
        try:
            return [dict(row) for row in db.execute('SELECT * FROM upgrades ORDER BY id')]
        finally:
            db.close()

    def test_pending_synced_upgrade_is_included_before_incrementing_successive_receipts(self):
        self.export([{'data':28000004,'lvl':46,'timer':300}])
        self.assertEqual(history.record(self.root,'buildings',hero(),3600,now=1400),48)
        # A new connection represents a worker restart; the history still advances.
        self.assertEqual(history.record(self.root,'buildings',hero(),3600,now=5000),49)
        self.assertEqual([(r['from_level'],r['target_level']) for r in self.receipts()],[(47,48),(48,49)])

    def test_fresh_sync_does_not_replay_receipts_already_reflected_in_the_export(self):
        self.export([{'data':28000004,'lvl':46}])
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=1000.1),47)
        data = self.export([{'data':28000004,'lvl':46,'timer':99}],timestamp=1000)
        history.checkpoint(self.root,data)
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=1200),48)

    def test_changed_export_cannot_reuse_an_old_checkpoint(self):
        data=self.export([{'data':28000004,'lvl':46}])
        history.checkpoint(self.root,data)
        self.export([{'data':28000004,'lvl':50}],timestamp=2000)
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=2100),51)

    def test_visual_level_is_preferred_and_future_receipts_use_it(self):
        self.export([{'data':28000004,'lvl':46}])
        self.assertEqual(history.record(self.root,'buildings',hero(52),100,now=1100),52)
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=1300),53)

    def test_grouped_buildings_advance_one_copy_per_confirmed_upgrade(self):
        self.export([{'data':1000008,'lvl':5,'cnt':2}],section='buildings')
        row={'id':'cannon','name':'Meriam','next_level':None}
        self.assertEqual(history.record(self.root,'buildings',row,100,now=1100),6)
        self.assertEqual(history.record(self.root,'buildings',row,100,now=1101),6)
        self.assertEqual(history.record(self.root,'buildings',row,100,now=1300),7)

    def test_mixed_level_copies_are_not_guessed_when_badge_is_missing(self):
        self.export([{'data':1000008,'lvl':5,'cnt':1},{'data':1000008,'lvl':6,'cnt':1}],section='buildings')
        row={'id':'cannon','name':'Meriam','next_level':None}
        self.assertIsNone(history.record(self.root,'buildings',row,100,now=1100))
        self.assertEqual(self.receipts()[0]['evidence'],'unknown')

    def test_another_account_does_not_inherit_prior_levels(self):
        self.export([{'data':28000004,'lvl':46}])
        history.record(self.root,'buildings',hero(),100,now=1100)
        data=self.export([{'data':28000004,'lvl':10}],timestamp=2000,tag='#BBB')
        history.checkpoint(self.root,data)
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=2100),11)

    def test_without_baseline_the_success_is_recorded_without_inventing_a_level(self):
        self.assertIsNone(history.record(self.root,'buildings',hero(),100,now=1100))
        self.assertIsNone(self.receipts()[0]['target_level'])

    def test_reporting_does_not_change_the_row_used_by_purchase_checks(self):
        self.export([{'data':28000004,'lvl':46,'timer':200}])
        row=hero()
        before=dict(row)
        with self.assertLogs('upgrades','INFO') as logs:
            upgrades.report_upgrade(SimpleNamespace(root=self.root),'buildings',row)
        self.assertEqual(row,before)
        self.assertIn('ke level 48',logs.output[0])

    def test_missing_evidence_never_prints_none_and_still_counts_the_upgrade(self):
        with self.assertLogs('upgrades','INFO') as logs:
            upgrades.report_upgrade(SimpleNamespace(root=self.root),'buildings',hero())
        self.assertNotIn('None',logs.output[0])
        stats=dashboard_metrics.Statistics()
        self.addCleanup(stats.db.close)
        run=stats.start(['--buildings'])
        self.assertEqual(stats.consume('INFO: Bangunan di-upgrade: Jawara Royale',run),'upgrade')
        self.assertEqual(stats.snapshot()['totals']['upgrades'],1)

    def test_journal_failure_does_not_turn_a_confirmed_purchase_into_a_retry(self):
        with patch.object(history,'record',side_effect=sqlite3.OperationalError('locked')),self.assertLogs('upgrades','INFO') as logs:
            upgrades.report_upgrade(SimpleNamespace(root=self.root),'buildings',hero(48))
        self.assertIn('ke level 48',logs.output[-1])

    def test_a_lost_receipt_disables_inference_until_a_new_sync(self):
        data=self.export([{'data':28000004,'lvl':46}])
        history.invalidate(self.root)
        self.assertIsNone(history.record(self.root,'buildings',hero(),100,now=1100))
        data=self.export([{'data':28000004,'lvl':47}],timestamp=1200)
        history.checkpoint(self.root,data)
        self.assertEqual(history.record(self.root,'buildings',hero(),100,now=1300),48)


class DisplayTests(unittest.TestCase):
    def tearDown(self):
        messages.use('id')

    def test_duration_units_and_zero(self):
        self.assertEqual(messages.duration(12125),'3h 22m 5s')
        self.assertEqual(messages.duration(3*86400+12*3600+30*60+10),'3d 12h 30m 10s')
        self.assertEqual(messages.duration(0),'0s')

    def test_retained_logs_names_waits_and_legacy_none(self):
        messages.use('en')
        self.assertEqual(messages.translate('INFO: Bangunan di-upgrade: Jawara Royale ke level 48'),
                         'INFO: Building upgraded: Royal Champion to level 48')
        self.assertEqual(messages.translate('INFO: Building upgraded: Jawara Royale to level None'),
                         'INFO: Building upgraded: Royal Champion')
        self.assertEqual(messages.translate('INFO: Menunggu 12125 detik: belum ada pilihan yang bisa di-upgrade sekarang'),
                         'INFO: Waiting 3h 22m 5s: nothing picked can be upgraded right now')

    def test_wait_formatting_does_not_change_how_long_the_game_is_closed(self):
        events=[]
        runner=SimpleNamespace(stop_at=None,poll=1,live=True,
                               device=SimpleNamespace(stop_game=lambda:events.append('closed')),
                               recover=lambda:events.append('open'))
        with patch.object(upgrades.time,'sleep',lambda seconds:events.append(seconds)),self.assertLogs('upgrades','INFO') as logs:
            upgrades.pause(runner,12125,'x')
        self.assertEqual(events,['closed',12125,'open'])
        self.assertTrue(all('3h 22m 5s' in line for line in logs.output))


if __name__=='__main__':
    unittest.main()
