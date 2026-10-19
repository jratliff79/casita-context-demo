"""Bounded numeric traces and private preparation failures; no payload logging."""
from contextlib import contextmanager
import importlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import context_relay as relay
import relay_monitor as monitor
import relay_workload_controller as controller
from relay_workload_plan import make_plan


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)

    def trace(self, **kwargs):
        try:
            module = importlib.import_module('relay_workload_diagnostics')
        except ModuleNotFoundError:
            self.fail('bounded stage diagnostics are unavailable')
        trace = module.Diagnostics(self.root, **kwargs)
        self.addCleanup(trace.close)
        return trace

    def rows(self):
        return [json.loads(line) for line in
                (self.root / 'stage-timing.jsonl').read_text().splitlines()]

    def test_nested_native_spans_join_to_numeric_request_tag(self):
        trace = self.trace()
        with trace.request():
            trace.bind_request('publish', '1042')
            with trace.span('server.handle'):
                with trace.span('native.fsck'):
                    pass
        rows = self.rows()
        native = next(row for row in rows if row['stage'] == 'native.fsck')
        handle = next(row for row in rows if row['stage'] == 'server.handle')
        self.assertEqual(native['parent_id'], handle['id'])
        self.assertEqual(native['trace_tag'], 1042)
        self.assertEqual(native['request_id'], handle['request_id'])
        self.assertGreaterEqual(native['elapsed_ns'], 0)
        self.assertEqual(native['outcome'], 'ok')
        self.assertTrue(trace.finish()['complete'])

    def test_parallel_requests_keep_separate_context_and_valid_bounded_json(self):
        trace = self.trace(max_records=12)
        barrier = threading.Barrier(2)
        def work(tag):
            with trace.request():
                trace.bind_request('publish', str(tag))
                barrier.wait(timeout=3)
                for _ in range(10):
                    with trace.span('native.import'):
                        pass
        threads = [threading.Thread(target=work, args=(tag,)) for tag in [11, 22]]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        summary = trace.finish()
        rows = self.rows()
        self.assertEqual(len(rows), 12)
        self.assertFalse(summary['complete'])
        self.assertGreater(summary['records_dropped'], 0)
        self.assertEqual(len({row['id'] for row in rows}), 12)
        for request in {row['request_id'] for row in rows}:
            self.assertEqual(len({row['trace_tag'] for row in rows
                                  if row['request_id'] == request}), 1)

    def test_byte_cap_retains_prefix_and_never_changes_operation_result(self):
        trace = self.trace(max_records=100, max_bytes=1024)
        for _ in range(20):
            with trace.span('native.fsck'):
                result = 42
        self.assertEqual(result, 42)
        summary = trace.finish()
        self.assertLessEqual((self.root / 'stage-timing.jsonl').stat().st_size, 1024)
        self.assertFalse(summary['complete'])
        self.assertGreater(summary['records_dropped'], 0)

    def test_write_error_is_incomplete_without_masking_the_primary_exception(self):
        trace = self.trace()
        with patch('os.write', side_effect=OSError('PRIVATE payload /secret/path')):
            with self.assertRaisesRegex(RuntimeError, 'original'):
                with trace.span('native.fsck'):
                    raise RuntimeError('original')
        summary = trace.finish()
        self.assertFalse(summary['complete'])
        self.assertTrue(summary['io_failed'])
        self.assertNotIn('PRIVATE', json.dumps(summary))

    def test_invalid_labels_and_request_tags_cannot_become_payload_channels(self):
        trace = self.trace()
        with self.assertRaises(ValueError):
            with trace.span('PRIVATE context /secret'):
                pass
        with trace.request():
            trace.bind_request('/private/path', 'PRIVATE-TOKEN')
            with trace.span('http.accepted'):
                pass
        row = self.rows()[0]
        self.assertEqual(row['operation'], 'unknown')
        self.assertIsNone(row['trace_tag'])
        self.assertNotIn('PRIVATE', json.dumps(row))

    def test_phase_and_acknowledged_revision_survive_failure_with_safe_frames(self):
        trace = self.trace()
        try:
            with trace.phase('prepare.seed_history'):
                trace.progress(50)
                monitor.state_size({'state': str(self.root / 'PRIVATE-TOKEN')})
        except FileNotFoundError as error:
            trace.failure(error)
        status = json.loads((self.root / 'preparation-status.json').read_text())
        failure = json.loads((self.root / 'failure.json').read_text())
        self.assertEqual(status['stage'], 'prepare.seed_history')
        self.assertEqual(status['acknowledged_revisions'], 50)
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(failure['phase'], 'prepare.seed_history')
        self.assertEqual(failure['error_type'], 'FileNotFoundError')
        self.assertTrue(any(frame['file'] == 'relay_monitor.py' for frame in failure['frames']))
        self.assertLessEqual(len(failure['frames']), 12)
        self.assertNotIn('PRIVATE', (self.root / 'failure.json').read_text())
        self.assertNotIn(str(self.root), (self.root / 'failure.json').read_text())

    def test_cleanup_failure_cannot_replace_the_original_failure(self):
        trace = self.trace()
        trace.failure(FileNotFoundError('PRIVATE'), phase='prepare.seed_history')
        for _ in range(10):
            trace.failure(RuntimeError('PRIVATE'), phase='cleanup')
        failure = json.loads((self.root / 'failure.json').read_text())
        self.assertEqual(failure['phase'], 'prepare.seed_history')
        self.assertEqual(failure['error_type'], 'FileNotFoundError')
        self.assertLessEqual(len(failure['secondary_failures']), 2)
        self.assertLessEqual((self.root / 'failure.json').stat().st_size, 8192)

    def test_private_modes_and_existing_trace_refusal(self):
        previous = os.umask(0o022)
        try:
            trace = self.trace()
            with trace.phase('prepare.initialize'):
                pass
            trace.finish()
        finally:
            os.umask(previous)
        for name in ['stage-timing.jsonl', 'stage-timing-summary.json', 'preparation-status.json']:
            self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
        old = (self.root / 'stage-timing.jsonl').read_bytes()
        with self.assertRaises(FileExistsError):
            self.trace()
        self.assertEqual((self.root / 'stage-timing.jsonl').read_bytes(), old)

    def test_progress_is_bounded_numeric_and_nonregressing(self):
        trace = self.trace()
        with trace.phase('prepare.seed_history'):
            trace.progress(50)
            for value in [-1, True, 1001, 49, 'PRIVATE']:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    trace.progress(value)
        self.assertLessEqual((self.root / 'preparation-status.json').stat().st_size, 1024)

    def test_missing_or_duplicate_server_request_tags_prevent_complete_diagnostics(self):
        trace = self.trace()
        for _ in range(2):
            with trace.request():
                trace.bind_request('publish', '1042')
                with trace.span('http.accepted'):
                    pass
        summary = trace.finish(expected_tags={1042, 1043})
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['missing_requests'], 1)
        self.assertEqual(summary['duplicate_requests'], 1)


