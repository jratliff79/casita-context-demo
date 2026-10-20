"""Failure gates for synthetic backup diagnostics, independent of timing traces."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import relay_workload_backup as backup
from relay_workload_diagnostics import Diagnostics


class BackupFailureEvidenceTests(unittest.TestCase):
    def result(self):
        return dict(start=10, end=11, revision=66, event_cursor=68,
            compressed_payload_bytes=1000, ciphertext_bytes=1234, ciphertext_sha256='a' * 64,
            full_inventory_verified=True, native_history_verified=True,
            authenticated_members_restored=True, real_age_encryption=True,
            local_sink_only=True, systemd_exercised=False)

    def test_failed_gate_retains_result_probe_and_numeric_rejection(self):
        for overlap, epoch, duration in [(0, 9, 3), (1, 10.5, 3), (1, 9, 1)]:
            with self.subTest(overlap=overlap, epoch=epoch, duration=duration), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory); root.chmod(0o700)
                diagnostic = Diagnostics(root, enabled=False)
                try:
                    evidence = backup.BackupEvidence(diagnostic)
                    evidence.result(self.result())
                    evidence.sent(1, 1, 10.1)
                    evidence.received(1, 1, 10.2, [])
                    with self.assertRaises(ValueError):
                        evidence.evaluate(epoch, duration, overlap)
                    value = json.loads((root / 'backup-overlap.json').read_text())
                    self.assertFalse(value['evaluation']['passed'])
                    self.assertEqual(value['backup'], self.result())
                    self.assertEqual(value['state'], 'failed')
                finally:
                    diagnostic.close()

    def test_capture_failure_preserves_stage_and_removes_age_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            evidence = backup.BackupEvidence(diagnostic)
            destination = root / 'backup'
            def fail(*args):
                destination.mkdir(mode=0o700)
                (destination / 'ephemeral-age-identity').write_text('SYNTHETIC SECRET')
                evidence.event('capture', 10)
                raise RuntimeError('SYNTHETIC SECRET')
            try:
                with patch.object(backup, '_capture_restore', side_effect=fail):
                    with self.assertRaises(RuntimeError) as failure:
                        backup.capture_restore(destination, None, None, None, '0' * 40, '0' * 40,
                                               observe=evidence.event)
                evidence.failed(failure.exception)
                self.assertFalse((destination / 'ephemeral-age-identity').exists())
                raw = (root / 'backup-overlap.json').read_text()
                value = json.loads(raw)
                self.assertIn('end', value['stages'])
                self.assertEqual(value['error_type'], 'RuntimeError')
                self.assertNotIn('SECRET', raw)
                self.assertIsNone(value['backup'])
            finally:
                diagnostic.close()

    def test_evidence_io_failure_prevents_later_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            evidence = backup.BackupEvidence(diagnostic)
            try:
                evidence.result(self.result())
                with patch.object(diagnostic, '_atomic', side_effect=OSError('synthetic IO')):
                    with self.assertRaises(OSError):
                        evidence.event('end', 11)
                with self.assertRaises(ValueError):
                    evidence.evaluate(9, 3, 1)
                self.assertFalse(json.loads((root / 'backup-overlap.json').read_text())['evaluation']['passed'])
            finally:
                diagnostic.close()


if __name__ == '__main__':
    unittest.main()
