"""Drain after both publishers finish; hostname evidence cannot qualify devices."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

import context_relay as relay
import relay_workload_plan as plan
import relay_workload_controller as controller
from relay_workload_backup import OwnedServer


class WorkloadFinalizationTests(unittest.TestCase):
    def test_final_drain_recovers_a_commit_after_the_last_scheduled_poll(self):
        drain = getattr(plan, 'drain_updates', lambda request, cursor, target: {})
        request = Mock(side_effect=[dict(events=[dict(cursor=10)], next_cursor=10),
                                    dict(events=[], next_cursor=10)])
        result = drain(request, 9, 10)
        self.assertEqual(result.get('cursor'), 10)
        self.assertEqual(result['events'], [dict(cursor=10)])
        self.assertEqual([call.args[1]['after'] for call in request.call_args_list], [9, 10])

    def test_final_drain_checks_quiescence_even_if_the_last_poll_was_current(self):
        drain = getattr(plan, 'drain_updates', lambda request, cursor, target: {})
        request = Mock(return_value=dict(events=[], next_cursor=10))
        self.assertEqual(drain(request, 10, 10).get('cursor'), 10)
        request.assert_called_once_with('updates', dict(after=10))
        for page in [dict(events=[], next_cursor=9),
                     dict(events=[dict(cursor=11)], next_cursor=11)]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                drain(Mock(return_value=page), 10, 10)

    def test_same_third_host_and_distinct_hostnames_never_qualify_devices(self):
        observe = getattr(controller, 'device_observations', lambda environments: dict(remote_devices_tested=True))
        for hosts in [('third-host', 'third-host'), ('third-host-a', 'third-host-b')]:
            with self.subTest(hosts=hosts):
                result = observe({name: dict(hostname=host) for name, host in zip(('alpha', 'beta'), hosts)})
                self.assertFalse(result['remote_devices_tested'])
                self.assertEqual(result['hostname_observations']['distinct_client_names'], len(set(hosts)) == 2)

    def test_worker_keeps_credentials_until_controller_releases_the_final_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            binary = Path(sys.executable).resolve()
            state = root / 'relay'
            relay.initialize(state, 'synthetic-jpcj-final-drain', 'alpha', ['beta'])
            (state / 'store').mkdir()  # Updates-only fixture; no native checkpoint is created or consulted.
            server = OwnedServer(relay.Relay(state, binary))
            server.start()
            with (root / 'worker.log').open('wb') as log:
                peer = controller.Peer([sys.executable, '-B', str(plan.HERE / 'relay_workload_client.py')], log)
                try:
                    peer.send(dict(operation='hello', binary=str(binary)))
                    peer.receive('hello')
                    credential = json.loads((state / 'credentials/beta.json').read_text())
                    peer.send(dict(operation='configure', config=dict(binary=str(binary),
                        output=str(root / 'client'), credential=credential, jobs=[], duration=0,
                        cursor=0, final_cursor=0, url='http://127.0.0.1:' + str(server.port),
                        relay_port=server.port)))
                    peer.receive('ready')
                    peer.send(dict(operation='start', epoch=time.monotonic() + .1))
                    self.assertEqual(peer.messages.get(timeout=3)['kind'], 'drained')
                    self.assertTrue((root / 'client/ephemeral-credential.json').is_file())
                    with self.assertRaises(queue.Empty):
                        peer.messages.get(timeout=.05)
                    peer.send(dict(operation='finish', cursor=0))
                    done = peer.receive('done', timeout=3)
                    self.assertEqual(done['final_updates']['cursor'], 0)
                    self.assertEqual(done['final_updates']['events'], [])
                    self.assertTrue(done['credential_removed'])
                    self.assertFalse((root / 'client/ephemeral-credential.json').exists())
                    peer.process.stdin.close()
                    self.assertEqual(peer.process.wait(timeout=3), 0)
                finally:
                    peer.close()
                    server.close()


if __name__ == '__main__':
    unittest.main()
