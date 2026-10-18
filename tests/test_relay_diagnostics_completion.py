"""Failure completion gates and real preparation paths, without paid resources."""
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import relay_monitor as monitor
import context_relay as relay
import relay_workload_controller as controller
from relay_workload_diagnostics import Diagnostics, read_status
from relay_workload_plan import make_plan


class DiagnosticsCompletionTests(unittest.TestCase):
    def test_http_unhandled_failure_uses_safe_frames_without_default_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root)
            server = relay.serve(Mock(timing=diagnostic), 0)
            try:
                with patch('sys.stderr', new_callable=io.StringIO) as stderr:
                    try:
                        raise BrokenPipeError('PRIVATE-FILENAME-OR-COMMAND')
                    except BrokenPipeError:
                        server.handle_error(None, None)
                    self.assertEqual(stderr.getvalue(), '')
                raw = (root / 'failure.json').read_text()
                self.assertNotIn('PRIVATE', raw)
                self.assertEqual(json.loads(raw)['phase'], 'http.accepted')
            finally:
                server.server_close(); diagnostic.close()

    def test_status_reader_accepts_only_bounded_regular_scalar_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            with diagnostic.phase('prepare.seed_history'):
                diagnostic.progress(50)
            path = root / 'preparation-status.json'
            value = read_status(path)
            self.assertEqual((value['stage'], value['state'], value['acknowledged_revisions']),
                             ('prepare.seed_history', 'complete', 50))
            for invalid in [dict(value, private='SYNTHETIC'), dict(value, acknowledged_revisions=True),
                            dict(value, monotonic_ns=-1), dict(value, stage='PRIVATE')]:
                path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    read_status(path)
            path.write_bytes(b' ' * 1025)
            with self.assertRaises(ValueError):
                read_status(path)
            path.unlink(); path.symlink_to(root / 'diagnostics.lock')
            with self.assertRaises(OSError):
                read_status(path)
            path.unlink(); os.mkfifo(path)
            with self.assertRaises(ValueError):
                read_status(path)
            diagnostic.close()

    def test_capped_trace_cannot_publish_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, max_records=1)
            for _ in range(2):
                with diagnostic.span('native.fsck'):
                    pass
            publish = getattr(controller, 'publish_receipt', lambda *args, **kwargs: None)
            with self.assertRaises(RuntimeError):
                publish(root, {'ok': True, 'verification': {}}, diagnostic)
            self.assertFalse((root / 'receipt.json').exists())
            self.assertFalse((root / 'stage-timing-summary.json').is_symlink())
            self.assertFalse(json.loads((root / 'stage-timing-summary.json').read_text())['complete'])
            self.assertEqual(json.loads((root / 'failure.json').read_text())['phase'], 'workload.verify')

    def test_private_trace_paths_refuse_symlinks_and_existing_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            outside = root / 'outside'; outside.mkdir(mode=0o700)
            link = root / 'linked-root'; link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                Diagnostics(link)
            target = root / 'private-target'; target.write_bytes(b'SYNTHETIC PRESERVED')
            (root / 'stage-timing.jsonl').symlink_to(target)
            with self.assertRaises(FileExistsError):
                Diagnostics(root)
            self.assertEqual(target.read_bytes(), b'SYNTHETIC PRESERVED')

    def test_invalid_limits_and_expected_tags_fail_before_unbounded_consumption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            for args in [{'enabled': 'PRIVATE'}, {'max_records': True}, {'max_bytes': 2**40}]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    Diagnostics(root, **args)
            diagnostic = Diagnostics(root)
            for tags in [iter([1]), [True], list(range(5000))]:
                with self.subTest(tags=type(tags)), self.assertRaises(ValueError):
                    diagnostic.finish(expected_tags=tags)
            diagnostic.close()

    def test_preflight_failure_is_persisted_even_when_timing_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(controller.platform, 'system', return_value='synthetic-non-Linux'):
            root = Path(directory) / 'failed'
            with self.assertRaises(ValueError):
                controller.run(sys.executable, root, make_plan(30, 20, 10))
            detail = json.loads((root / 'failure.json').read_text())
            self.assertEqual(detail['phase'], 'prepare.preflight')
            self.assertTrue(detail['frames'])
            self.assertFalse((root / 'stage-timing.jsonl').exists())
            self.assertFalse((root / 'receipt.json').exists())

    def test_seed_failure_keeps_public_call_site_and_no_private_filename(self):
        actual = Path.is_file
        def exists(path):
            return True if str(path) in ('/usr/bin/age', '/usr/bin/age-keygen') else actual(path)
        previous = os.umask(0o077)
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'is_file', exists), \
                    patch.object(controller.platform, 'system', return_value='Linux'), \
                    patch.object(controller.relay, 'Relay', return_value=Mock()):
                root = Path(directory) / 'failed'
                def fail(*args, **kwargs):
                    monitor.state_size({'state': str(root / 'PRIVATE-FILENAME')})
                with patch.object(controller, 'seed_history', side_effect=fail):
                    with self.assertRaises(FileNotFoundError):
                        controller.run(sys.executable, root, make_plan(30, 20, 10), stage_timing=True)
                detail = json.loads((root / 'failure.json').read_text())
                self.assertEqual(detail['phase'], 'prepare.seed_history')
                self.assertTrue(any(frame['file'] == 'relay_monitor.py' for frame in detail['frames']))
                self.assertNotIn('PRIVATE', json.dumps(detail))
                self.assertNotIn(str(root), json.dumps(detail))
                self.assertFalse((root / 'receipt.json').exists())
        finally:
            os.umask(previous)


if __name__ == '__main__':
    unittest.main()
