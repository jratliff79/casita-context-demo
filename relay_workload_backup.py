"""Owned synthetic service boundary and local age capture/restore overlap.

No system service or cloud client is invoked. The real backup capture, history
verification and encryption code runs through a synthetic stop/start adapter.
"""
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time

import context_relay as relay
import relay_backup as backup
from relay_load import require
from relay_workload_plan import HERE, SOURCE_FILES
from relay_workload_diagnostics import error_type

LIMIT = 512 * 1024**2
PAYLOAD_BUDGET = 384 * 1024**2


class BackupEvidence:
    """Always-on bounded scalar evidence, including unsuccessful overlap checks."""
    STAGES = frozenset(('prepare', 'ready', 'capture', 'decrypt', 'verify', 'end'))
    INTEGERS = ('revision', 'event_cursor', 'compressed_payload_bytes', 'ciphertext_bytes')
    FLAGS = ('full_inventory_verified', 'native_history_verified', 'authenticated_members_restored',
             'real_age_encryption', 'local_sink_only', 'systemd_exercised')

    def __init__(self, diagnostics):
        self.diagnostics, self.lock = diagnostics, threading.RLock()
        self.invalid = False
        self.value = dict(schema='casita-backup-overlap.v1', state='not_started', stages={},
                          backup=None, probes=[], evaluation=None, error_type=None)
        self._write()

    @staticmethod
    def timestamp(value):
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                'invalid backup evidence timestamp')
        return value

    def _write(self):
        try:
            self.diagnostics._atomic('backup-overlap.json', self.value, 65536)
        except BaseException:
            self.invalid = True
            raise

    def event(self, stage, at):
        with self.lock:
            require(stage in self.STAGES and stage not in self.value['stages'], 'invalid backup stage')
            self.value['stages'][stage] = self.timestamp(at)
            if self.value['state'] != 'failed':
                self.value['state'] = 'running'
            self._write()

    def sent(self, operator, identifier, at):
        with self.lock:
            require(type(operator) is int and operator in (1, 2) and type(identifier) is int
                    and 0 < identifier <= 128 and len(self.value['probes']) < 128
                    and not any(row['id'] == identifier for row in self.value['probes']),
                    'backup probe evidence limit or identity changed')
            self.value['probes'].append(dict(operator=operator, id=identifier,
                sent=self.timestamp(at), received=None, active_jobs=[]))
            self._write()

    def received(self, operator, identifier, at, active_jobs):
        with self.lock:
            row = next(row for row in self.value['probes'] if row['id'] == identifier)
            require(row['operator'] == operator and row['received'] is None
                    and type(active_jobs) is list and len(active_jobs) <= 2
                    and len(set(active_jobs)) == len(active_jobs)
                    and all(type(job) is int and 0 <= job < 4096 for job in active_jobs),
                    'invalid backup probe evidence')
            row.update(received=self.timestamp(at), active_jobs=active_jobs)
            require(row['received'] >= row['sent'], 'backup probe clock order changed')
            self._write()

    def result(self, result):
        with self.lock:
            safe = {key: result[key] for key in ('start', 'end', *self.INTEGERS, *self.FLAGS,
                                               'ciphertext_sha256')}
            for key in ('start', 'end'):
                self.timestamp(safe[key])
            require(safe['start'] < safe['end'] and all(type(safe[key]) is int and safe[key] >= 0
                    for key in self.INTEGERS) and all(type(safe[key]) is bool for key in self.FLAGS)
                    and type(safe['ciphertext_sha256']) is str and len(safe['ciphertext_sha256']) == 64
                    and all(c in '0123456789abcdef' for c in safe['ciphertext_sha256']),
                    'invalid backup result evidence')
            self.value['backup'] = safe
            if self.value['state'] != 'failed':
                self.value['state'] = 'backup_complete'
            self._write()

    def evaluate(self, epoch, duration, overlap):
        with self.lock:
            result = self.value['backup']
            passed = (not self.invalid and self.value['state'] == 'backup_complete'
                      and result is not None and type(overlap) is int and overlap > 0
                      and epoch <= result['start'] < result['end'] <= epoch + duration)
            self.value['evaluation'] = dict(window_start=epoch, window_end=epoch + duration,
                definitely_overlapping_operations=overlap, passed=passed)
            self.value['state'] = 'verified' if passed else 'failed'
            self._write()
            require(passed, 'backup did not definitely overlap timed client work')

    def failed(self, error):
        with self.lock:
            self.value['state'] = 'failed'
            if self.value['error_type'] is None:
                self.value['error_type'] = error_type(error)
            self._write()


