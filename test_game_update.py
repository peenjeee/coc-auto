from pathlib import Path
import unittest

import game_update

DUMP = (Path(__file__).parent / 'fixtures' / 'playstore-update-offer.xml').read_bytes()
PAGE = (Path(__file__).parent / 'fixtures' / 'playstore-game-page.xml').read_bytes()
DUMPSYS = '''  Package [com.supercell.clashofclans] (42d0666):
    versionCode=180647003 minSdk=24 targetSdk=36
    versionName=18.647.2
    lastUpdateTime=2026-10-24 02:39:47
'''


class UpdateButtonTest(unittest.TestCase):
    def test_the_update_button_of_the_play_store_dialog_is_found(self):
        self.assertEqual(game_update.update_button(DUMP), [829, 497])

    def test_the_dump_message_around_the_xml_is_ignored(self):
        self.assertEqual(game_update.update_button(DUMP + b'UI hierchary dumped to: /dev/tty'), [829, 497])

    def test_on_the_game_store_page_the_button_around_the_label_is_tapped(self):
        # There the "Update" text itself is not clickable; the box around it is [329,430][604,502].
        self.assertEqual(game_update.update_button(PAGE), [466, 466])

    def test_an_indonesian_update_button_counts(self):
        self.assertEqual(game_update.update_button(DUMP.replace(b'text="Update"', b'text="Perbarui"')), [829, 497])

    def test_an_install_button_is_never_tapped(self):
        # The store page lists sponsored apps; the game itself is always installed already.
        self.assertIsNone(game_update.update_button(DUMP.replace(b'text="Update"', b'text="Instal"')))
        self.assertIsNone(game_update.update_button(PAGE.replace(b'"Update"', b'"Install"')))

    def test_no_button_while_downloading(self):
        self.assertIsNone(game_update.update_button(DUMP.replace(b'text="Update"', b'text="Batal"')))
        self.assertIsNone(game_update.update_button(PAGE.replace(b'"Update"', b'"Batal"')))

    def test_a_disabled_button_is_not_tapped(self):
        start = DUMP.index(b'text="Update"')
        node = DUMP[DUMP.rindex(b'<node', 0, start):DUMP.index(b'>', start)]
        self.assertIsNone(game_update.update_button(DUMP.replace(node, node.replace(b'enabled="true"',
                                                                                   b'enabled="false"'))))

    def test_another_app_is_never_updated(self):
        self.assertIsNone(game_update.update_button(DUMP.replace(b'Clash of Clans', b'Other Game')))

    def test_a_button_outside_the_play_store_is_never_tapped(self):
        self.assertIsNone(game_update.update_button(DUMP.replace(b'com.android.vending', b'com.example.app')))

    def test_an_error_instead_of_a_dump_finds_nothing(self):
        self.assertIsNone(game_update.update_button(b'ERROR: could not get idle state.'))
        self.assertIsNone(game_update.update_button(b'<hierarchy><node text="Update"></hierarchy>'))


class InstalledVersionTest(unittest.TestCase):
    def test_the_version_name_code_and_install_time_are_read(self):
        self.assertEqual(game_update.installed_version(DUMPSYS), '18.647.2 (180647003, 2026-10-24 02:39:47)')

    def test_a_missing_package_has_no_version(self):
        self.assertIsNone(game_update.installed_version(''))
        self.assertIsNone(game_update.installed_version(DUMPSYS.replace('lastUpdateTime', 'firstInstallTime')))


if __name__ == '__main__':
    unittest.main()
