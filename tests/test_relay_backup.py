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
            db.executemany('INSERT INTO members VALUES (?,?,1)', [('jp', 'a'*64), ('cj', 'b'*64)])
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (1, snapshot, json.dumps(pin)))
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
        self.assertEqual(events, ['file', 'rename', 'directory'])
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
                    db.execute('INSERT INTO proposals(id,payload) VALUES (?,?)', ('synthetic-id', '{'))
                else:
                    db.execute('INSERT INTO requests VALUES (?,?,?,?)', ('jp', 'synthetic-id', 'hash', '{'))
            with self.subTest(table=table), tempfile.TemporaryDirectory(dir=self.root) as temp:
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
        for field, malformed in (('name', 'x'*65), ('token_hash', 'x'*65), ('enabled', 'x'*100_000)):
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute('UPDATE members SET '+field+'=? WHERE token_hash=?', (malformed, 'b'*64))
            with self.subTest(field=field), tempfile.TemporaryDirectory(dir=self.root) as temp:
                with self.assertRaisesRegex(backup.BackupError, 'member metadata exceeds'):
                    backup.capture(config, Path(temp)/'payload', run=lambda *args: None, ready=lambda c: None)
            with sqlite3.connect(Path(config['state'])/'relay.sqlite') as db:
                db.execute("DELETE FROM members WHERE name != 'jp'")
                db.execute('INSERT INTO members VALUES (?,?,1)', ('cj', 'b'*64))

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
