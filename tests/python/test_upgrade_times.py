import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_export_catalog as catalog


class UpgradeTimesTests(unittest.TestCase):
    def test_upgrade_times_use_next_level_but_build_times_keep_destination_level(self):
        data = {key:[] for key in ('buildings','traps','heroes','troops','spells','pets','equipment')}
        data['buildings'] = [{'_id':1,'levels':[{'level':1,'build_time':5},{'level':2,'build_time':30}]}]
        for key,identity in [('spells',2),('troops',3),('heroes',4),('pets',5)]:
            data[key] = [{'_id':identity,'levels':[{'level':3,'upgrade_time':3600},{'level':4,'upgrade_time':0}]}]
        identities = {str(i):{'id':str(i),'list':'test','name':str(i)} for i in range(1,6)}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'web').mkdir()
            with patch.object(catalog,'ROOT',root):
                catalog.write_upgrade_times(data,identities)
            text = (root/'web/upgrade-times.js').read_text(encoding='utf-8')
            result = json.loads(text.split('export const UPGRADE_TIMES = ',1)[1].rstrip(';\n'))
        self.assertEqual(result['test/1']['seconds'],{'1':5,'2':30})
        for i in range(2,6):
            self.assertEqual(result[f'test/{i}']['seconds'],{'4':3600})
