"""Synthetic observations, real loopback HTTP and read-only SQLite qualification."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from relay_readiness import request_proof, response_proof
import context_relay
import relay_monitor as monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        context_relay.initialize(self.state, 'synthetic-team', 'owner', ['member'])
        context_relay.provision_readiness(self.state)
        self.credential = self.state / 'monitoring/readiness.json'
        self.spool = self.root / 'spool'
        self.spool.mkdir()
        os.chmod(self.credential, 0o600)
        stats = SimpleNamespace(f_blocks=100000, f_bavail=50000, f_frsize=4096, f_files=100000, f_favail=50000)
        sampler = patch.object(monitor.os, 'statvfs', return_value=stats)
        sampler.start()
        self.addCleanup(sampler.stop)
        self.config = dict(state=str(self.state), spool=str(self.spool), credential=str(self.credential), port=8765,
                           backup_max_bytes=1024, free_percent=15, inodes_free_percent=10,
                           region='us-west-2', namespace='Synthetic/Relay', relay_name='synthetic')

    def server(self, status=200, response=None, headers=None):
        observations = []
        identity = json.loads(self.credential.read_bytes())
        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                body = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                observations.append({'body':body, 'authorization':handler.headers['Authorization']})
                handler.send_response(status)
                for name, value in (headers or {}).items(): handler.send_header(name, value)
                handler.end_headers()
                handler.wfile.write(response if response is not None else json.dumps(
                    {'ready': True, 'nonce': body['nonce'],
                     'proof': response_proof(identity['token'], identity['workspace'], body['nonce'])}).encode())
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        self.config['port'] = server.server_port
        return observations

    def test_readiness_requests_no_event_payload_and_rejects_invalid_response(self):
        seen = self.server()
        monitor.readiness(self.config)
        identity = json.loads(self.credential.read_bytes())
        self.assertNotIn('workspace', seen[0]['body'])
        self.assertEqual(len(seen[0]['body']['nonce']), 64)
        self.assertNotIn(identity['token'], seen[0]['authorization'])
        self.assertEqual(seen[0]['authorization'], 'RelayReadiness ' + request_proof(identity['token'], seen[0]['body']))
        for raw in (b'{}', b'x'*257, b'{"events":["private"],"next_cursor":1}',
                    b'{"ready":1}', b'{"ready":true,"private":1}'):
            with self.subTest(raw=raw):
                self.server(response=raw)
                with self.assertRaises(Exception): monitor.readiness(self.config)

    def test_closed_listener_and_unreadable_credential_fail_without_hiding_disk(self):
        import socket
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));self.config['port']=listener.getsockname()[1]
        result=monitor.sample(self.config)
        self.assertEqual(result['Readiness'],0)
        self.assertEqual(result['MonitorComplete'],1)
        self.credential.chmod(0o644)
        self.assertEqual(monitor.sample(self.config)['Readiness'],0)

    def test_collector_failures_are_partial_samples_and_cloud_rejects_nonfinite_values(self):
        self.server()
        with patch.object(monitor,'state_size',side_effect=RuntimeError('private path')):
            result=monitor.sample(self.config)
        self.assertEqual(result['MonitorComplete'],0)
        self.assertNotIn('StateAllocatedBytes',result)
        self.assertIn('StoragePressure',result)
        for value in (float('nan'),float('inf'),-1):
            result['StateAllocatedBytes']=value
            with self.assertRaises(monitor.MonitorError):monitor.publish(self.config,result,Mock())

    def test_http_error_and_redirect_are_rejected(self):
        self.server(status=401, response=b'private-error-body')
        with self.assertRaises(Exception): monitor.readiness(self.config)
        self.server(status=302, headers={'Location':'http://example.invalid/leak'})
        with self.assertRaises(Exception): monitor.readiness(self.config)

    def test_unavailable_does_not_hide_storage_and_no_state_changes(self):
        self.server(status=503)
        before = {p.relative_to(self.state): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.state.rglob('*') if p.is_file()}
        result = monitor.sample(self.config)
        self.assertEqual(result['Readiness'], 0)
        self.assertEqual(result['MonitorComplete'], 1)
        self.assertIn('AvailableDiskBytes', result)
        self.assertEqual(before, {p.relative_to(self.state): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.state.rglob('*') if p.is_file()})

    def test_authenticated_real_relay_readiness(self):
        class Index(context_relay.Relay):
            def __init__(service, state):
                service.state=state; service.workspace='synthetic-team'; service.owner='owner'
        server = context_relay.serve(Index(self.state), 0)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        self.addCleanup(lambda:(server.shutdown(),thread.join(),server.server_close()))
        self.config['port']=server.server_port
        self.assertEqual(monitor.sample(self.config)['Readiness'],1)
        credential=json.loads(self.credential.read_bytes());credential['token']='x'*40
        invalid = self.root / 'invalid-readiness.json'
        invalid.write_text(json.dumps(credential))
        invalid.chmod(0o600)
        self.config['credential'] = str(invalid)
        self.assertEqual(monitor.sample(self.config)['Readiness'],0)

    def test_disk_and_inode_pressure_and_reserved_backup_space(self):
        base = dict(f_blocks=1000, f_bavail=500, f_frsize=1024**2, f_files=1000, f_favail=500)
        for changes, expected in (({},0),({'f_bavail':150},1),({'f_favail':100},1),
                                  ({'f_bavail':257,'f_frsize':1024**2},0)):
            with self.subTest(changes=changes), patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**(base|changes))):
                self.assertEqual(monitor.storage_pressure(self.config, {**monitor.filesystem(self.config), **monitor.filesystem(self.config, 'spool')}),expected)
        self.config['backup_max_bytes']=200*1024**2
        with patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**base)):
            self.assertEqual(monitor.storage_pressure(self.config, {**monitor.filesystem(self.config), **monitor.filesystem(self.config, 'spool')}),1)
        with patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**(base|{'f_files':0}))):
            with self.assertRaises(monitor.MonitorError): monitor.filesystem(self.config)

    def test_backup_reserve_uses_separate_spool_filesystem(self):
        self.config['spool'] = str(self.root / 'spool')
        state = SimpleNamespace(f_blocks=1000, f_bavail=500, f_frsize=1024**2, f_files=1000, f_favail=500)
        spool = SimpleNamespace(f_blocks=10000, f_bavail=1, f_frsize=1024**2, f_files=1000, f_favail=500)
        def observation(path):
            return state if str(path) == self.config['state'] else spool
        with patch.object(monitor.os, 'statvfs', side_effect=observation), patch.object(monitor, 'readiness'):
            result = monitor.sample(self.config)
        self.assertEqual(result['StoragePressure'], 1)
        self.assertEqual(result['SpoolAvailableDiskBytes'], 1024**2)

    def test_state_mount_does_not_inherit_spool_reserve_and_failure_is_visible(self):
        state = SimpleNamespace(f_blocks=100, f_bavail=50, f_frsize=1024**2, f_files=1000, f_favail=500)
        spool = SimpleNamespace(f_blocks=10000, f_bavail=5000, f_frsize=1024**2, f_files=1000, f_favail=500)
        def observation(path):
            return state if str(path) == self.config['state'] else spool
        with patch.object(monitor.os, 'statvfs', side_effect=observation), patch.object(monitor, 'readiness'):
            self.assertEqual(monitor.sample(self.config)['StoragePressure'], 0)
        def failed_spool(path):
            if str(path) == self.config['spool']:
                raise OSError('private path')
            return state
        with patch.object(monitor.os, 'statvfs', side_effect=failed_spool), patch.object(monitor, 'readiness'):
            result = monitor.sample(self.config)
        self.assertEqual(result['MonitorComplete'], 0)
        self.assertIn('AvailableDiskBytes', result)
        self.assertNotIn('StoragePressure', result)

    def test_readiness_scope_cannot_read_or_write_member_api_and_revocation_reloads(self):
        class Index(context_relay.Relay):
            def __init__(service, state):
                service.state = state
                service.workspace, service.owner = 'synthetic-team', 'owner'
        service = Index(self.state)
        credential = json.loads(self.credential.read_bytes())
        member = json.loads((self.state / 'credentials/owner.json').read_bytes())
        before = hashlib.sha256((self.state / 'relay.sqlite').read_bytes()).hexdigest()
        body = {'nonce':secrets.token_hex(32)}
        result = service.handle(request_proof(credential['token'], body), 'readiness', body)
        self.assertEqual(result, {'ready':True, 'nonce':body['nonce'],
                         'proof':response_proof(credential['token'], credential['workspace'], body['nonce'])})
        for operation in ('context', 'updates', 'publish', 'propose', 'accept'):
            with self.subTest(operation=operation), self.assertRaises(context_relay.Rejected) as rejected:
                service.handle(credential['token'], operation, {'workspace':'synthetic-team'})
            self.assertEqual(rejected.exception.status, 401)
        with self.assertRaises(context_relay.Rejected):
            service.handle(member['token'], 'readiness', {'workspace':'synthetic-team'})
        with self.assertRaises(context_relay.Rejected):
            service.handle(credential['token'], 'readiness', {'workspace':'other'})
        self.assertEqual(before, hashlib.sha256((self.state / 'relay.sqlite').read_bytes()).hexdigest())
        context_relay.revoke_readiness(self.state)
        with self.assertRaises(context_relay.Rejected):
            service.handle(credential['token'], 'readiness', {'workspace':'synthetic-team'})
        self.assertEqual(service.handle(member['token'], 'updates', {'workspace':'synthetic-team','after':0})['events'], [])

    def test_rogue_static_listener_and_replayed_response_cannot_report_readiness(self):
        self.server(response=b'{"ready":true}')
        self.assertEqual(monitor.sample(self.config)['Readiness'], 0)
        identity = json.loads(self.credential.read_bytes())
        old_nonce = '0' * 64
        replay = json.dumps({'ready':True, 'nonce':old_nonce,
            'proof':response_proof(identity['token'], identity['workspace'], old_nonce)}).encode()
        self.server(response=replay)
        with patch.object(monitor.secrets, 'token_hex', return_value='1'*64):
            self.assertEqual(monitor.sample(self.config)['Readiness'], 0)
        reflected = {'nonce':'1'*64}
        self.server(response=json.dumps({'ready':True,'nonce':'1'*64,
            'proof':request_proof(identity['token'], reflected)}).encode())
        with patch.object(monitor.secrets, 'token_hex', return_value='1'*64):
            self.assertEqual(monitor.sample(self.config)['Readiness'], 0)

    def test_bogus_proofs_do_not_disclose_workspace_and_scope_secret_is_not_bearer(self):
        class Index(context_relay.Relay):
            def __init__(service, state):
                service.state = state
                service.workspace, service.owner = 'synthetic-team', 'owner'
        service = Index(self.state)
        identity = json.loads(self.credential.read_bytes())
        for workspace in ('synthetic-team','other'):
            body = {'workspace':workspace, 'nonce':secrets.token_hex(32)}
            for invalid in ('0'*64, identity['token']):
                with self.assertRaises(context_relay.Rejected) as rejected:
                    service.handle(invalid, 'readiness', body)
                self.assertEqual(rejected.exception.status, 401)
                self.assertEqual(str(rejected.exception), 'unauthorized')
        body = {'workspace':'other', 'nonce':secrets.token_hex(32)}
        with self.assertRaises(context_relay.Rejected) as rejected:
            service.handle(request_proof(identity['token'],body),'readiness',body)
        self.assertEqual(rejected.exception.status, 403)

    def test_missing_readiness_database_is_not_created(self):
        class Index(context_relay.Relay):
            def __init__(service, state):
                service.state = state
                service.workspace, service.owner = 'synthetic-team', 'owner'
        identity = json.loads(self.credential.read_bytes())
        body = {'nonce':secrets.token_hex(32)}
        database = self.state / 'relay.sqlite'
        database.unlink()
        with self.assertRaises(Exception):
            Index(self.state).handle(request_proof(identity['token'],body),'readiness',body)
        self.assertFalse(database.exists())
        self.assertFalse((self.state / 'relay.sqlite-journal').exists())
        before = self.credential.read_bytes()
        with self.assertRaises(Exception):context_relay.provision_readiness(self.state)
        self.assertFalse(database.exists())
        self.assertEqual(self.credential.read_bytes(),before)

    def test_failed_provisioning_cleans_new_files_and_retry_succeeds(self):
        context_relay.revoke_readiness(self.state)
        real_fsync = context_relay.os.fsync
        for failure_call in (1,2,3):
            calls = 0
            def failed(fd):
                nonlocal calls
                calls += 1
                if calls == failure_call:
                    raise OSError('synthetic durability failure')
                return real_fsync(fd)
            with self.subTest(failure_call=failure_call):
                with patch.object(context_relay.os,'fsync',side_effect=failed), self.assertRaises(OSError):
                    context_relay.provision_readiness(self.state)
                self.assertFalse(self.credential.exists())
                self.assertEqual(list(self.credential.parent.iterdir()), [])
                context_relay.provision_readiness(self.state)
                self.assertEqual(context_relay.readiness_credential(self.state)['scope'],'relay-readiness.v1')
                context_relay.revoke_readiness(self.state)

    def test_native_monitor_inventory_failure_stops_thread_and_closes_socket(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('synthetic_backup_qualification',
            Path(__file__).resolve().parents[1] / 'ci/verify-relay-backup.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        service = SimpleNamespace(state=self.state)
        server = context_relay.serve(service,0)
        with patch.object(script.relay,'serve',return_value=server), \
             patch.object(script.backup,'inventory',side_effect=script.backup.BackupError('synthetic limit')):
            with self.assertRaises(script.backup.BackupError):script.observe_native_monitor(service,self.spool)
        self.assertEqual(server.socket.fileno(),-1)
        self.assertFalse(any(t.name=='native-monitor-qualification' and t.is_alive() for t in threading.enumerate()))

    def test_readiness_provision_never_overwrites_or_borrows_a_member_token(self):
        before = self.credential.read_bytes()
        with self.assertRaises(FileExistsError): context_relay.provision_readiness(self.state)
        self.assertEqual(before, self.credential.read_bytes())
        token = json.loads((self.state / 'credentials/owner.json').read_bytes())['token']
        context_relay.revoke_readiness(self.state)
        with patch.object(context_relay.secrets, 'token_urlsafe', return_value=token):
            with self.assertRaises(context_relay.Rejected): context_relay.provision_readiness(self.state)
        self.assertFalse(self.credential.exists())

    def test_monitor_rejects_ordinary_member_credential_even_on_successful_http(self):
        self.server()
        self.config['credential'] = str(self.state / 'credentials/owner.json')
        os.chmod(self.config['credential'], 0o600)
        self.assertEqual(monitor.sample(self.config)['Readiness'], 0)

    def test_inventory_is_bounded_and_never_follows_links(self):
        (self.state/'bad-link').symlink_to(self.credential)
        with self.assertRaises(monitor.MonitorError): monitor.state_size(self.config)
        (self.state/'bad-link').unlink()
        with patch.object(monitor,'MAX_ENTRIES',1):
            with self.assertRaises(monitor.MonitorError): monitor.state_size(self.config)
        clock=iter([0,11]).__next__
        with self.assertRaises(monitor.MonitorError): monitor.state_size(self.config,clock)

    def test_database_missing_is_not_created_and_incomplete_metrics_fail_closed(self):
        self.server()
        (self.state/'relay.sqlite').unlink()
        result=monitor.sample(self.config)
        self.assertEqual(result['MonitorComplete'],0)
        self.assertNotIn('RetainedEvents',result)
        self.assertFalse((self.state/'relay.sqlite').exists())

    def test_counts_aggregate_and_no_source_or_credentials_in_metrics(self):
        self.server()
        result=monitor.sample(self.config)
        client=Mock();monitor.publish(self.config,result,client)
        payload=client.put_metric_data.call_args.kwargs
        self.assertEqual(payload['Namespace'],'Synthetic/Relay')
        self.assertEqual({m['MetricName'] for m in payload['MetricData']},set(monitor.METRICS))
        serialized=json.dumps(payload)
        credential=json.loads(self.credential.read_bytes())
        for private in (self.config['state'],credential['token'],credential['workspace'],'owner.json'):
            self.assertNotIn(private,serialized)
        for item in payload['MetricData']:
            self.assertEqual(item['Dimensions'],[{'Name':'Relay','Value':'synthetic'}])

    def test_private_configuration_rejects_links_permissions_and_oversize(self):
        bad=self.root/'config';bad.write_text('{}');bad.chmod(0o644)
        with self.assertRaises(monitor.MonitorError): monitor.private_json(bad,os.getuid())
        bad.chmod(0o600);bad.write_bytes(b'x'*16385)
        with self.assertRaises(monitor.MonitorError): monitor.private_json(bad,os.getuid())
        link=self.root/'link';link.symlink_to(self.credential)
        with self.assertRaises(OSError): monitor.private_json(link,os.getuid())

    def test_config_rejects_unbounded_and_nonfinite_thresholds(self):
        for key,value in (('port',True),('backup_max_bytes',0),('free_percent',float('nan')),
                          ('inodes_free_percent',float('inf')),('namespace','AWS/Reserved'),
                          ('state','relative')):
            config=copy.deepcopy(self.config);config[key]=value
            with self.subTest(key=key),self.assertRaises(monitor.MonitorError):monitor.validate_config(config)

    def test_cloud_failure_propagates_without_success_receipt(self):
        client=Mock();client.put_metric_data.side_effect=RuntimeError('private cloud detail')
        with self.assertRaises(RuntimeError):monitor.publish(self.config,{'Readiness':1,'MonitorComplete':1},client)


if __name__=='__main__':unittest.main()
