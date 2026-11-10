from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from coc_auto import bot
from coc_auto import village_export as export

ROOT = RESOURCE_ROOT


def example():
    data = {'tag':'#ABC123', 'timestamp':1000}
    data.update({section:[] for sections in export.SECTIONS.values() for section in sections})
    data['buildings'] = [{'data':1000008,'lvl':12,'cnt':3}, {'data':1000008,'lvl':13,'cnt':2},
                         {'data':1000008,'lvl':12,'timer':123}, {'data':1000010,'lvl':10,'cnt':200}]
    data['buildings2'] = [{'data':1000041,'lvl':4,'cnt':2}]
    data['equipment'] = [{'data':90000013,'lvl':9}]
    return data


class ExportDataTests(unittest.TestCase):
    def test_real_schema_keeps_level_groups_counts_and_active_upgrade_separate(self):
        data = export.parse(json.dumps(example()),now=1001,copied_after=1000)
        rows = export.rows(data)
        cannons = [r for r in rows['buildings'] if r['id']=='cannon']
        self.assertEqual(sorted((r['current_level'],r['count']) for r in cannons),[(12,1),(12,3),(13,2)])
        running = next(r for r in cannons if r['section']=='in_progress')
        self.assertEqual((running['current_level'],running['next_level'],running['seconds']),(12,13,123))
        self.assertTrue(all(r['cost'] is None for r in cannons))
        self.assertEqual(rows['builder'][0]['id'],'double_cannon')
        self.assertEqual(rows['gear'][0]['name'],'Rocket Spear')

    def test_stale_future_unrelated_and_partial_exports_are_refused(self):
        bad = ['hello', '{}', '{', json.dumps({**example(),'timestamp':900}),
               json.dumps({**example(),'timestamp':1010}), json.dumps({**example(),'units':None})]
        for value in bad:
            with self.subTest(value=value[:40]), self.assertRaises(export.ExportError):
                export.parse(value,now=1001,copied_after=1000)

    def test_invalid_counts_levels_and_timers_do_not_become_guessed_values(self):
        for key,value in [('lvl',None),('lvl',True),('lvl',-1),('cnt',0),('cnt',False),('timer',-1),('data','1000008')]:
            data = example()
            data['buildings'][0][key] = value
            with self.subTest(key=key,value=value), self.assertRaises(export.ExportError):
                export.parse(json.dumps(data),now=1001,copied_after=1000)

    def test_unknown_items_and_modular_buildings_are_visible_but_not_selectable(self):
        data = example()
        data['buildings'] += [{'data':1999999,'lvl':3}, {'data':1000097,'types':[{'modules':[{'data':102000033,'lvl':2}]}]}]
        rows = export.rows(data)['buildings']
        unknown = next(r for r in rows if r['data_id']==1999999)
        modular = next(r for r in rows if r['data_id']==1000097)
        self.assertFalse(unknown['selectable'])
        self.assertEqual(unknown['current_level'],3)
        self.assertIsNone(modular['current_level'])
        self.assertIsNone(modular['next_level'])
        self.assertFalse(modular['selectable'])
        self.assertEqual(modular['modules'][0]['modules'][0]['lvl'],2)
        self.assertFalse(next(r for r in rows if r['id']=='wall')['selectable'])

    def test_home_and_builder_ids_are_not_confused(self):
        data = example()
        data['buildings'] = data['buildings2']
        with self.assertRaises(export.ExportError):
            export.rows(data)

    def test_names_can_be_displayed_in_indonesian_without_reading_text(self):
        row = next(r for r in export.rows(example(),language='id')['buildings'] if r['id']=='cannon')
        self.assertEqual(row['name'],'Meriam')

    def test_every_selectable_game_identity_is_accepted_by_its_action_catalog(self):
        from coc_auto import buildings
        for identity in export.catalog().values():
            key = identity['id']
            if not key or key == 'wall':
                continue
            listing = identity['list']
            value = {'targets':[{'id':key,'name':identity['name'],'level':None}]}
            if listing == 'buildings':
                value['keep_builders'] = 0
            with self.subTest(listing=listing,key=key):
                buildings.selection(value,listing)


