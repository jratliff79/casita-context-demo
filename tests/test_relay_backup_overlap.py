"""Synthetic overlap proof regressions; no native stores or private fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import relay_workload_backup as backup
import relay_workload_client as client
import relay_workload_controller as controller
from relay_workload_diagnostics import Diagnostics
from relay_workload_plan import make_plan


class BackupOverlapTests(unittest.TestCase):
    def api(self, module, name):
        value = getattr(module, name, None)
        self.assertIsNotNone(value, name + ' must provide bounded overlap evidence')
        return value

    def test_armed_probe_waits_for_real_work_without_holding_or_inventing_it(self):
        sent = []
        probes = self.api(client, 'ActiveProbes')(sent.append)
        probes.request(1, wait_active=True)
        self.assertEqual(sent, [])  # Idle or future arrivals are not active.
        probes.begin('synthetic-publication')
        self.assertEqual(sent, [dict(kind='probe', probe_id=1, active=['synthetic-publication'])])
        probes.end('synthetic-publication')
        probes.request(2)
        self.assertEqual(sent[-1]['active'], [])
        probes.begin('synthetic-consultation')
        probes.request(3, wait_active=True)
        self.assertEqual(sent[-1]['active'], ['synthetic-consultation'])
        self.assertEqual(controller.causal_overlap([
            dict(sent=10.01, received=10.02, active=sent[0]['active']),
            dict(sent=9.99, received=10.02, active=['outside']),
            dict(sent=10.01, received=11.01, active=['late']),
            dict(sent=10.01, received=10.02, active=[]),
        ], 10, 11), 1)

    def test_pending_probe_is_bounded_and_ids_and_flags_are_validated(self):
        probes = self.api(client, 'ActiveProbes')(lambda message: None)
        for identifier, flag in [(True, True), (-1, True), (1, 'yes')]:
            with self.assertRaises(ValueError):
                probes.request(identifier, wait_active=flag)
        probes.request(1, wait_active=True)
        with self.assertRaises(ValueError):
            probes.request(2, wait_active=True)

    def test_preparation_and_wait_do_not_extend_actual_capture_interval(self):
        begin = self.api(backup, 'wait_for_capture')
        clock, starts, events, waits = [12.0], [], [], []
        def sleep(delay):
            waits.append(delay)
            clock[0] += delay
        with patch.object(backup.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(backup.time, 'sleep', side_effect=sleep):
            actual = begin(15.0, starts.append, lambda stage, at: events.append((stage, at)))
        self.assertEqual((actual, starts), (15.0, [15.0]))
        self.assertEqual(events, [('ready', 12.0), ('capture', 15.0)])
        self.assertAlmostEqual(sum(waits), 3.0)
        # Preparation that finishes late cannot backdate the proof interval.
        with patch.object(backup.time, 'monotonic', return_value=18.0):
            self.assertEqual(begin(15.0, None, None), 18.0)

    def test_backup_preparation_precedes_midpoint_without_changing_arrivals(self):
        schedule = self.api(controller, 'backup_schedule')
        for plan in [make_plan(), make_plan(initial_revisions=840), make_plan(30, 20, 10)]:
            before = json.dumps(plan, sort_keys=True)
            timing = schedule(plan, 100)
            midpoint = 100 + plan['normal_seconds'] / 2
            self.assertLess(timing['prepare_at'], timing['start_at'])
            self.assertEqual(timing['start_at'], midpoint - .5)
            self.assertEqual(before, json.dumps(plan, sort_keys=True))
            self.assertTrue(any(job['at'] == midpoint - 100 and job['operation'] in ('publish', 'consult')
                                for jobs in plan['clients'].values() for job in jobs))

    def test_failure_evidence_is_private_bounded_and_keeps_actual_backup_result(self):
        evidence_type = self.api(backup, 'BackupEvidence')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            evidence = evidence_type(diagnostic)
            evidence.event('prepare', 8)
            evidence.event('ready', 9)
            evidence.event('capture', 10)
            evidence.sent(1, 1, 10.01)
            evidence.received(1, 1, 10.02, [3])
            evidence.sent(2, 2, 10.03)  # Unanswered probes survive failure.
            evidence.result(dict(start=10, end=11, revision=66, event_cursor=68,
                ciphertext_sha256='a' * 64, ciphertext_bytes=1234, compressed_payload_bytes=1000,
                full_inventory_verified=True, native_history_verified=True,
                authenticated_members_restored=True, real_age_encryption=True,
                local_sink_only=True, systemd_exercised=False,
                pin={'PRIVATE': 'SYNTHETIC SECRET'}, token='SYNTHETIC SECRET'))
            evidence.failed(ValueError('SYNTHETIC SECRET /private/filename'))
            path = root / 'backup-overlap.json'
            value = json.loads(path.read_text())
            self.assertEqual(value['state'], 'failed')
            self.assertEqual(value['backup']['revision'], 66)
            self.assertEqual(value['probes'][0]['active_jobs'], [3])
            self.assertIsNone(value['probes'][1]['received'])
            self.assertNotIn('SECRET', path.read_text())
            self.assertNotIn('/private', path.read_text())
            self.assertNotIn('pin', value['backup'])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertLess(path.stat().st_size, 65536)
            self.assertFalse((root / 'receipt.json').exists())
            diagnostic.close()

    def test_probe_evidence_cap_and_io_failure_cannot_be_called_complete(self):
        evidence_type = self.api(backup, 'BackupEvidence')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            evidence = evidence_type(diagnostic)
            for identifier in range(1, 129):
                evidence.sent(1, identifier, float(identifier))
                evidence.received(1, identifier, float(identifier), [])
            with self.assertRaises(ValueError):
                evidence.sent(1, 129, 129.0)
            with patch.object(diagnostic, '_atomic', side_effect=OSError('synthetic failure')):
                with self.assertRaises(OSError):
                    evidence.event('end', 130)
            self.assertFalse((root / 'receipt.json').exists())
            diagnostic.close()


if __name__ == '__main__':
    unittest.main()
