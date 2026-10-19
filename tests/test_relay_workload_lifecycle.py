"""Process, capture, provenance and failure-path controls for the paced harness."""
import ast
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import relay_workload_plan as plan
import relay_workload_controller as controller
import relay_workload_backup as recovery


class WorkloadLifecycleTests(unittest.TestCase):
    def test_standard_ssh_port_is_supported_without_relaxing_forwarded_port_rules(self):
        client = dict(host='synthetic-client', port=22, checkout='/tmp/synthetic',
                      python='python3', forward_port=18765)
        try:
            args = plan.ssh_argv(client, 12345)
        except ValueError:
            self.fail('standard SSH port 22 must be supported')
        self.assertIn('22', args)
        with self.assertRaises(ValueError):
            plan.ssh_argv(dict(client, forward_port=22), 12345)

    def test_source_pins_cover_the_entrypoint_and_remote_worker_transitive_imports(self):
        pending, seen = ['relay_workload.py', 'relay_workload_client.py'], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            for node in ast.walk(ast.parse((plan.HERE / name).read_text())):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                    [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for module in modules:
                    candidate = module.split('.')[0] + '.py'
                    if (plan.HERE / candidate).is_file():
                        pending.append(candidate)
        self.assertTrue(seen.issubset(plan.SOURCE_FILES), seen - set(plan.SOURCE_FILES))
        self.assertEqual(len(plan.source_pins()), len(plan.SOURCE_FILES))

    def test_startup_failure_is_retained_without_a_success_receipt(self):
        original = Path.is_file
        def exists(path):
            return True if str(path) in ('/usr/bin/age', '/usr/bin/age-keygen') else original(path)
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'is_file', exists), \
                patch.object(controller.platform, 'system', return_value='Linux'), \
                patch.object(controller.relay, 'initialize', side_effect=RuntimeError('synthetic startup')):
            output = Path(directory) / 'failed'
            with self.assertRaises(RuntimeError):
                controller.run(sys.executable, output, plan.make_plan(30, 20, 10))
            self.assertFalse((output / 'receipt.json').exists())
            self.assertTrue((output / 'failure.json').is_file())

    def test_causal_probe_proves_overlap_without_assuming_clock_drift(self):
        count = getattr(controller, 'causal_overlap', lambda probes, start, end: 0)
        probes = [dict(sent=10.1, received=10.3, active=['alpha-publish-1']),
                  dict(sent=9.9, received=10.2, active=['beta-publish-1']),
                  dict(sent=10.8, received=11.1, active=['alpha-consult-1']),
                  dict(sent=10.2, received=10.4, active=[])]
        self.assertEqual(count(probes, 10, 11), 1)

    def test_synthetic_service_adapter_cannot_stop_a_different_service(self):
        with patch.object(recovery.relay, 'serve', return_value=Mock()):
            server = recovery.OwnedServer(Mock())
            with self.assertRaises(ValueError):
                server.command('/usr/bin/systemctl', 'stop', 'production.service')

    def test_cleanup_attempts_every_resource_after_one_transport_close_fails(self):
        cleanup = getattr(controller, 'close_resources', lambda peers, server, logs: None)
        first, second, server, log = Mock(), Mock(), Mock(), Mock()
        first.close.side_effect = OSError('synthetic transport close')
        with self.assertRaises(RuntimeError):
            cleanup({'alpha': first, 'beta': second}, server, [log])
        second.close.assert_called_once()
        server.close.assert_called_once()
        log.close.assert_called_once()

    def test_local_cipher_sink_cannot_escape_its_fresh_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'synthetic.age'; source.write_bytes(b'SYNTHETIC ENCRYPTED FIXTURE')
            sink = recovery.LocalEncryptedSink(root / 'sink')
            with self.assertRaises(ValueError):
                sink.upload(source, '../escaped.age')
            self.assertFalse((root / 'escaped.age').exists())

    def extract(self, member):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            member.uid, member.gid = os.getuid(), os.getgid()
            archive.addfile(member, io.BytesIO(b'fixture') if member.isfile() else None)
        process = Mock(stdout=io.BytesIO(raw.getvalue()))
        process.wait.return_value = process.poll.return_value = 0
        with tempfile.TemporaryDirectory() as directory, patch.object(recovery.subprocess, 'Popen', return_value=process):
            output = Path(directory) / 'restored'
            count = recovery.extract_encrypted(Path('synthetic.age'), Path('identity'), output)
            return count, (output / 'fixture.txt').read_bytes()

    def test_decrypted_archive_requires_safe_regular_members(self):
        member = tarfile.TarInfo('fixture.txt'); member.size = 7
        count, value = self.extract(member)
        self.assertGreater(count, 0)
        self.assertEqual(value, b'fixture')
        for name, kind in [('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                           ('fixture.txt', tarfile.SYMTYPE), ('fixture.txt', tarfile.LNKTYPE)]:
            member = tarfile.TarInfo(name); member.type = kind
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                self.extract(member)

    def start_worker(self, root):
        log = (root / 'worker.log').open('wb')
        peer = controller.Peer([sys.executable, '-B', str(plan.HERE / 'relay_workload_client.py')], log)
        peer.send(dict(operation='hello', binary=str(Path(sys.executable).resolve())))
        hello = peer.receive('hello')
        plan.validate_hello(hello, plan.source_pins(), hello['binary_sha256'])
        config = dict(binary=str(Path(sys.executable).resolve()), output=str(root / 'worker-output'),
                      credential=dict(workspace='synthetic-two-operator-test', token='synthetic-ephemeral-token-only'),
                      jobs=[], duration=0, cursor=8)
        peer.send(dict(operation='configure', config=config)); peer.receive('ready')
        return peer, log

    def test_worker_success_removes_its_ephemeral_credential_before_acknowledgment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); peer, log = self.start_worker(root)
            try:
                peer.send(dict(operation='start', epoch=time.monotonic() + .1))
                done = peer.receive('done', timeout=3)
                self.assertTrue(done['credential_removed'])
                self.assertFalse((root / 'worker-output/ephemeral-credential.json').exists())
                peer.process.stdin.close()
                self.assertEqual(peer.process.wait(timeout=3), 0)
            finally:
                peer.close(); log.close()

    def test_controller_disconnect_cancels_the_remote_worker_and_removes_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); peer, log = self.start_worker(root)
            try:
                peer.send(dict(operation='start', epoch=time.monotonic() + 30))
                deadline = time.monotonic() + 3
                credential = root / 'worker-output/ephemeral-credential.json'
                while not credential.exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertTrue(credential.exists())
                peer.process.stdin.close()
                self.assertNotEqual(peer.process.wait(timeout=3), 0)
                self.assertFalse(credential.exists())
            finally:
                peer.close(); log.close()


if __name__ == '__main__':
    unittest.main()