class FakeGame:
    def __init__(self, copy=True, export_button=True):
        self.state, self.inputs = 'home', []
        self.seconds, self.sequence = 0.,1
        self.copy, self.export_button = copy,export_button
        self.frames = {name:np.zeros((720,1280,3),np.uint8) for name in ('home','main','more','export')}
        def place(state,name,x,y):
            template = cv2.imread(str(ROOT/'calibration/export'/name))
            h,w = template.shape[:2]
            self.frames[state][y:y+h,x:x+w] = template
        for state in ('main','more','export'):
            place(state,'close.png',1096,34)
            place(state,'settings-id.png',945,30)
        place('main','more-id.png',551,617)
        for state in ('more','export'):
            place(state,'back.png',81,33)
        if export_button:
            place('export','export-id.png',272,441)
            self.frames['export'][444:470,850:1010] = (0,200,140)
        detector = SimpleNamespace(detect=lambda image:'home' if self.state=='home' else 'unknown')
        self.runner = SimpleNamespace(root=ROOT,device=self,detector=detector,config={})

    def capture(self):
        self.seconds += .05
        return self.frames[self.state].copy()

    def sleep(self,seconds):
        self.seconds += seconds

    def tap(self,point):
        self.inputs.append(tuple(point))
        if point == [60,60]: self.state='main'
        elif point == [640,637]: self.state='more'
        elif point == [930,456] and self.copy: self.sequence+=1
        elif point == [1119,56]: self.state='home'

    def command(self,*args):
        self.inputs.append(args)
        self.state='export'

    def clipboard(self):
        return self.sequence,json.dumps(example())

    def scanner(self):
        return export.Scanner(self.runner,clipboard=self.clipboard,sleep=self.sleep,
                              clock=lambda:self.seconds,wall_clock=lambda:1000+self.seconds)


class NavigationTests(unittest.TestCase):
    def game(self,**options):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        game = FakeGame(**options)
        game.runner.root = Path(temporary.name)
        shutil.copytree(ROOT/'calibration/export',game.runner.root/'calibration/export')
        return game

    def test_navigation_copies_exact_export_and_returns_to_village(self):
        game = self.game()
        data = game.scanner().read()
        self.assertEqual(data,example())
        self.assertEqual(game.state,'home')
        self.assertIn((930,456),game.inputs)
        self.assertEqual(game.inputs[-1],(1119,56))
        self.assertLess(game.seconds,20)

    def test_export_prepared_when_profile_opens_is_valid_after_a_slower_scroll(self):
        game = self.game()
        original_command = game.command
        def delayed_scroll(*args):
            game.seconds += 6
            return original_command(*args)
        game.command = delayed_scroll
        self.assertEqual(game.scanner().read(),example())
        self.assertEqual(game.state,'home')

    def test_unchanged_clipboard_times_out_and_closes_settings(self):
        game = self.game(copy=False)
        with self.assertRaisesRegex(export.ExportError,'synchronization'):
            game.scanner().read()
        self.assertEqual(game.state,'home')
        self.assertLess(game.seconds,20)

    def test_missing_export_never_taps_an_api_token_or_another_setting(self):
        game = self.game(export_button=False)
        with self.assertRaisesRegex(export.ExportError,'not found'):
            game.scanner().read()
        self.assertEqual(game.state,'home')
        taps = [p for p in game.inputs if len(p)==2]
        self.assertEqual(taps,[(60,60),(1010,60),(640,637),(1119,56)])
        self.assertLess(game.seconds,30)

    def test_already_open_settings_pages_can_be_synced_without_reopening_profile(self):
        for state in ('main','more','export'):
            game = self.game()
            game.state = state
            with self.subTest(state=state):
                self.assertEqual(game.scanner().read(),example())
                self.assertNotIn((60,60),game.inputs)
                self.assertEqual(game.state,'home')


if __name__ == '__main__':
    unittest.main()
