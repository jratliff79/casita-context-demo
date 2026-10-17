"""Receipt checks independent of a worker's reported summaries."""
import copy
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import relay_workload_controller as controller
from relay_workload_plan import make_plan
import relay_workload_plan as protocol


class WorkloadObservationTests(unittest.TestCase):
    def test_worker_rejects_executable_drift_during_the_timed_workload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = root / 'synthetic-executable'
            shutil.copyfile(sys.executable, binary)
            binary.chmod(0o700)
            with (root / 'worker.log').open('wb') as log:
                peer = controller.Peer([sys.executable, '-B', str(protocol.HERE / 'relay_workload_client.py')], log)
                try:
                    peer.send(dict(operation='hello', binary=str(binary)))
                    peer.receive('hello')
                    peer.send(dict(operation='configure', config=dict(binary=str(binary),
                        output=str(root / 'client'), credential=dict(workspace='synthetic-jpcj-drift',
                        token='synthetic-only'), jobs=[], duration=.5, cursor=0)))
                    peer.receive('ready')
                    peer.send(dict(operation='start', epoch=time.monotonic() + .1))
                    credential = root / 'client/ephemeral-credential.json'
                    deadline = time.monotonic() + 3
                    while not credential.exists() and time.monotonic() < deadline:
                        time.sleep(.005)
                    self.assertTrue(credential.exists())
                    with binary.open('ab') as stream:
                        stream.write(b'synthetic drift')
                    message = peer.messages.get(timeout=3)
                    self.assertEqual(message['kind'], 'failure')
                    self.assertFalse(credential.exists())
                    self.assertNotEqual(peer.process.wait(timeout=3), 0)
                finally:
                    peer.close()

    def test_reconnect_drains_all_missed_pages_and_requires_the_exact_target_cursor(self):
        catch_up = getattr(protocol, 'catch_up_events', lambda request, cursor, count: {})
        request = Mock(side_effect=[dict(events=[dict(cursor=8), dict(cursor=9)], next_cursor=9),
                                    dict(events=[dict(cursor=10)], next_cursor=10),
                                    dict(events=[], next_cursor=10)])
        result = catch_up(request, 7, 3)
        self.assertIn('cursor', result)
        self.assertEqual(result['cursor'], 10)
        self.assertEqual([event['cursor'] for event in result['events']], [8, 9, 10])
        with self.assertRaises(ValueError):
            catch_up(Mock(return_value=dict(events=[], next_cursor=7)), 7, 3)

    def test_shortened_schedule_polls_after_its_last_publication(self):
        plan = make_plan(30, 20, 10, 12)
        for jobs in plan['clients'].values():
            last_publish = max(job['at'] for job in jobs if job['operation'] == 'publish')
            last_poll = max(job['at'] for job in jobs if job['operation'] == 'updates')
            self.assertGreater(last_poll, last_publish)

    def test_packaged_source_without_git_has_no_invented_revision(self):
        revision = getattr(controller, 'source_revision', lambda: None)
        with patch.object(controller.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(128, 'git')):
            self.assertEqual(revision(), '0' * 40)

    def test_latency_must_include_queueing_from_the_scheduled_arrival(self):
        check = getattr(controller, 'validate_timing', lambda row: None)
        row = dict(job=dict(operation='publish'), start=11, end=12, scheduled=10,
                   milliseconds=1000, dispatch_lag_ms=1000)
        with self.assertRaises(ValueError):
            check(row)

    def test_controller_rejects_nonfinite_timings_and_hard_deadline_breaches(self):
        check = getattr(controller, 'validate_timing', lambda row: None)
        base = dict(job=dict(operation='publish'), start=10, end=11, scheduled=10,
                    milliseconds=1000, dispatch_lag_ms=0)
        for mutation in [dict(end=math.nan), dict(milliseconds=math.inf), dict(start=9),
                         dict(end=26, milliseconds=16000)]:
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check(dict(base, **mutation))

    def test_history_slowdown_fails_even_when_the_overall_publish_budget_passes(self):
        check = getattr(controller, 'history_windows', lambda plan, rows: {})
        plan = make_plan(initial_revisions=840)
        rows = {}
        for name in ('alpha', 'beta'):
            rows[name] = [dict(job=job, milliseconds=1500 if job['at'] >= 2700 else 500)
                          for job in plan['clients'][name]
                          if job['phase'] == 'normal' and job['operation'] == 'publish']
        result = check(plan, rows)
        self.assertIn('alpha', result)
        self.assertEqual(result['alpha']['first']['count'], 15)
        self.assertEqual(result['beta']['last']['count'], 15)
        self.assertFalse(result['alpha']['ratio_passed'])
        self.assertLess(result['alpha']['last']['p95_ms'], 2000)


if __name__ == '__main__':
    unittest.main()
