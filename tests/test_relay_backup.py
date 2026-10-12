"""Synthetic backup failures; no AWS account, secret or live service required."""
import datetime as dt
import copy
import json
import os
from pathlib import Path
import tempfile
import sqlite3
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock

import relay_backup as backup
from relay_validation import snapshot as validate_snapshot, Rejected


class FakeCloud:
    def __init__(self, fail_upload=False):
        self.keys, self.metrics, self.alerts = [], [], []
        self.fail_upload = fail_upload

    def upload(self, archive, key):
        if self.fail_upload:
            raise RuntimeError('SYNTHETIC PRIVATE PAYLOAD MUST NOT LEAK')
        self.keys.append(key)

    def age(self, value):
        self.metrics.append(value)

    def alert(self, value):
        self.alerts.append(value)


class Backups(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = dict(spool=str(self.root), max_bytes=1024, recipient='SYNTHETIC',
                           state=str(self.root.parent / (self.root.name + '-state')),
                           app=str(self.root.parent / (self.root.name + '-app')))
        self.now = dt.datetime(2026, 1, 2, 3, tzinfo=dt.timezone.utc)
        self.old_umask = os.umask(0o077)
        self.addCleanup(os.umask, self.old_umask)

    def capture(self, config, stage):
        (stage / 'synthetic-private-state').write_text('SYNTHETIC SECRET')
        return dict(revision=4, event_cursor=6)

    def encrypt(self, stage, archive, recipient):
        archive.write_bytes(b'SYNTHETIC CIPHERTEXT')

    def run_backup(self, cloud=None, **kwargs):
        cloud = cloud or FakeCloud()
        return backup.backup(self.config, cloud, now=self.now,
                             make_capture=kwargs.get('capture', self.capture),
                             make_archive=kwargs.get('encrypt', self.encrypt),
                             clock=kwargs.get('clock', lambda: 0))

    def test_success_commits_only_after_both_uploads_and_removes_plaintext(self):
        cloud = FakeCloud()
        result = self.run_backup(cloud)
        self.assertEqual(len(cloud.keys), 2)
        self.assertTrue(cloud.keys[0].startswith('hourly/'))
        self.assertTrue(cloud.keys[1].startswith('daily/'))
        self.assertEqual(result['revision'], 4)
        self.assertEqual(result['event_cursor'], 6)
        self.assertEqual(cloud.metrics, [0])
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'status.json'})
        self.assertEqual(backup.private_json(self.root / 'status.json')['daily_date'], '2026-01-02')

    def test_daily_marker_prevents_extra_daily_uploads(self):
        self.run_backup()
        cloud = FakeCloud()
        self.run_backup(cloud)
        self.assertEqual(len(cloud.keys), 1)

    def test_failed_upload_does_not_advance_success_or_daily_marker(self):
        self.run_backup()
        original = backup.private_json(self.root / 'status.json')
        self.now += dt.timedelta(days=1)
        cloud = FakeCloud(fail_upload=True)
        with self.assertRaises(backup.BackupError):
            self.run_backup(cloud)
        result = backup.private_json(self.root / 'status.json')
        self.assertEqual(result['last_success'], original['last_success'])
        self.assertEqual(result['daily_date'], original['daily_date'])
        self.assertEqual(cloud.metrics, [])
        self.assertNotIn('PRIVATE PAYLOAD', cloud.alerts[0])
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'status.json'})
        retry = FakeCloud()
        self.run_backup(retry)
        self.assertEqual(len(retry.keys), 2)
        self.assertIn('Recovery', retry.alerts[0])

    def test_encryption_failure_cleans_both_plaintext_and_partial_ciphertext(self):
        def fail(stage, archive, recipient):
            archive.write_bytes(b'partial')
            raise RuntimeError('SYNTHETIC SECRET')
        cloud = FakeCloud()
        with self.assertRaises(backup.BackupError):
            self.run_backup(cloud, encrypt=fail)
        self.assertEqual(cloud.keys, [])
        self.assertNotIn('SYNTHETIC SECRET', json.dumps(cloud.alerts))
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'status.json'})

    def test_capture_failure_removes_staging(self):
        def fail(config, stage):
            (stage / 'private').write_text('SYNTHETIC SECRET')
            raise ValueError()
        with self.assertRaises(backup.BackupError):
            self.run_backup(capture=fail)
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'status.json'})

    def test_monitor_reports_age_and_cannot_reset_success(self):
        self.run_backup()
        cloud = FakeCloud()
        self.assertEqual(backup.monitor(self.config, cloud, self.now.timestamp()+7201), 7201)
        self.assertEqual(cloud.metrics, [7201])
        self.assertEqual(backup.private_json(self.root/'status.json')['last_success'], self.now.timestamp())

    def test_private_config_rejects_symlink_world_readable_or_fifo(self):
        p = self.root/'config'
        p.write_text('{}'); p.chmod(0o644)
        with self.assertRaises(backup.BackupError):
            backup.private_json(p)
        p.chmod(0o600)
        link = self.root/'link'; link.symlink_to(p)
        with self.assertRaises(OSError):
            backup.private_json(link)
        fifo = self.root/'fifo'; os.mkfifo(fifo)
        with self.assertRaises(backup.BackupError):
            backup.private_json(fifo)

    def test_inventory_rejects_links_and_limits_size(self):
        tree = self.root/'tree'; tree.mkdir()
        (tree/'file').write_bytes(b'x'*10)
        with self.assertRaises(backup.BackupError):
            backup.inventory(tree, 5)
        (tree/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(backup.BackupError):
            backup.inventory(tree, 100)

    def test_copy_preserves_uid_gid_mode_and_hash(self):
        tree = self.root/'tree'; tree.mkdir(mode=0o700)
        (tree/'file').write_text('SYNTHETIC'); (tree/'file').chmod(0o600)
        target = self.root/'copy'
        backup.copy_tree(tree, target, 1000)
        self.assertEqual(backup.inventory(tree, 1000), backup.inventory(target, 1000))

    def test_controller_restarts_after_copy_error(self):
        app = self.root/'app'; app.mkdir(); (app/'file').write_text('SYNTHETIC')
        binary = self.root/'binary'; binary.write_bytes(b'SYNTHETIC BINARY')
        inventory = self.root/'inventory'; backup.write_json(inventory, backup.inventory(app, 100_000))
        config = dict(app=str(app), binary=str(binary), binary_pin=backup.file_pin(binary, 1_000_000),
                      runtime_inventory=str(inventory), max_bytes=1000,
                      state=str(self.root/'missing'), service='synthetic.service', service_unit=str(binary),
                      service_unit_pin=backup.file_pin(binary, 1000))
        calls = []
        with self.assertRaises(backup.BackupError):
            backup.capture(config, self.root/'payload', run=lambda *args: calls.append(args))
        self.assertEqual(calls[-1], ('/usr/bin/systemctl', 'start', 'synthetic.service'))

    def test_runtime_cache_is_excluded_but_private_state_is_complete(self):
        tree = self.root/'tree'; tree.mkdir()
        (tree/'source.py').write_text('SYNTHETIC SOURCE')
        cache = tree/'__pycache__'; cache.mkdir(); (cache/'source.pyc').write_bytes(b'SYNTHETIC CACHE')
        target = self.root/'runtime'
        backup.copy_tree(tree, target, 1000, exclude_cache=True)
        self.assertFalse((target/'__pycache__').exists())
        state = self.root/'state'
        backup.copy_tree(tree, state, 1000)
        self.assertTrue((state/'__pycache__/source.pyc').exists())

    def test_cleanup_only_removes_owned_spool_artifacts(self):
        (self.root/'staging-test').mkdir(); (self.root/'staging-test'/'private').write_text('SYNTHETIC')
        (self.root/'pending-test.age').write_text('SYNTHETIC')
        (self.root/'keep').write_text('SYNTHETIC')
        backup.cleanup(self.root)
        self.assertEqual([p.name for p in self.root.iterdir()], ['keep'])

    def test_notification_and_metric_failure_preserve_acknowledged_success(self):
        backup.write_json(self.root/'status.json', {'failed': True, 'last_success': 1})
        cloud = FakeCloud()
        cloud.alert = Mock(side_effect=RuntimeError('SYNTHETIC PRIVATE ERROR'))
        cloud.age = Mock(side_effect=RuntimeError('SYNTHETIC PRIVATE ERROR'))
        result = self.run_backup(cloud)
        self.assertFalse(result['failed'])
        self.assertEqual(result['last_success'], self.now.timestamp())
        self.assertEqual(backup.private_json(self.root/'status.json'), result)
        retry = FakeCloud()
        self.run_backup(retry)
        self.assertEqual(len(retry.keys), 1)  # Successful daily copy was not rolled back.

    def test_preflight_reserves_plaintext_ciphertext_and_overhead(self):
        self.config['max_bytes'] = 1024**3
        cloud = FakeCloud()
        capture = Mock(side_effect=self.capture)
        for free in (self.config['max_bytes'] + backup.RESERVE_BYTES + 1,
                     2*self.config['max_bytes'] + backup.RESERVE_BYTES):
            with self.subTest(free=free), patch.object(backup.shutil, 'disk_usage',
                                                     return_value=Mock(free=free)):
                with self.assertRaises(backup.BackupError):
                    self.run_backup(cloud, capture=capture)
        capture.assert_not_called()
        self.assertEqual(cloud.keys, [])
        with patch.object(backup.shutil, 'disk_usage',
                          return_value=Mock(free=2*self.config['max_bytes'] + backup.RESERVE_BYTES+1)):
            self.run_backup(cloud, capture=capture)
        capture.assert_called_once()

    def test_spool_overlap_and_resolved_aliases_fail_before_capture(self):
        capture = Mock(side_effect=self.capture)
        original = self.config.copy()
        for key in ('state', 'app'):
            for source in (self.root, self.root.parent, self.root/'source'):
                self.config = dict(original, **{key: str(source)})
                with self.subTest(key=key, source=source), self.assertRaises(backup.BackupError):
                    self.run_backup(capture=capture)
        alias = self.root.parent/(self.root.name+'-alias')
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        self.config = dict(original, state=str(alias/'source'))
        with self.assertRaises(backup.BackupError):
            self.run_backup(capture=capture)
        capture.assert_not_called()

    def test_direct_capture_overlap_never_stops_service(self):
        config = dict(state=str(self.root/'state'), app=str(self.root/'app'), binary='unused')
        run = Mock()
        with self.assertRaises(backup.BackupError):
            backup.capture(config, self.root/'state'/'staging', run=run)
        run.assert_not_called()

    def test_inventory_limits_empty_entries_and_metadata_incrementally(self):
        tree = self.root/'tree'; tree.mkdir()
        for i in range(100):
            (tree/str(i)).touch()
        with patch.object(backup, 'sha256', wraps=backup.sha256) as hashing:
            with self.assertRaisesRegex(backup.BackupError, 'entry limit'):
                backup.inventory(tree, 1000, max_entries=5)
            self.assertEqual(hashing.call_count, 4)
        with self.assertRaisesRegex(backup.BackupError, 'metadata limit'):
            backup.inventory(tree, 1000, max_metadata_bytes=50)

    def capture_fixture(self):
        app = self.root/'app'; app.mkdir(); (app/'file').write_text('SYNTHETIC')
        state = self.root/'state'; state.mkdir()
        (state/'snapshots').mkdir(); (state/'store').mkdir()
        text = 'SYNTHETIC NOTE\n'
        snapshot = json.dumps(dict(schema='casita-context-relay.v1', workspace='synthetic-team',
                     revision=1, parent_revision=0, author='jp', created_at='2026-01-02T03:00:00+00:00',
                     tasks={'synthetic-task': dict(task_id='synthetic-task', summary='Synthetic fixture',
                            completed=[], pending=[], blockers=[], source_revision='a'*40,
                            files=[dict(path='note.txt', text=text,
                                        sha256=backup.hashlib.sha256(text.encode()).hexdigest())])},
                     memory=[], decision=None))
        archive = b'SYNTHETIC ARCHIVE'
        pin = dict(revision=1, directory_key='casita.directory.v1:'+'A'*43,
                   snapshot_sha256=backup.hashlib.sha256(snapshot.encode()).hexdigest(),
                   archive_sha256=backup.hashlib.sha256(archive).hexdigest())
        (state/'snapshots'/(pin['archive_sha256']+'.casitar')).write_bytes(archive)
        with sqlite3.connect(state/'relay.sqlite') as db:
            db.executescript(';'.join(backup.RELAY_SCHEMA.values()))
            db.execute('INSERT INTO settings VALUES (?,?)', ('synthetic-team', 'jp'))
            (state/'credentials').mkdir(mode=0o700)
            for name in ('jp', 'cj'):
                token = 'SYNTHETIC-CREDENTIAL-FOR-'+name+'-ONLY-0000'
                db.execute('INSERT INTO members VALUES (?,?,1)', (name, backup.record_digest(token.encode())))
                backup.write_json(state/'credentials'/(name+'.json'), dict(workspace='synthetic-team', token=token))
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (1, snapshot, json.dumps(pin)))
            request_id = 'synthetic-initial'
            request_hash = backup.record_digest(backup.record_canonical(dict(operation='publish', body=dict(
                workspace='synthetic-team', request_id=request_id, expected_revision=0,
                checkpoint=json.loads(snapshot)['tasks']['synthetic-task']))))
            event = dict(kind='publish', author='jp', revision=1, pin=pin,
                         created_at=json.loads(snapshot)['created_at'],
                         request_id=request_id, request_hash=request_hash)
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(event),))
            db.execute('INSERT INTO requests VALUES (?,?,?,?)',
                       ('jp', request_id, request_hash, json.dumps(dict(pin=pin, cursor=1))))
        binary = self.root/'binary'; binary.write_bytes(b'SYNTHETIC BINARY')
        unit = self.root/'service'; unit.write_text('SYNTHETIC UNIT')
        index = self.root/'inventory'; backup.write_json(index, backup.inventory(app, 100_000))
        config = dict(app=str(app), state=str(state), binary=str(binary),
                      binary_pin=backup.file_pin(binary, 1_000_000), runtime_inventory=str(index),
                      service_unit=str(unit), service_unit_pin=backup.file_pin(unit, 1_000_000),
                      service='synthetic.service', max_bytes=1_000_000,
                      source_commit='a'*40, casita_commit='b'*40)
        return config, pin, snapshot

    def test_runtime_or_binary_replacement_between_precheck_and_copy_is_rejected(self):
        config, pin, snapshot = self.capture_fixture()
        for key, message in (('app', 'runtime source pin'), ('binary', 'native binary pin')):
            def run(*args):
                if args[:2] == ('/usr/bin/systemctl', 'start'):
                    path = Path(config[key]) / 'file' if key == 'app' else Path(config[key])
                    path.write_text('SYNTHETIC REPLACEMENT')
            with self.subTest(key=key), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, message):
                    backup.capture(config, Path(temp)/'payload', run=run, ready=lambda c: None)
            (Path(config['app'])/'file').write_text('SYNTHETIC')

    def test_missing_corrupt_historical_archive_or_native_objects_rejects_backup(self):
        config, pin, snapshot = self.capture_fixture()
        state = Path(config['state'])
        path = state/'snapshots'/(pin['archive_sha256']+'.casitar')
        with sqlite3.connect(state/'relay.sqlite') as db:
            second = dict(pin, revision=2)
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (2, snapshot, json.dumps(second)))
        for contents in (None, b'SYNTHETIC CORRUPTION'):
            if contents is None:
                path.unlink()
            else:
                path.write_bytes(contents)
            with self.subTest(contents=contents), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'archive missing or damaged'):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)
        path.write_bytes(b'SYNTHETIC ARCHIVE')
        def run(*args):
            if 'fsck' in args:
                raise backup.BackupError('SYNTHETIC native object damage')
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            with self.assertRaisesRegex(backup.BackupError, 'native object damage'):
                backup.capture(config, Path(temp)/'payload', run=run, ready=lambda c: None)

    def test_archive_root_and_native_checkout_content_must_match_pin(self):
        config, pin, snapshot = self.capture_fixture()
        for bad_root, bad_content in ((True, False), (False, True), (False, False)):
            def run(*args):
                if 'verify' in args:
                    return json.dumps(dict(validity='verified', roots=[
                        'wrong-root' if bad_root else pin['directory_key']])).encode()
                if 'checkout' in args:
                    folder = Path(args[-2]); folder.mkdir()
                    (folder/'snapshot.json').write_text('SYNTHETIC WRONG' if bad_content else snapshot)
            with self.subTest(bad_root=bad_root, bad_content=bad_content), tempfile.TemporaryDirectory(dir=self.root) as temp:
                if bad_root or bad_content:
                    with self.assertRaises(backup.BackupError):
                        backup.capture(config, Path(temp)/'payload', run=run, ready=lambda c: None)
                else:
                    result = backup.capture(config, Path(temp)/'payload', run=run, ready=lambda c: None)
                    self.assertEqual(result['pin'], pin)

    def test_native_ledger_writes_never_enter_recovery_payload_and_scratch_is_removed(self):
        config, pin, snapshot = self.capture_fixture()
        state = Path(config['state'])
        ledger = state/'store'/'synthetic-ledger'
        ledger.write_bytes(b'SYNTHETIC QUIESCENT LEDGER')
        ledger.chmod(0o600)
        original = backup.inventory(state, config['max_bytes'])
        for fail in (False, True):
            stores = []
            with self.subTest(fail=fail), tempfile.TemporaryDirectory(dir=self.root) as temp:
                payload = Path(temp)/'payload'
                def run(*args):
                    if '--repository' in args:
                        store = Path(args[args.index('--repository') + 1])
                        stores.append(store)
                        self.assertNotEqual(store, state/'store')
                        self.assertNotEqual(store, payload/'state'/'store')
                        (store/'synthetic-ledger').write_bytes(b'SYNTHETIC NATIVE MUTATION')
                        (store/'synthetic-new-pin').write_bytes(b'SYNTHETIC OPERATOR PIN')
                        if fail:
                            raise backup.BackupError('synthetic native verification failure')
                    if 'verify' in args:
                        return json.dumps(dict(validity='verified', roots=[pin['directory_key']])).encode()
                    if 'checkout' in args:
                        restored = Path(args[-2]); restored.mkdir()
                        (restored/'snapshot.json').write_text(snapshot)
                if fail:
                    with self.assertRaisesRegex(backup.BackupError, 'native verification failure'):
                        backup.capture(config, payload, run=run, ready=lambda c: None)
                else:
                    backup.capture(config, payload, run=run, ready=lambda c: None)
                self.assertTrue(stores)
                self.assertTrue(all(not store.exists() for store in stores))
                self.assertEqual(backup.inventory(payload/'state', config['max_bytes']), original)
                self.assertEqual(backup.inventory(state, config['max_bytes']), original)

    def test_native_verification_store_copy_obeys_byte_budget(self):
        config, _, _ = self.capture_fixture()
        state = Path(config['state'])
        (state/'store'/'synthetic-oversized').write_bytes(b'x' * 101)
        run = Mock()
        with sqlite3.connect(state/'relay.sqlite') as db:
            with self.assertRaisesRegex(backup.BackupError, 'size limit'):
                backup.verify_snapshots(db, state, Path(config['binary']), run=run, max_bytes=100)
        run.assert_not_called()
        self.assertEqual(list(self.root.glob('staging-native-*')), [])

    def test_manifest_limit_counts_formatted_bytes_before_writing(self):
        config, pin, snapshot = self.capture_fixture()
        for number in range(300):
            (Path(config['state'])/('S'*180+str(number))).touch()
        def run(*args):
            if 'verify' in args:
                return json.dumps(dict(validity='verified', roots=[pin['directory_key']])).encode()
            if 'checkout' in args:
                folder = Path(args[-2]); folder.mkdir()
                (folder/'snapshot.json').write_text(snapshot)
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            first = Path(temp)/'first'
            manifest = backup.capture(config, first, run=run, ready=lambda c: None)
            written = (first/'recovery-manifest.json').stat().st_size
            self.assertGreater(written, len(json.dumps(manifest).encode()) + 100)
            # Leave room for compact JSON but less than the actual formatted file.
            with patch.object(backup, 'MAX_METADATA_BYTES', written - 64*1024 - 100):
                second = Path(temp)/'second'
                with self.assertRaisesRegex(backup.BackupError, 'manifest exceeds'):
                    backup.capture(config, second, run=run, ready=lambda c: None)
                self.assertFalse((second/'recovery-manifest.json').exists())

    def test_status_rename_fsyncs_parent_directory_before_return(self):
        events = []
        fsync, replace = backup.os.fsync, backup.os.replace
        def record_fsync(fd):
            kind = 'directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file'
            events.append(kind)
            fsync(fd)
        def record_replace(*args):
            events.append('rename')
            replace(*args)
        with patch.object(backup.os, 'fsync', side_effect=record_fsync), \
             patch.object(backup.os, 'replace', side_effect=record_replace):
            self.run_backup()
        self.assertEqual(events, ['directory', 'file', 'rename', 'directory', 'directory'])
        self.assertTrue(backup.private_json(self.root/'status.json')['last_success'])

    def test_stale_plaintext_cleanup_does_not_depend_on_cloud_initialization(self):
        stage = self.root/'staging-interrupted'; stage.mkdir()
        (stage/'private-state').write_text('SYNTHETIC PRIVATE STATE')
        archive = self.root/'pending-interrupted.age'; archive.write_text('SYNTHETIC PARTIAL')
        original_stat = Path.stat
        def fake_stat(path, *args, **kwargs):
            if path == self.root:
                return SimpleNamespace(st_uid=0, st_mode=stat.S_IFDIR | 0o700)
            return original_stat(path, *args, **kwargs)
        def fail_cloud(config):
            self.assertFalse(stage.exists())
            self.assertFalse(archive.exists())
            raise RuntimeError('SYNTHETIC unavailable cloud dependency')
        config = dict(self.config, service='context-relay.service')
        with patch.object(backup.os, 'getuid', return_value=0), \
             patch.object(backup, 'private_json', return_value=config), \
             patch.object(Path, 'stat', fake_stat), \
             patch.object(backup, 'Cloud', side_effect=fail_cloud), \
             patch.object(sys, 'argv', ['relay_backup.py', 'backup', '--config', str(self.root/'config')]):
            with self.assertRaisesRegex(RuntimeError, 'unavailable cloud dependency'):
                backup.main()
        self.assertFalse(stage.exists())
        self.assertFalse(archive.exists())

    def test_runtime_mode_drift_is_rejected_before_and_after_copy(self):
        config, pin, snapshot = self.capture_fixture()
        for path in (Path(config['app']), Path(config['app'])/'file'):
            original_mode = stat.S_IMODE(path.stat().st_mode)
            original_hash = backup.sha256(Path(config['app'])/'file')
            path.chmod(original_mode | 0o011)
            run = Mock()
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'runtime source pin'):
                    backup.capture(config, Path(temp)/'payload', run=run, ready=lambda c: None)
            run.assert_not_called()
            path.chmod(original_mode)
            def drift_after_precheck(*args):
                if args[:2] == ('/usr/bin/systemctl', 'start'):
                    path.chmod(original_mode | 0o011)
            with tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'copied runtime source pin'):
                    backup.capture(config, Path(temp)/'payload', run=drift_after_precheck,
                                   ready=lambda c: None)
            self.assertEqual(backup.sha256(Path(config['app'])/'file'), original_hash)
            path.chmod(original_mode)

    def test_oversized_binary_replacement_does_not_fill_capture(self):
        config, pin, snapshot = self.capture_fixture()
        def replace_binary(*args):
            if args[:2] == ('/usr/bin/systemctl', 'start'):
                Path(config['binary']).write_bytes(b'S'*(config['max_bytes']+1))
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            payload = Path(temp)/'payload'
            with self.assertRaisesRegex(backup.BackupError, 'copy input exceeds'):
                backup.capture(config, payload, run=replace_binary, ready=lambda c: None)
            self.assertFalse((payload/'linux-casita').exists())
            self.assertLess(sum(p.stat().st_size for p in payload.rglob('*') if p.is_file()),
                            config['max_bytes'])

    def test_bounded_copy_stops_growth_after_initial_size_check(self):
        source = self.root/'source'; source.write_bytes(b'S'*8)
        target = self.root/'target'
        original_fstat = backup.os.fstat
        def grow(fd):
            info = original_fstat(fd)
            with source.open('ab') as stream:
                stream.write(b'X'*10)
            return info
        with patch.object(backup.os, 'fstat', side_effect=grow):
            with self.assertRaisesRegex(backup.BackupError, 'copy input exceeds'):
                backup.bounded_copy(source, target, 8)
        self.assertLessEqual(target.stat().st_size, 8)

    def test_future_or_invalid_success_timestamp_never_publishes_fresh_metric(self):
        cloud = FakeCloud()
        for success in (self.now.timestamp()+1, 'invalid', float('nan'), float('inf'), -1, True):
            backup.write_json(self.root/'status.json', {'last_success': success})
            with self.subTest(success=success), self.assertRaisesRegex(backup.BackupError, 'timestamp'):
                backup.monitor(self.config, cloud, self.now.timestamp())
        self.assertEqual(cloud.metrics, [])
        backup.write_json(self.root/'status.json', {'last_success': 0})
        self.assertEqual(backup.monitor(self.config, cloud, now=0), 0)

    def test_missing_or_altered_write_path_tables_fail_despite_sqlite_integrity(self):
        config, pin, snapshot = self.capture_fixture()
        for table in ('requests', 'proposals'):
            for altered in (False, True):
                with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                    if altered:
                        db.execute('ALTER TABLE '+table+' ADD COLUMN unexpected TEXT')
                    else:
                        db.execute('DROP TABLE '+table)
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                with self.subTest(table=table, altered=altered), tempfile.TemporaryDirectory(dir=self.root) as temp:
                    with self.assertRaisesRegex(backup.BackupError, 'relay schema mismatch'):
                        backup.capture(config, Path(temp)/'payload', run=lambda *args: None,
                                       ready=lambda c: None)
                with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                    db.execute('DROP TABLE IF EXISTS '+table)
                    db.execute(backup.RELAY_SCHEMA[table])

    def test_service_unit_bytes_or_modes_drifting_after_precheck_fail_capture(self):
        config, pin, snapshot = self.capture_fixture()
        unit = Path(config['service_unit'])
        for mode_only in (False, True):
            def drift(*args):
                if args[:2] == ('/usr/bin/systemctl', 'start'):
                    if mode_only:
                        unit.chmod(0o644)
                    else:
                        unit.write_text('UNQUALIFIED SYNTHETIC UNIT')
            with self.subTest(mode_only=mode_only), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'copied service unit pin mismatch'):
                    backup.capture(config, Path(temp)/'payload', run=drift, ready=lambda c: None)
                self.assertFalse((Path(temp)/'payload/recovery-manifest.json').exists())
            unit.write_text('SYNTHETIC UNIT'); unit.chmod(0o600)
        unit.chmod(0o644)
        run = Mock()
        with self.assertRaisesRegex(backup.BackupError, 'service unit pin mismatch'):
            backup.capture(config, self.root/'never-created', run=run, ready=lambda c: None)
        run.assert_not_called()

    def test_slow_backup_publishes_elapsed_age_without_advancing_recovery_point(self):
        cloud = FakeCloud()
        clock = Mock(side_effect=[10, 7211])
        result = self.run_backup(cloud, clock=clock)
        self.assertEqual(cloud.metrics, [7201])
        self.assertEqual(result['last_success'], self.now.timestamp())
        self.assertEqual(backup.monitor(self.config, FakeCloud(), self.now.timestamp()+7201), 7201)

    def test_extra_fields_float_and_boolean_revision_pins_fail_receiver_compatibility(self):
        config, pin, snapshot = self.capture_fixture()
        for malformed in (dict(pin, unexpected='field'), dict(pin, revision=1.0),
                          dict(pin, revision=True)):
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('UPDATE revisions SET pin=?', (json.dumps(malformed),))
            with self.subTest(pin=malformed), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'snapshot pin schema'):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None,
                                   ready=lambda c: None)

    def test_invalid_event_past_readiness_page_and_event_metadata_bounds(self):
        config, pin, snapshot = self.capture_fixture()
        valid = json.dumps(dict(kind='publish', author='jp', revision=1))
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('DELETE FROM requests'); db.execute('DELETE FROM events')
            db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            db.executemany('INSERT INTO events(payload) VALUES (?)', [(valid,)]*101)
            self.assertEqual(backup.verify_events(db), 101)
            for malformed in ('{', '[]', json.dumps(dict(kind='publish', author='jp', revision=True)),
                              json.dumps(dict(kind='publish', author='jp', revision=1, cursor=1))):
                db.execute('UPDATE events SET payload=? WHERE cursor=101', (malformed,))
                with self.subTest(event=malformed), self.assertRaises(backup.BackupError):
                    backup.verify_events(db)
            db.execute('UPDATE events SET payload=? WHERE cursor=101', (valid,))
            with patch.object(backup, 'MAX_ENTRIES', 100), self.assertRaises(backup.BackupError):
                backup.verify_events(db)
            with patch.object(backup, 'MAX_METADATA_BYTES', 100), self.assertRaises(backup.BackupError):
                backup.verify_events(db)
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('UPDATE events SET payload=? WHERE cursor=101', ('{',))
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            with self.assertRaisesRegex(backup.BackupError, 'invalid persisted JSON'):
                backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)

    def test_corrupt_saved_proposal_or_idempotency_json_is_not_accepted(self):
        config, pin, snapshot = self.capture_fixture()
        for table in ('proposals', 'requests'):
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                if table == 'proposals':
                    db.execute('INSERT INTO proposals(id,payload) VALUES (?,?)', ('a'*64, '{'))
                else:
                    db.execute('INSERT INTO requests VALUES (?,?,?,?)', ('jp', 'synthetic-id', 'a'*64, '{'))
            with self.subTest(table=table), tempfile.TemporaryDirectory(dir=self.root) as temp, \
                 patch.object(backup, 'verify_snapshots'):
                with self.assertRaisesRegex(backup.BackupError, 'invalid persisted JSON'):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('DELETE FROM '+table)

    def test_digest_rejects_oversize_links_and_fifo_before_reading(self):
        source = self.root/'source'; source.write_bytes(b'S'*10)
        with self.assertRaises(backup.BackupError):
            backup.sha256(source, 5)
        fifo = self.root/'fifo'; os.mkfifo(fifo)
        with self.assertRaises(backup.BackupError):
            backup.sha256(fifo)
        link = self.root/'link'; link.symlink_to(source)
        with self.assertRaises(OSError):
            backup.sha256(link)

    def test_hash_consistent_semantically_invalid_snapshot_is_rejected(self):
        config, pin, raw = self.capture_fixture()
        good = json.loads(raw)
        variants = [{}, dict(good, workspace='another-team'), dict(good, revision=True),
                    dict(good, tasks={}), dict(good, memory='broken')]
        wrong_checkpoint = copy.deepcopy(good)
        wrong_checkpoint['tasks']['synthetic-task']['files'][0]['sha256'] = 'f'*64
        variants.append(wrong_checkpoint)
        for value in variants:
            raw = json.dumps(value)
            changed_pin = dict(pin, snapshot_sha256=backup.hashlib.sha256(raw.encode()).hexdigest())
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('UPDATE revisions SET snapshot=?,pin=?', (raw, json.dumps(changed_pin)))
            with self.subTest(snapshot=value), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaises(Rejected):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None,
                                   ready=lambda c: None)

    def test_shared_snapshot_rules_accept_original_citation_after_task_changes(self):
        config, pin, raw = self.capture_fixture()
        value = json.loads(raw); value.update(revision=2, parent_revision=1)
        source = value['tasks']['synthetic-task']['files'][0]
        proposal = dict(author='cj', base_revision=1, created_at=value['created_at'],
                        task_id='synthetic-task', scope='workspace', source_revision='a'*40,
                        entry_id='synthetic-memory', statement='Synthetic lesson',
                        citation=dict(path=source['path'], sha256=source['sha256'],
                                      start_line=1, end_line=1, excerpt=['SYNTHETIC NOTE']))
        from relay_validation import canonical, digest
        entry = dict(proposal, accepted_by='jp', accepted_at=value['created_at'],
                     proposal_id=digest(canonical(proposal)))
        value['memory'] = [entry]
        value['tasks']['synthetic-task']['source_revision'] = 'b'*40
        value['tasks']['synthetic-task']['files'] = [dict(path='new.txt', text='NEW', sha256=digest(b'NEW'))]
        self.assertIs(validate_snapshot(value, 'synthetic-team', 2), value)
        for field, bad in (('scope', 'invalid'), ('citation', {}), ('accepted_by', True)):
            malformed = copy.deepcopy(value); malformed['memory'][0][field] = bad
            with self.subTest(field=field), self.assertRaises(Rejected):
                validate_snapshot(malformed, 'synthetic-team', 2)

    def test_native_binary_mode_drift_is_rejected_before_and_after_copy(self):
        config, pin, snapshot = self.capture_fixture()
        binary = Path(config['binary']); binary.chmod(0o644)
        run = Mock()
        with self.assertRaisesRegex(backup.BackupError, 'native binary pin mismatch'):
            backup.capture(config, self.root/'never-created', run=run, ready=lambda c: None)
        run.assert_not_called(); binary.chmod(0o600)
        def drift(*args):
            if args[:2] == ('/usr/bin/systemctl', 'start'):
                binary.chmod(0o777)
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            with self.assertRaisesRegex(backup.BackupError, 'copied native binary pin mismatch'):
                backup.capture(config, Path(temp)/'payload', run=drift, ready=lambda c: None)

    def test_member_count_and_metadata_are_bounded_before_rows_are_loaded(self):
        config, pin, snapshot = self.capture_fixture()
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.executemany('INSERT INTO members VALUES (?,?,1)',
                           [('synthetic-'+str(i), str(i).zfill(64)) for i in range(10)])
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            with self.assertRaisesRegex(backup.BackupError, 'member index exceeds'):
                backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute("DELETE FROM members WHERE name LIKE 'synthetic-%'")
        token_hash = backup.record_digest(backup.private_json(Path(config['state'])/'credentials/cj.json')['token'].encode())
        for field, malformed in (('name', 'x'*65), ('token_hash', 'x'*65), ('enabled', 'x'*100_000)):
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('UPDATE members SET '+field+'=? WHERE token_hash=?', (malformed, token_hash))
            with self.subTest(field=field), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'member metadata exceeds'):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute("DELETE FROM members WHERE name != 'jp'")
                db.execute('INSERT INTO members VALUES (?,?,1)', ('cj', token_hash))

    def test_saved_proposal_schema_id_flag_and_source_binding(self):
        config, pin, raw = self.capture_fixture()
        snapshot = json.loads(raw); source = snapshot['tasks']['synthetic-task']['files'][0]
        proposal = dict(author='cj', base_revision=1, created_at=snapshot['created_at'],
                        task_id='synthetic-task', scope='workspace', source_revision='a'*40,
                        entry_id='synthetic-memory', statement='Synthetic lesson',
                        citation=dict(path=source['path'], sha256=source['sha256'],
                                      start_line=1, end_line=1, excerpt=['SYNTHETIC NOTE']))
        proposal_id = backup.record_digest(backup.record_canonical(proposal))
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('INSERT INTO proposals VALUES (?,?,0)', (proposal_id, json.dumps(proposal)))
            backup.verify_auxiliary_json(db)
            for changed, accepted, wrong_id in ((proposal, 0, True), (proposal, 2, False),
                                                (proposal, 1, False), ({}, 0, False),
                                                (dict(proposal, source_revision='b'*40), 0, False)):
                db.execute('DELETE FROM proposals')
                identity = 'f'*64 if wrong_id else backup.record_digest(backup.record_canonical(changed))
                db.execute('INSERT INTO proposals VALUES (?,?,?)', (identity, json.dumps(changed), accepted))
                with self.subTest(changed=changed, accepted=accepted, wrong_id=wrong_id), \
                     self.assertRaises((backup.BackupError, Rejected)):
                    backup.verify_auxiliary_json(db)

    def test_settings_require_one_valid_enabled_owner(self):
        config, pin, raw = self.capture_fixture()
        for owner, disabled, duplicate in (('missing-owner', False, False), ('jp', True, False),
                                            ('jp', False, True), ('INVALID OWNER', False, False)):
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('DELETE FROM settings')
                db.execute('INSERT INTO settings VALUES (?,?)', ('synthetic-team', owner))
                if duplicate:
                    db.execute('INSERT INTO settings VALUES (?,?)', ('synthetic-team', owner))
                db.execute('UPDATE members SET enabled=? WHERE name=?', (0 if disabled else 1, 'jp'))
            with self.subTest(owner=owner, disabled=disabled, duplicate=duplicate), \
                 tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaises(backup.BackupError):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None,
                                   ready=lambda c: None)

    def test_cached_results_must_match_committed_events_and_revisions(self):
        config, pin, raw = self.capture_fixture()
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('DELETE FROM requests'); db.execute('DELETE FROM events')
            db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            event = dict(kind='publish', author='jp', revision=1, pin=pin)
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(event),))
            good = dict(pin=pin, cursor=1)
            db.execute('INSERT INTO requests VALUES (?,?,?,?)',
                       ('jp', 'synthetic-request', backup.record_digest(backup.record_canonical(dict(
                           operation='publish', body=dict(workspace='synthetic-team',
                           request_id='synthetic-request', expected_revision=0,
                           checkpoint=json.loads(raw)['tasks']['synthetic-task'])))), json.dumps(good)))
            backup.verify_auxiliary_json(db)
            for result in (dict(pin=pin, cursor=999), dict(cursor=1),
                           dict(good, unexpected=True),
                           dict(pin=dict(pin, snapshot_sha256='f'*64), cursor=1)):
                db.execute('UPDATE requests SET result=?', (json.dumps(result),))
                with self.subTest(result=result), self.assertRaises(backup.BackupError):
                    backup.verify_auxiliary_json(db)

    def test_accepted_memory_must_name_configured_owner(self):
        config, pin, raw = self.capture_fixture()
        value = json.loads(raw); value.update(revision=2, parent_revision=1)
        source = value['tasks']['synthetic-task']['files'][0]
        proposal = dict(author='cj', base_revision=1, created_at=value['created_at'],
                        task_id='synthetic-task', scope='workspace', source_revision='a'*40,
                        entry_id='synthetic-memory', statement='Synthetic lesson',
                        citation=dict(path=source['path'], sha256=source['sha256'],
                                      start_line=1, end_line=1, excerpt=['SYNTHETIC NOTE']))
        identity = backup.record_digest(backup.record_canonical(proposal))
        value['memory'] = [dict(proposal, accepted_by='cj', accepted_at=value['created_at'],
                               proposal_id=identity)]
        for decision in (None, dict(proposal_id=identity, accepted_by='cj')):
            value['decision'] = decision
            raw = json.dumps(value)
            changed_pin = dict(pin, revision=2, snapshot_sha256=backup.record_digest(raw.encode()))
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('DELETE FROM revisions')
                db.execute('INSERT INTO revisions VALUES (?,?,?)', (2, raw, json.dumps(changed_pin)))
                with self.subTest(decision=decision), self.assertRaisesRegex(backup.BackupError, 'configured owner'):
                    backup.verify_snapshots(db, Path(config['state']), Path(config['binary']), run=lambda *a: None)

    def test_swapped_cached_results_reject_new_and_legacy_events(self):
        config, pin, raw = self.capture_fixture()
        snapshot = json.loads(raw); checkpoint = snapshot['tasks']['synthetic-task']
        second = copy.deepcopy(snapshot); second.update(revision=2, parent_revision=1)
        second['tasks']['synthetic-task']['summary'] = 'Synthetic second revision'
        second_raw = json.dumps(second)
        second_pin = dict(pin, revision=2, snapshot_sha256=backup.record_digest(second_raw.encode()))
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (2, second_raw, json.dumps(second_pin)))
            for legacy in (False, True):
                db.execute('DELETE FROM requests'); db.execute('DELETE FROM events')
                db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
                results, identities = [], []
                for index, current_pin in enumerate((pin, second_pin)):
                    request_id = 'synthetic-request-'+str(index)
                    request_hash = backup.record_digest(backup.record_canonical(dict(operation='publish', body=dict(
                        workspace='synthetic-team', request_id=request_id, expected_revision=index,
                        checkpoint=(checkpoint if index == 0 else second['tasks']['synthetic-task'])))))
                    event = dict(kind='publish', author='jp', revision=index+1, pin=current_pin)
                    if not legacy:
                        event.update(request_id=request_id, request_hash=request_hash)
                    cursor = db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(event),)).lastrowid
                    result = dict(pin=current_pin, cursor=cursor)
                    results.append(result); identities.append(request_id)
                    db.execute('INSERT INTO requests VALUES (?,?,?,?)',
                               ('jp', request_id, request_hash, json.dumps(result)))
                backup.verify_auxiliary_json(db)
                for request_id, result in zip(identities, reversed(results)):
                    db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(result), request_id))
                with self.subTest(legacy=legacy), self.assertRaisesRegex(backup.BackupError, 'identity|content hash'):
                    backup.verify_auxiliary_json(db)

    def test_cleanup_fsyncs_spool_after_plaintext_removal(self):
        stage = self.root/'staging-interrupted'; stage.mkdir()
        (stage/'synthetic-plaintext').write_text('SYNTHETIC')
        (self.root/'pending-interrupted.age').write_text('SYNTHETIC PARTIAL')
        seen = []
        fsync = backup.os.fsync
        def record(fd):
            self.assertFalse(stage.exists())
            self.assertFalse((self.root/'pending-interrupted.age').exists())
            self.assertTrue(stat.S_ISDIR(backup.os.fstat(fd).st_mode))
            seen.append(fd); fsync(fd)
        with patch.object(backup.os, 'fsync', side_effect=record):
            backup.cleanup(self.root)
        self.assertEqual(len(seen), 1)

    def test_enabled_member_credential_matches_workspace_and_token_hash(self):
        config, pin, raw = self.capture_fixture()
        state = Path(config['state']); path = state/'credentials/cj.json'
        credential = backup.private_json(path)
        token_hash = backup.record_digest(credential['token'].encode())
        backup.verify_member_credential(state, 'cj', token_hash, 'synthetic-team')
        for value in (dict(credential, workspace='wrong-workspace'),
                      dict(credential, token='OTHER-SYNTHETIC-CREDENTIAL-000000'), {}):
            backup.write_json(path, value)
            with self.subTest(value=value), self.assertRaises(backup.BackupError):
                backup.verify_member_credential(state, 'cj', token_hash, 'synthetic-team')
        backup.write_json(path, credential)
        with sqlite3.connect(state/'relay.sqlite') as db:
            db.execute("UPDATE members SET token_hash=? WHERE name='cj'", ('f'*64,))
        with tempfile.TemporaryDirectory(dir=self.root) as temp:
            with self.assertRaisesRegex(backup.BackupError, 'credential mismatch'):
                backup.capture(config, Path(temp)/'payload', run=lambda *a: None, ready=lambda c: None)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            backup.verify_member_credential(state, 'cj', token_hash, 'synthetic-team')

    def test_exhausted_or_inconsistent_event_sequence_is_rejected(self):
        config, pin, raw = self.capture_fixture()
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            db.execute('DELETE FROM requests'); db.execute('DELETE FROM events')
            db.execute("DELETE FROM sqlite_sequence WHERE name='events'")
            self.assertEqual(backup.verify_events(db), 0)
            db.execute('INSERT INTO events(payload) VALUES (?)',
                       (json.dumps(dict(kind='publish', author='jp', revision=1)),))
            self.assertEqual(backup.verify_events(db), 1)
            for sequence in (9223372036854775807, -1, 0, 'x'*100_000):
                db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='events'", (sequence,))
                with self.subTest(sequence=sequence), self.assertRaisesRegex(backup.BackupError, 'event sequence'):
                    backup.verify_events(db)
            db.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='events'")
            db.execute('INSERT INTO sqlite_sequence VALUES (?,?)', ('unexpected', 1))
            with self.assertRaisesRegex(backup.BackupError, 'event sequence'):
                backup.verify_events(db)

    def test_plaintext_removal_is_durable_before_upload_and_fsync_failure_blocks_upload(self):
        cloud = FakeCloud(); boundaries = []
        real = backup.fsync_directory
        def record(path):
            self.assertFalse(any(p.name.startswith('staging-') for p in self.root.iterdir()))
            boundaries.append('durable'); real(path)
        upload = cloud.upload
        def checked_upload(*args):
            self.assertTrue(boundaries)
            upload(*args)
        cloud.upload = checked_upload
        with patch.object(backup, 'fsync_directory', side_effect=record):
            self.run_backup(cloud)
        cloud = FakeCloud(); calls = []
        def fail_first(path):
            calls.append(path)
            if len(calls) == 1:
                raise OSError('SYNTHETIC fsync failure')
            real(path)
        with patch.object(backup, 'fsync_directory', side_effect=fail_first):
            with self.assertRaises(backup.BackupError):
                self.run_backup(cloud)
        self.assertEqual(cloud.keys, [])
        self.assertEqual(backup.private_json(self.root/'status.json')['failure_phase'], 'encryption')

    def test_legacy_publish_hash_must_name_the_unique_changed_task(self):
        config, pin, raw = self.capture_fixture()
        previous = json.loads(raw); previous.update(revision=2, parent_revision=1)
        other = copy.deepcopy(previous['tasks']['synthetic-task']); other['task_id'] = 'synthetic-other'
        previous['tasks']['synthetic-other'] = other
        current = copy.deepcopy(previous); current.update(revision=3, parent_revision=2)
        current['tasks']['synthetic-task']['summary'] = 'Synthetic change to first task'
        result = dict(pin=dict(pin, revision=3), cursor=3)
        event = dict(kind='publish', author='jp', revision=3)
        def request_hash(checkpoint):
            return backup.record_digest(backup.record_canonical(dict(operation='publish', body=dict(
                workspace='synthetic-team', request_id='synthetic-third', expected_revision=2,
                checkpoint=checkpoint))))
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            for revision, value in ((2, previous), (3, current)):
                db.execute('INSERT INTO revisions VALUES (?,?,?)', (revision, json.dumps(value), '{}'))
            with self.assertRaisesRegex(backup.BackupError, 'content hash'):
                backup.verify_request_identity(db, 'synthetic-third', request_hash(other), event, result)
            backup.verify_request_identity(db, 'synthetic-third',
                                           request_hash(current['tasks']['synthetic-task']), event, result)
            unchanged = copy.deepcopy(previous); unchanged.update(revision=3, parent_revision=2)
            db.execute('UPDATE revisions SET snapshot=? WHERE revision=3', (json.dumps(unchanged),))
            with self.assertRaisesRegex(backup.BackupError, 'unique changed checkpoint'):
                backup.verify_request_identity(db, 'synthetic-third', request_hash(other), event, result)
            # Explicit identity permits a valid no-op from the current producer.
            event.update(request_id='synthetic-third', request_hash=request_hash(other))
            backup.verify_request_identity(db, 'synthetic-third', request_hash(other), event, result)

    def test_missing_middle_event_rejects_even_with_matching_final_sequence(self):
        config, pin, raw = self.capture_fixture()
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            for revision in (2, 3):
                db.execute('INSERT INTO events(payload) VALUES (?)',
                           (json.dumps(dict(kind='publish', author='jp', revision=revision)),))
            db.execute('DELETE FROM events WHERE cursor=2')
            self.assertEqual(db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()[0], 3)
            with self.assertRaisesRegex(backup.BackupError, 'event cursor'):
                backup.verify_events(db)

    def test_publish_transition_rejects_unrelated_task_or_memory_changes(self):
        config, pin, raw = self.capture_fixture()
        previous = json.loads(raw)
        current = copy.deepcopy(previous); current.update(revision=2, parent_revision=1)
        current['tasks']['synthetic-task']['summary'] = 'Synthetic authorized update'
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            first = backup.verify_auxiliary_json(db)
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (2, json.dumps(current), '{}'))
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(dict(
                kind='publish', author='jp', revision=2, created_at=current['created_at'])),))
            requests = {**first, 2: {}}
            backup.verify_transitions(db, requests)
            for field in ('tasks', 'memory'):
                value = copy.deepcopy(current)
                if field == 'tasks':
                    other = copy.deepcopy(value['tasks']['synthetic-task']); other['task_id'] = 'synthetic-other'
                    value['tasks']['synthetic-other'] = other
                else:
                    value['memory'] = ['SYNTHETIC unauthorized memory']
                db.execute('UPDATE revisions SET snapshot=? WHERE revision=2', (json.dumps(value),))
                with self.subTest(field=field), self.assertRaisesRegex(backup.BackupError, 'unrelated context'):
                    backup.verify_transitions(db, requests)

    def test_acceptance_transition_preserves_tasks_and_unrelated_memory(self):
        config, pin, raw = self.capture_fixture()
        previous = json.loads(raw); source = previous['tasks']['synthetic-task']['files'][0]
        proposal = dict(author='cj', base_revision=1, created_at=previous['created_at'],
                        task_id='synthetic-task', scope='workspace', source_revision='a'*40,
                        entry_id='synthetic-memory', statement='Synthetic lesson',
                        citation=dict(path=source['path'], sha256=source['sha256'],
                                      start_line=1, end_line=1, excerpt=['SYNTHETIC NOTE']))
        identity = backup.record_digest(backup.record_canonical(proposal))
        current = copy.deepcopy(previous); current.update(revision=2, parent_revision=1,
            decision=dict(proposal_id=identity, accepted_by='jp'),
            memory=[dict(proposal, accepted_by='jp', accepted_at=previous['created_at'], proposal_id=identity)])
        with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
            first = backup.verify_auxiliary_json(db)
            db.execute('INSERT INTO proposals VALUES (?,?,1)', (identity, json.dumps(proposal)))
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(dict(
                kind='propose', author='cj', revision=1, created_at=proposal['created_at'])),))
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(dict(
                kind='accept', author='jp', revision=2, created_at=current['created_at'])),))
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (2, json.dumps(current), '{}'))
            requests = {**first, 2: dict(proposal=proposal, proposal_id=identity), 3: {}}
            backup.verify_transitions(db, requests)
            db.execute('UPDATE proposals SET accepted=0')
            with self.assertRaisesRegex(backup.BackupError, 'marked accepted'):
                backup.verify_transitions(db, requests)
            db.execute('UPDATE proposals SET accepted=1')
            for field in ('tasks', 'memory'):
                value = copy.deepcopy(current)
                if field == 'tasks':
                    value['tasks']['synthetic-task']['summary'] = 'Synthetic unauthorized task change'
                else:
                    value['memory'].append('SYNTHETIC unauthorized memory')
                db.execute('UPDATE revisions SET snapshot=? WHERE revision=2', (json.dumps(value),))
                with self.subTest(field=field), self.assertRaisesRegex(backup.BackupError, 'changed tasks|unrelated memory'):
                    backup.verify_transitions(db, requests)

    def test_cloud_upload_requires_sse_and_exact_server_checksum(self):
        archive = self.root/'archive'; archive.write_bytes(b'SYNTHETIC CIPHERTEXT')
        cloud = backup.Cloud.__new__(backup.Cloud)
        cloud.config = {'bucket': 'synthetic-bucket'}
        cloud.s3 = Mock()
        expected = backup.base64.b64encode(bytes.fromhex(backup.sha256(archive))).decode()
        cloud.s3.put_object.return_value = {'ChecksumSHA256': expected}
        cloud.upload(archive, 'hourly/synthetic.age')
        arguments = cloud.s3.put_object.call_args.kwargs
        self.assertEqual(arguments['ServerSideEncryption'], 'AES256')
        self.assertEqual(arguments['ChecksumSHA256'], expected)
        cloud.s3.put_object.return_value = {'ChecksumSHA256': 'wrong'}
        with self.assertRaises(backup.BackupError):
            cloud.upload(archive, 'hourly/synthetic.age')


if __name__ == '__main__':
    unittest.main()
