"""Synthetic backup failures; no AWS account, secret or live service required."""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import sqlite3
import unittest
from unittest.mock import patch
from unittest.mock import Mock

import relay_backup as backup


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
                             make_archive=kwargs.get('encrypt', self.encrypt))

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
        inventory = self.root/'inventory'; backup.write_json(inventory, {'file': backup.sha256(app/'file')})
        config = dict(app=str(app), binary=str(binary), binary_sha256=backup.sha256(binary),
                      runtime_inventory=str(inventory), max_bytes=1000,
                      state=str(self.root/'missing'), service='synthetic.service', service_unit=str(binary))
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
        snapshot = '{"synthetic":true}'
        archive = b'SYNTHETIC ARCHIVE'
        pin = dict(revision=1, directory_key='casita.directory.v1:'+'A'*43,
                   snapshot_sha256=backup.hashlib.sha256(snapshot.encode()).hexdigest(),
                   archive_sha256=backup.hashlib.sha256(archive).hexdigest())
        (state/'snapshots'/(pin['archive_sha256']+'.casitar')).write_bytes(archive)
        with sqlite3.connect(state/'relay.sqlite') as db:
            db.executescript('CREATE TABLE revisions(revision INTEGER, snapshot TEXT, pin TEXT);'
                             'CREATE TABLE events(cursor INTEGER); CREATE TABLE members(name TEXT,enabled INTEGER);')
            db.execute('INSERT INTO revisions VALUES (?,?,?)', (1, snapshot, json.dumps(pin)))
        binary = self.root/'binary'; binary.write_bytes(b'SYNTHETIC BINARY')
        unit = self.root/'service'; unit.write_text('SYNTHETIC UNIT')
        index = self.root/'inventory'; backup.write_json(index, {'file': backup.sha256(app/'file')})
        config = dict(app=str(app), state=str(state), binary=str(binary),
                      binary_sha256=backup.sha256(binary), runtime_inventory=str(index),
                      service_unit=str(unit), service='synthetic.service', max_bytes=100_000,
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
