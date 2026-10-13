"""Synthetic observations, real loopback HTTP and read-only SQLite qualification."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import context_relay
import relay_monitor as monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'
        context_relay.initialize(self.state, 'synthetic-team', 'jp', ['cj'])
        self.credential = self.state / 'credentials/jp.json'
        os.chmod(self.credential, 0o600)
        self.config = dict(state=str(self.state), credential=str(self.credential), port=8765,
                           backup_max_bytes=1024, free_percent=15, inodes_free_percent=10,
                           region='us-west-2', namespace='Synthetic/Relay', relay_name='synthetic')

    def server(self, status=200, response=None, headers=None):
        observations = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler):
                observations.append(json.loads(handler.rfile.read(int(handler.headers['Content-Length']))))
                handler.send_response(status)
                for name, value in (headers or {}).items(): handler.send_header(name, value)
                handler.end_headers()
                handler.wfile.write(response if response is not None else json.dumps(
                    {'events': [], 'next_cursor': monitor.CURSOR}).encode())
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(), server.server_close()))
        self.config['port'] = server.server_port
        return observations

    def test_readiness_requests_no_event_payload_and_rejects_invalid_response(self):
        seen = self.server()
        monitor.readiness(self.config)
        self.assertEqual(seen, [{'workspace': 'synthetic-team', 'after': monitor.CURSOR}])
        for raw in (b'{}', b'x'*257, b'{"events":["private"],"next_cursor":1}',
                    b'{"events":[],"next_cursor":true}'):
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
                service.state=state; service.workspace='synthetic-team'; service.owner='jp'
        server = context_relay.serve(Index(self.state), 0)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        self.addCleanup(lambda:(server.shutdown(),thread.join(),server.server_close()))
        self.config['port']=server.server_port
        self.assertEqual(monitor.sample(self.config)['Readiness'],1)
        credential=json.loads(self.credential.read_bytes());credential['token']='x'*40
        self.credential.write_text(json.dumps(credential))
        self.assertEqual(monitor.sample(self.config)['Readiness'],0)

    def test_disk_and_inode_pressure_and_reserved_backup_space(self):
        base = dict(f_blocks=1000, f_bavail=500, f_frsize=1024**2, f_files=1000, f_favail=500)
        for changes, expected in (({},0),({'f_bavail':150},1),({'f_favail':100},1),
                                  ({'f_bavail':257,'f_frsize':1024**2},0)):
            with self.subTest(changes=changes), patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**(base|changes))):
                self.assertEqual(monitor.filesystem(self.config)['StoragePressure'],expected)
        self.config['backup_max_bytes']=200*1024**2
        with patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**base)):
            self.assertEqual(monitor.filesystem(self.config)['StoragePressure'],1)
        with patch.object(monitor.os,'statvfs',return_value=SimpleNamespace(**(base|{'f_files':0}))):
            with self.assertRaises(monitor.MonitorError): monitor.filesystem(self.config)

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
        for private in (self.config['state'],credential['token'],credential['workspace'],'jp.json'):
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
