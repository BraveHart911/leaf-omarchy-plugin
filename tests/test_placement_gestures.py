import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app_layout import ApplicationLayout
from leaf_control import LeafControl
import gestures


class PlacementTests(unittest.TestCase):
    def test_legacy_groups_keep_the_displayed_order_and_membership_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'
            layout = ApplicationLayout(path)
            layout.folders = {'first': {'name': 'First', 'apps': ['a', 'b']},
                              'second': {'name': 'Second', 'apps': ['c', 'd']}}
            layout.order = ['x', 'y', 'uninstalled']
            visible = ['folder:first', 'folder:second', 'x', 'y']
            self.assertTrue(layout.place_entry('folder:first', 'x', visible, after=True))
            restored = ApplicationLayout(path)
            self.assertEqual(restored.order, ['folder:second', 'x', 'folder:first', 'y', 'uninstalled'])
            self.assertEqual(restored.folders, layout.folders)

    def test_save_failure_keeps_the_previous_arrangement(self):
        layout = ApplicationLayout(Path('/unused/layout.json'))
        layout.order = ['a', 'b']
        with patch.object(layout, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                layout.place_entry('b', 'a', ['a', 'b'])
        self.assertEqual(layout.order, ['a', 'b'])

    def test_group_lands_on_next_page_without_off_by_one(self):
        ids = ['folder:a'] + [str(i) for i in range(8)]
        placements = []
        app = SimpleNamespace(app_grid_entry_ids=ids, app_page_index=0, app_page_count=3,
                              app_page_capacity=3, search=SimpleNamespace(get_text=lambda: ''))
        def place(source, target, after=False):
            final = ids[:]
            final.remove(source)
            final.insert(final.index(target) + int(after), source)
            placements.append(final)
            return True
        app._place_grid_entry = place
        self.assertTrue(LeafControl._place_on_adjacent_page(app, 'folder:a', 1))
        self.assertEqual(placements[0].index('folder:a'), 3)
        self.assertFalse(LeafControl._place_on_adjacent_page(app, 'folder:a', -1))


class GestureTests(unittest.TestCase):
    @patch.object(gestures.subprocess, 'run')
    @patch.object(gestures.subprocess, 'check_output', return_value=b'{"class":"editor"}')
    def test_horizontal_gesture_does_not_change_leaf_when_another_app_is_focused(self, read, run):
        gestures.dispatch('left')
        run.assert_not_called()

    @patch.object(gestures.subprocess, 'run')
    @patch.object(gestures.subprocess, 'check_output', return_value=b'{"class":"org.leafcontrol.LeafControl"}')
    def test_direction_maps_to_the_requested_page(self, read, run):
        gestures.dispatch('left')
        self.assertIn('next-page', run.call_args.args[0])
        gestures.dispatch('right')
        self.assertIn('previous-page', run.call_args.args[0])

    @patch.object(gestures.subprocess, 'Popen')
    def test_up_opens_fullscreen_apps(self, start):
        gestures.dispatch('up')
        self.assertEqual(start.call_args.args[0][-2:], ['--apps', '--fullscreen'])
