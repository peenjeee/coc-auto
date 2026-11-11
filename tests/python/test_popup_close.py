from coc_auto.paths import RESOURCE_ROOT, REPO_ROOT
from pathlib import Path as _TestPath
FIXTURES = _TestPath(__file__).resolve().parents[1] / "fixtures"
from pathlib import Path
import unittest

import cv2
import numpy as np

from coc_auto import popup_close

ROOT = RESOURCE_ROOT


def frame(name):
    return cv2.imread(str(FIXTURES / f'{name}.png'))


class CloseButtonTests(unittest.TestCase):
    def test_a_popup_never_seen_is_closed_by_its_red_x(self):
        # A new season's Gold Pass offer, whose X sits elsewhere and on another background than the known popups'.
        (x, y), score = popup_close.close_button(frame('gold-pass-season'))
        self.assertLess(abs(x - 1116) + abs(y - 83), 8)
        self.assertGreaterEqual(score, popup_close.CLOSE_MIN_SCORE)
        for name in ('research-confirm', 'gear-confirm-one-ore'):
            with self.subTest(name=name):
                (x, y), _ = popup_close.close_button(frame(name))
                self.assertLess(abs(x - 1131) + abs(y - 56), 8)

    def test_the_village_and_the_builder_list_show_no_close_button(self):
        list_frame = np.zeros((720, 1280, 3), np.uint8)
        list_frame[:, 936:] = frame('building-list-top')
        for image in (frame('home-wall-selected'), list_frame):
            self.assertIsNone(popup_close.close_button(image))


if __name__ == '__main__':
    unittest.main()