def wait_for_capture(start_at, on_start, observe):
    """Preparation and scheduled waiting are outside the actual backup interval."""
    ready = time.monotonic()
    if observe:
        observe('ready', ready)
    if start_at is not None:
        require(type(start_at) in (int, float) and math.isfinite(start_at), 'invalid backup start time')
        while True:
            remaining = start_at - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(.05, remaining))
    started = time.monotonic()
    if observe:
        observe('capture', started)
    if on_start:
        on_start(started)
    return started


class OwnedServer:
    def __init__(self, service):
        self.http = relay.serve(service, 0)
        self.thread = None
        self.pauses = []
        self.paused_at = None

    @property
    def port(self):
        return self.http.server_port

    def start(self):
        require(self.thread is None or not self.thread.is_alive(), 'owned server already running')
        self.thread = threading.Thread(target=lambda: self.http.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        if self.paused_at is not None:
            self.pauses.append(dict(start=self.paused_at, end=time.monotonic()))
            self.paused_at = None

    def pause(self):
        require(self.thread is not None and self.thread.is_alive(), 'owned server not running')
        self.paused_at = time.monotonic()
        self.http.shutdown()
        self.thread.join(timeout=5)
        require(not self.thread.is_alive(), 'owned HTTP server did not stop')

    def command(self, *args):
        if args[0] != '/usr/bin/systemctl':
            return backup.command(*args)
        require(args[-1] == 'synthetic-workload.service', 'only owned synthetic service may be controlled')
        if args[1] == 'stop':
            self.pause()
        elif args[1] == 'start':
            self.start()
        else:
            require(args[1:3] == ('is-active', '--quiet') and self.thread.is_alive(), 'owned service unavailable')
        return b''

    def close(self):
        if self.thread is not None and self.thread.is_alive():
            self.pause()
        self.http.server_close()


class LocalEncryptedSink:
    """Implements the backup upload interface using only the fresh private output."""
    def __init__(self, root):
        self.root, self.paths = Path(root), []
        self.root.mkdir()

    def upload(self, archive, key):
        target = self.root / key
        require(target.resolve().is_relative_to(self.root.resolve()), 'unsafe synthetic object key')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
        target.chmod(0o600)
        self.paths.append(target)

    def age(self, seconds):
        pass

    def alert(self, message):
        pass


def extract_encrypted(archive, identity, destination):
    """Stream age plaintext into bounded regular-file extraction; never execute it."""
    destination.mkdir(mode=0o700)
    total, count, compressed, names, modes = 0, 0, 0, set(), []
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(['/usr/bin/age', '-d', '-i', str(identity), str(archive)],
                                   stdout=subprocess.PIPE, stderr=errors)
        class Counted:
            def read(self, size):
                nonlocal compressed
                raw = process.stdout.read(size)
                compressed += len(raw)
                require(compressed <= PAYLOAD_BUDGET, 'compressed recovery payload exceeds budget')
                return raw
        stream = Counted()
        try:
            with tarfile.open(fileobj=stream, mode='r|gz') as tar:
                for member in tar:
                    count += 1
                    path = PurePosixPath(member.name)
                    require(count <= backup.MAX_ENTRIES and not path.is_absolute() and '..' not in path.parts
                            and (member.isdir() or member.isfile()) and path.as_posix() not in names,
                            'unsafe recovery archive member')
                    names.add(path.as_posix())
                    total += member.size
                    require(0 <= member.size and total <= LIMIT and not member.mode & 0o7000,
                            'recovery archive exceeds limits')
                    target = destination / path
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, target.open('xb') as out:
                            shutil.copyfileobj(source, out, 64 * 1024)
                    modes.append((target, member.mode, member.uid, member.gid))
            while stream.read(64 * 1024):
                pass
            require(process.wait(timeout=120) == 0, 'age decryption failed')
            for path, mode, uid, gid in reversed(modes):
                os.chown(path, uid, gid)
                path.chmod(mode)
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()
    return compressed


def capture_restore(root, state, binary, server, source_commit, casita_commit, on_start=None,
                    start_at=None, observe=None):
    identity = Path(root) / 'ephemeral-age-identity'
    try:
        if observe:
            observe('prepare', time.monotonic())
        return _capture_restore(root, state, binary, server, source_commit, casita_commit, identity,
                                on_start, start_at, observe)
    finally:
        identity.unlink(missing_ok=True)
        if observe:
            observe('end', time.monotonic())


def _capture_restore(root, state, binary, server, source_commit, casita_commit, identity, on_start,
                     start_at, observe):
    root = Path(root)
    root.mkdir(mode=0o700)
    app, spool = root / 'runtime', root / 'spool'
    app.mkdir(); spool.mkdir()
    for name in SOURCE_FILES:
        shutil.copyfile(HERE / name, app / name)
    unit = root / 'synthetic.service'
    unit.write_text('SYNTHETIC OWNED HTTP SERVICE; no installed system unit\n')
    index = root / 'runtime-inventory.json'
    backup.write_json(index, backup.inventory(app, LIMIT))
    subprocess.run(['/usr/bin/age-keygen', '-o', str(identity)], check=True, capture_output=True, timeout=10)
    recipient = subprocess.check_output(['/usr/bin/age-keygen', '-y', str(identity)], timeout=10).decode().strip()
    config = dict(state=str(state), app=str(app), spool=str(spool), binary=str(binary),
                  binary_pin=backup.file_pin(binary, LIMIT), service_unit=str(unit),
                  service_unit_pin=backup.file_pin(unit, LIMIT), runtime_inventory=str(index), max_bytes=LIMIT,
                  service='synthetic-workload.service', source_commit=source_commit, casita_commit=casita_commit,
                  recipient=recipient, port=server.port, health_credential=str(state / 'credentials/alpha.json'))
    sink, captured = LocalEncryptedSink(root / 'encrypted-objects'), {}
    started = wait_for_capture(start_at, on_start, observe)
    def capture(config, stage):
        manifest = backup.capture(config, stage, run=server.command)
        captured.update(manifest)
        return manifest
    try:
        result = backup.backup(config, sink, make_capture=capture)
        archive = sink.paths[0]
        require(backup.sha256(archive) == result['ciphertext_sha256'], 'encrypted artifact hash mismatch')
        with archive.open('rb') as stream:
            require(stream.read(22) == b'age-encryption.org/v1\n', 'not encrypted with age')
        restored = root / 'restored'
        if observe:
            observe('decrypt', time.monotonic())
        compressed = extract_encrypted(archive, identity, restored)
        if observe:
            observe('verify', time.monotonic())
        require(json.loads((restored / 'recovery-manifest.json').read_text()) == captured,
                'recovery manifest changed')
        actual = backup.inventory(restored, LIMIT)
        actual.pop('recovery-manifest.json')
        require(actual == captured['inventory'], 'full restored inventory mismatch')
        with relay.connect(restored / 'state', readonly=True) as db:
            require(db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'restored SQLite integrity failed')
            backup.verify_schema(db)
            backup.verify_events(db)
            backup.verify_transitions(db, backup.verify_auxiliary_json(db))
            backup.verify_snapshots(db, restored / 'state', binary)
        restored_service = relay.Relay(restored / 'state', binary)
        for name in ('alpha', 'beta'):
            credential = backup.private_json(restored / 'state/credentials' / (name + '.json'))
            response = restored_service.handle(credential['token'], 'context', dict(workspace=credential['workspace']))
            require(response['pin'] == captured['pin'], 'restored authenticated pin mismatch')
            relay.receive(response, binary, root / ('native-restore-' + name))
        return dict(start=started, end=time.monotonic(), pin=captured['pin'], revision=captured['revision'],
                    event_cursor=captured['event_cursor'], compressed_payload_bytes=compressed,
                    ciphertext_sha256=result['ciphertext_sha256'], ciphertext_bytes=result['ciphertext_bytes'],
                    full_inventory_verified=True, native_history_verified=True, authenticated_members_restored=True,
                    real_age_encryption=True, local_sink_only=True, systemd_exercised=False,
                    pauses=list(server.pauses))
    finally:
        identity.unlink(missing_ok=True)