class RecordingTiming:
    def __init__(self):
        self.stages = []
        self.bindings = []

    @contextmanager
    def span(self, stage):
        self.stages.append(stage)
        yield

    @contextmanager
    def request(self):
        yield

    def bind_request(self, operation, tag):
        self.bindings.append((operation, tag))


class StageIntegrationTests(unittest.TestCase):
    def test_real_http_handler_records_boundaries_and_only_the_numeric_trace_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / 'credential.json'
            credential.write_text(json.dumps({'workspace': 'synthetic', 'token': 'x' * 40}))
            timing = RecordingTiming()
            class Service:
                def handle(self, token, operation, body):
                    return {'events': [], 'next_cursor': 0}
            service = Service(); service.timing = timing
            server = relay.serve(service, 0)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                kwargs = {'trace_tag': 1042} if 'trace_tag' in inspect.signature(relay.client).parameters else {}
                result = relay.client('http://127.0.0.1:' + str(server.server_port),
                                      credential, 'updates', {'after': 0}, **kwargs)
                self.assertEqual(result['next_cursor'], 0)
                self.assertIn(('updates', '1042'), timing.bindings)
                self.assertTrue({'http.accepted', 'http.read_decode', 'http.handle',
                                 'http.response_write'} <= set(timing.stages))
            finally:
                server.shutdown(); thread.join(); server.server_close()

    def test_snapshot_records_native_stages_without_removing_integrity_or_durability(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'snapshots').mkdir()
            service = relay.Relay.__new__(relay.Relay)
            service.state, service.timing = state, RecordingTiming()
            snapshot = {'revision': 1, 'synthetic': 'PRIVATE payload'}
            calls = []
            class Store:
                def run(self, *args):
                    calls.append(args[0])
                    if args[0] == 'archive':
                        Path(args[args.index('--output') + 1]).write_bytes(b'synthetic archive')
                    if args[0] == 'checkout':
                        target = Path(args[2]); target.mkdir()
                        (target / 'snapshot.json').write_bytes(relay.canonical(snapshot))
                def roots(self):
                    calls.append('root ls')
                    return {'relay/' + '0' * 32: 'synthetic-key'}
            service.store = Store()
            with patch.object(relay.secrets, 'token_hex', return_value='0' * 32), \
                    patch.object(relay.os, 'fsync') as sync:
                pin = service.save_snapshot(Mock(), snapshot)
            self.assertEqual(calls, ['import', 'root ls', 'archive', 'fsck', 'checkout'])
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(pin['snapshot_sha256'], relay.digest(relay.canonical(snapshot)))
            self.assertTrue({'native.import', 'native.root_ls', 'native.archive_create',
                             'native.fsck', 'native.checkout', 'server.snapshot_persist'}
                            <= set(service.timing.stages))

    def test_real_startup_failure_has_phase_and_frames_without_a_success_receipt(self):
        original = Path.is_file
        def exists(path):
            return True if str(path) in ('/usr/bin/age', '/usr/bin/age-keygen') else original(path)
        with tempfile.TemporaryDirectory() as directory, patch.object(Path, 'is_file', exists), \
                patch.object(controller.platform, 'system', return_value='Linux'), \
                patch.object(controller.relay, 'initialize', side_effect=RuntimeError('PRIVATE /secret')):
            output = Path(directory) / 'failed'
            kwargs = {'stage_timing': True} if 'stage_timing' in inspect.signature(controller.run).parameters else {}
            with self.assertRaises(RuntimeError):
                controller.run(sys.executable, output, make_plan(30, 20, 10), **kwargs)
            failure = json.loads((output / 'failure.json').read_text())
            self.assertEqual(failure.get('phase'), 'prepare.initialize')
            self.assertTrue(failure.get('frames'))
            self.assertNotIn('PRIVATE', json.dumps(failure))
            self.assertFalse((output / 'receipt.json').exists())


if __name__ == '__main__':
    unittest.main()
