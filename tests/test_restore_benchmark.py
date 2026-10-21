"""Permanent restore probes must retain failures and the exact native gates."""
import base64
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_relay as relay

ROOT = Path(__file__).resolve().parents[1]


class RestoreEvidence(unittest.TestCase):
    def test_observer_failure_cannot_replace_the_original_native_error(self):
        archive = b'SYNTHETIC ARCHIVE'
        response = dict(revision=1, pin=dict(revision=1, directory_key='synthetic',
            snapshot_sha256='a' * 64, archive_sha256=relay.digest(archive)),
            archive=base64.b64encode(archive).decode())
        def unavailable(record):
            raise RuntimeError('observer marker')
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(relay.Casita, 'run', side_effect=OSError('original native marker')):
            with self.assertRaisesRegex(OSError, 'original native marker'):
                relay.receive(response, '/synthetic/native', Path(directory) / 'receive',
                              native_timing=unavailable)

    def module(self):
        self.assertTrue((ROOT / 'relay_restore_benchmark.py').is_file(),
                        'fresh-store restoration is missing from the permanent benchmark corpus')
        return importlib.import_module('relay_restore_benchmark')

    def test_restore_case_is_registered_in_all_and_covers_failed_history_horizons(self):
        self.module()
        manifest = json.loads((ROOT / 'benchmarks/manifest.json').read_text())
        self.assertEqual(manifest['all'], ['relay-fsck', 'restore-native'])
        self.assertEqual(manifest['cases']['restore-native']['horizons'], [6, 34, 118, 166, 840])
        self.assertEqual(manifest['cases']['restore-native']['runner'], 'relay_restore_benchmark.py')

    def test_failed_receive_keeps_completed_time_and_partial_native_stages(self):
        benchmark = self.module(); records = []
        def failed(*args, native_timing):
            native_timing(dict(stage='init', start_monotonic_ns=1, elapsed_ns=1,
                               children_cpu_ns=1, outcome='error'))
            raise RuntimeError('PRIVATE /private/operator/secret-token')
        with patch.object(benchmark.relay, 'receive', side_effect=failed):
            with self.assertRaises(RuntimeError):
                benchmark.receive_trial({}, Path('/synthetic/native'), Path('/synthetic/output'), records)
        self.assertEqual(len(records), 1)
        self.assertGreaterEqual(records[0]['milliseconds'], 0)
        self.assertFalse(records[0]['validation_passed'])
        self.assertEqual(records[0]['native_stages'][0]['outcome'], 'error')
        self.assertNotIn('PRIVATE', json.dumps(records))
        self.assertNotIn('/private', json.dumps(records))

    def test_success_gate_requires_all_native_and_corruption_cases(self):
        benchmark = self.module()
        good = dict(all_cases_recorded=True, all_restore_commands_verified=True,
                    all_retained_snapshots_verified=True, full_fsck_on_every_publication=True,
                    corruption_controls={k: True for k in benchmark.CONTROLS})
        benchmark.correctness_gate(good)
        for key in ['all_cases_recorded', 'all_restore_commands_verified',
                    'all_retained_snapshots_verified', 'full_fsck_on_every_publication']:
            with self.subTest(key=key), self.assertRaises(ValueError):
                benchmark.correctness_gate(dict(good, **{key: False}))
        with self.assertRaises(ValueError):
            benchmark.correctness_gate(dict(good, corruption_controls={}))


if __name__ == '__main__':
    unittest.main()
