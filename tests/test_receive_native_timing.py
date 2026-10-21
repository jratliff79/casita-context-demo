"""Native diagnosis must retain every restore gate and never expose command data."""
import base64
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_relay as relay
from relay_load import fixture


class ReceiveNativeTiming(unittest.TestCase):
    def exercise(self, fail=None, observer=None):
        self.assertIn('native_timing', inspect.signature(relay.receive).parameters,
                      'client-native command timings are missing from receive')
        raw = relay.canonical(dict(schema=relay.SCHEMA, workspace='synthetic-timing', revision=1,
            parent_revision=0, author='alpha', created_at='2026-10-21T00:00:00Z',
            tasks={'task-alpha-0': fixture('task-alpha-0', 1, 4096)}, memory=[], decision=None))
        archive = b'SYNTHETIC ARCHIVE'
        pin = dict(revision=1, directory_key='synthetic-directory',
                   snapshot_sha256=relay.digest(raw), archive_sha256=relay.digest(archive))
        response = dict(workspace='synthetic-timing', revision=1, pin=pin,
                        archive=base64.b64encode(archive).decode())
        calls, records = [], []
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'restore'
            def run(store, *args):
                stage = '_'.join(args[:2]) if args[0] == 'archive' else args[0]
                calls.append(stage)
                if stage == fail:
                    raise RuntimeError('PRIVATE COMMAND /private/operator/secret-token')
                if stage == 'archive_import':
                    return json.dumps(dict(mappings=[dict(root=pin['directory_key'])]))
                if stage == 'checkout':
                    (output / 'received').mkdir()
                    (output / 'received/snapshot.json').write_bytes(raw)
                return ''
            with patch.object(relay.Casita, 'run', run):
                if fail or observer:
                    with self.assertRaises(RuntimeError):
                        relay.receive(response, '/synthetic/native', output,
                                      native_timing=observer or records.append)
                    self.assertFalse(output.exists(), 'failed verification left usable context')
                else:
                    value = relay.receive(response, '/synthetic/native', output, native_timing=records.append)
                    self.assertEqual(value, json.loads(raw))
                    self.assertEqual(json.loads((output / 'verified-pin.json').read_bytes()), pin)
        return calls, records

    def test_records_all_five_native_commands_without_changing_validation_order(self):
        calls, records = self.exercise()
        expected = ['init', 'archive_verify', 'archive_import', 'checkout', 'fsck']
        self.assertEqual(calls, expected)
        self.assertEqual([r['stage'] for r in records], expected)
        for record in records:
            self.assertEqual(set(record), {'stage', 'start_monotonic_ns', 'elapsed_ns',
                                           'children_cpu_ns', 'outcome'})
            self.assertEqual(record['outcome'], 'ok')
            for key in ['start_monotonic_ns', 'elapsed_ns', 'children_cpu_ns']:
                self.assertIs(type(record[key]), int)
                self.assertGreaterEqual(record[key], 0)

    def test_failed_full_fsck_is_recorded_and_context_is_removed(self):
        calls, records = self.exercise(fail='fsck')
        self.assertEqual(calls[-1], 'fsck')
        self.assertEqual(records[-1]['outcome'], 'error')
        encoded = json.dumps(records)
        for forbidden in ['PRIVATE', '/private', 'secret-token', 'argv', 'stderr']:
            self.assertNotIn(forbidden, encoded)

    def test_failed_archive_verification_does_not_import_or_return_context(self):
        calls, records = self.exercise(fail='archive_verify')
        self.assertEqual(calls, ['init', 'archive_verify'])
        self.assertEqual(records[-1]['outcome'], 'error')

    def test_diagnostic_failure_fails_closed_and_does_not_leave_a_verified_pin(self):
        def unavailable(record):
            raise RuntimeError('synthetic diagnostic unavailable')
        calls, _ = self.exercise(observer=unavailable)
        self.assertEqual(calls, ['init'])


if __name__ == '__main__':
    unittest.main()
