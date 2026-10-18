"""Real directory-entry races in the read-only, bounded storage sample."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import relay_monitor as monitor


class InventoryRaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / 'state'
        self.state.mkdir()
        (self.state / 'retained').write_bytes(b'synthetic retained object' * 4096)
        self.config = {'state': str(self.state)}

    def allocated(self):
        return sum(path.stat().st_blocks * 512
                   for path in [self.state, *self.state.rglob('*')])

    def removing_scan(self, names):
        actual = os.scandir
        @contextmanager
        def scan(fd):
            with actual(fd) as children:
                def entries():
                    for child in children:
                        if child.name in names:
                            (self.state / child.name).unlink()
                        yield child
                yield entries()
        return scan

    def test_disappearing_native_marker_keeps_retained_allocation(self):
        marker = self.state / 'pack-index-reclaim-needed'
        marker.write_bytes(b'synthetic transient marker')
        with patch.object(monitor.os, 'scandir', self.removing_scan({marker.name})):
            result = monitor.state_size(self.config)
        self.assertFalse(marker.exists())
        self.assertEqual(result, {'StateAllocatedBytes': self.allocated()})
        self.assertGreater(result['StateAllocatedBytes'], 0)

    def test_directory_removed_between_stat_and_open_is_transient(self):
        folder = self.state / 'native-temporary-directory'
        folder.mkdir()
        actual = os.open
        def opening(path, flags, *args, **kwargs):
            if path == folder.name and kwargs.get('dir_fd') is not None:
                folder.rmdir()
            return actual(path, flags, *args, **kwargs)
        with patch.object(monitor.os, 'open', opening):
            result = monitor.state_size(self.config)
        self.assertFalse(folder.exists())
        self.assertEqual(result, {'StateAllocatedBytes': self.allocated()})

    def test_stable_nested_inventory_and_hidden_credentials_are_unchanged(self):
        nested = self.state / 'native'
        nested.mkdir()
        (nested / 'object').write_bytes(b'x' * 8192)
        hidden = self.state / 'credentials'
        hidden.mkdir()
        (hidden / 'synthetic').write_bytes(b'x' * 16384)
        excluded = sum(path.stat().st_blocks * 512
                       for path in [hidden, *hidden.rglob('*')])
        self.assertEqual(monitor.state_size(self.config),
                         {'StateAllocatedBytes': self.allocated() - excluded})

    def test_missing_state_root_is_not_an_empty_success(self):
        with self.assertRaises(FileNotFoundError):
            monitor.state_size({'state': str(self.state / 'missing')})

    def test_permission_failure_is_not_a_disappearing_entry(self):
        folder = self.state / 'native'
        folder.mkdir()
        actual = os.open
        def opening(path, flags, *args, **kwargs):
            if path == folder.name and kwargs.get('dir_fd') is not None:
                raise PermissionError(errno.EACCES, 'synthetic denied directory', path)
            return actual(path, flags, *args, **kwargs)
        with patch.object(monitor.os, 'open', opening):
            with self.assertRaises(PermissionError):
                monitor.state_size(self.config)

    def test_symlink_replacement_is_not_followed(self):
        folder = self.state / 'native'
        folder.mkdir()
        outside = self.state.parent / 'outside'
        outside.mkdir()
        (outside / 'private').write_bytes(b'synthetic outside content')
        actual = os.open
        def opening(path, flags, *args, **kwargs):
            if path == folder.name and kwargs.get('dir_fd') is not None:
                folder.rmdir()
                folder.symlink_to(outside, target_is_directory=True)
            return actual(path, flags, *args, **kwargs)
        with patch.object(monitor.os, 'open', opening):
            with self.assertRaises(OSError):
                monitor.state_size(self.config)
        self.assertTrue(folder.is_symlink())

    def test_disappearing_entries_still_consume_entry_budget(self):
        names = {'native-marker-' + str(i) for i in range(4)}
        for name in names:
            (self.state / name).touch()
        with patch.object(monitor.os, 'scandir', self.removing_scan(names)):
            with patch.object(monitor, 'MAX_ENTRIES', 2):
                with self.assertRaises(monitor.MonitorError):
                    monitor.state_size(self.config)

    def test_disappearing_entries_still_consume_time_budget(self):
        marker = self.state / 'native-marker'
        marker.touch()
        with patch.object(monitor.os, 'scandir', self.removing_scan({marker.name})):
            with self.assertRaises(monitor.MonitorError):
                monitor.state_size(self.config, clock=iter([0, 0, 11, 11, 11]).__next__)


if __name__ == '__main__':
    unittest.main()
