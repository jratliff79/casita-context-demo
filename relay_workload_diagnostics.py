"""Private, bounded numeric workload diagnostics. Never serialize payloads/argv."""
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time

MAX_RECORDS = 60_000
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 1024
MAX_TAGS = 4096
MAX_STATUS_WRITES = 2048
STAGES = frozenset('''
http.accepted http.read_decode http.handle http.response_write
server.handle server.authenticate server.snapshot server.snapshot_persist server.commit
native.init native.import native.root_ls native.archive_create native.archive_verify
native.fsck native.checkout native.other
prepare.preflight prepare.config prepare.initialize prepare.service prepare.seed_history
prepare.verify_events prepare.verify_transitions prepare.verify_snapshots prepare.storage
prepare.worker_alpha prepare.worker_beta prepare.ready workload.execute workload.sample
workload.backup workload.verify workload.reconnect cleanup
supervisor.host_sample supervisor.inventory supervisor.process supervisor.receipt
supervisor.authorization supervisor.cutoff
'''.split())
OPERATIONS = frozenset(('publish', 'propose', 'accept', 'context', 'updates', 'readiness'))
HERE = Path(__file__).resolve().parent
TRUSTED_FRAMES = {str(HERE / name): name for name in (
    'context_relay.py', 'demo.py', 'relay_monitor.py', 'relay_backup.py',
    'relay_workload_controller.py', 'relay_workload_plan.py',
    'relay_workload_diagnostics.py', 'relay_workload_backup.py',
    'relay_workload_reconnect.py')}


def error_type(error):
    # Fixed categories, including for subclasses with attacker-controlled names.
    for kind in (FileNotFoundError, PermissionError, TimeoutError, subprocess.TimeoutExpired,
                 KeyboardInterrupt, SystemExit, ValueError, TypeError, RuntimeError, OSError):
        if isinstance(error, kind):
            return kind.__name__
    known = {('relay_monitor', 'MonitorError'), ('relay_backup', 'BackupError'),
             ('context_relay', 'Rejected')}
    kind = type(error)
    return kind.__name__ if (kind.__module__, kind.__name__) in known else 'OtherError'


def failure_info(error, phase):
    frames, scanned, tb = [], 0, error.__traceback__
    while tb is not None and scanned < 256:
        code = tb.tb_frame.f_code
        filename = TRUSTED_FRAMES.get(code.co_filename)
        frames.append(dict(file=filename or 'external',
                           function=code.co_name[:64] if filename else 'external',
                           line=tb.tb_lineno if filename else 0))
        frames = frames[-12:]
        scanned += 1
        tb = tb.tb_next
    return dict(phase=phase, error_type=error_type(error), frames=frames,
                frames_truncated=scanned > 12 or tb is not None)


def read_status(path):
    """Accept only the bounded scalar status, never an arbitrary JSON payload."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
            raise ValueError('invalid diagnostic status file')
        raw = stream.read(1025)
    if len(raw) > 1024:
        raise ValueError('diagnostic status exceeds bound')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {
            'stage', 'state', 'monotonic_ns', 'acknowledged_revisions'} or
            value['stage'] not in STAGES or value['state'] not in ('running', 'complete', 'failed') or
            type(value['monotonic_ns']) is not int or value['monotonic_ns'] < 0 or
            type(value['acknowledged_revisions']) is not int or
            not 0 <= value['acknowledged_revisions'] <= 1000):
        raise ValueError('invalid diagnostic status')
    return value


class Diagnostics:
    def __init__(self, root, enabled=True, max_records=MAX_RECORDS, max_bytes=MAX_BYTES):
        if type(enabled) is not bool:
            raise ValueError('invalid diagnostic enable flag')
        if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
            raise ValueError('invalid diagnostic record limit')
        if type(max_bytes) is not int or not MAX_RECORD_BYTES <= max_bytes <= MAX_BYTES:
            raise ValueError('invalid diagnostic byte limit')
        self.root = Path(root)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o777 != 0o700:
            raise ValueError('diagnostics require a private owned directory')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        lock = os.open(self.root / 'diagnostics.lock', flags, 0o600)
        os.close(lock)
        self.fd = os.open(self.root / 'stage-timing.jsonl', flags, 0o600) if enabled else None
        self.enabled, self.max_records, self.max_bytes = enabled, max_records, max_bytes
        self.lock, self.local = threading.RLock(), threading.local()
        self.serial = self.records = self.dropped = self.bytes = self.overhead_ns = 0
        self.io_failed, self.closed = False, False
        self.tags = Counter()
        self.current_phase, self.failed_phase = 'prepare.preflight', None
        self.last_phase = self.current_phase
        self.revisions, self.status_writes = 0, 0
        self.primary = None

    def _atomic(self, name, value, limit):
        raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        if len(raw) > limit:
            raise ValueError('diagnostic artifact exceeds bound')
        pending = self.root / (name + '.pending')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
            os.replace(pending, self.root / name)
        finally:
            pending.unlink(missing_ok=True)

    def _status(self, stage, state):
        with self.lock:
            if self.status_writes >= MAX_STATUS_WRITES:
                self.io_failed = True
                return
            self.status_writes += 1
            try:
                self._atomic('preparation-status.json', dict(stage=stage, state=state,
                    monotonic_ns=time.monotonic_ns(), acknowledged_revisions=self.revisions), 1024)
            except OSError:
                self.io_failed = True

    def _id(self):
        with self.lock:
            self.serial += 1
            return self.serial

    @contextmanager
    def request(self):
        previous = getattr(self.local, 'request', None)
        self.local.request = dict(request_id=self._id(), trace_tag=None, operation='unknown')
        try:
            yield
        finally:
            self.local.request = previous

    def bind_request(self, operation, tag):
        request = getattr(self.local, 'request', None)
        if request is not None:
            request['operation'] = operation if operation in OPERATIONS else 'unknown'
            request['trace_tag'] = (int(tag) if isinstance(tag, str)
                and re.fullmatch(r'[0-9]{1,10}', tag) and 0 < int(tag) < 2**31 else None)

    @contextmanager
    def span(self, stage):
        if stage not in STAGES:
            raise ValueError('unknown diagnostic stage')
        if not self.enabled:
            yield
            return
        stack = getattr(self.local, 'stack', [])
        self.local.stack = stack
        if len(stack) >= 32:
            with self.lock:
                self.dropped += 1
            yield
            return
        identity, parent = self._id(), stack[-1] if stack else None
        request = getattr(self.local, 'request', None)
        started = time.monotonic_ns()
        stack.append(identity)
        outcome, category = 'ok', None
        try:
            yield
        except BaseException as error:
            outcome, category = 'error', error_type(error)
            raise
        finally:
            elapsed = time.monotonic_ns() - started
            stack.pop()
            row = dict(id=identity, parent_id=parent, stage=stage, start_monotonic_ns=started,
                       elapsed_ns=elapsed, outcome=outcome, error_type=category,
                       request_id=request['request_id'] if request else None,
                       trace_tag=request['trace_tag'] if request else None,
                       operation=request['operation'] if request else 'unknown')
            self._emit(row)

    def _emit(self, row):
        started = time.monotonic_ns()
        with self.lock:
            raw = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
            if (self.closed or self.io_failed or self.records >= self.max_records
                    or len(raw) > MAX_RECORD_BYTES or self.bytes + len(raw) > self.max_bytes):
                self.dropped += 1
            else:
                try:
                    written = os.write(self.fd, raw)
                    self.bytes += written
                    if written != len(raw):
                        raise OSError('short diagnostic write')
                    self.records += 1
                    tag = row['trace_tag']
                    if row['stage'] == 'http.accepted' and tag is not None:
                        if tag not in self.tags and len(self.tags) >= MAX_TAGS:
                            self.io_failed = True
                        else:
                            self.tags[tag] += 1
                except OSError:
                    self.io_failed = True
                    self.dropped += 1
            self.overhead_ns += time.monotonic_ns() - started

    @contextmanager
    def phase(self, stage):
        if stage not in STAGES:
            raise ValueError('unknown preparation stage')
        previous, self.current_phase = self.current_phase, stage
        self.last_phase = stage
        self._status(stage, 'running')
        try:
            with self.span(stage):
                yield
        except BaseException:
            self.failed_phase = self.failed_phase or stage
            self._status(stage, 'failed')
            raise
        else:
            self._status(stage, 'complete')
        finally:
            self.current_phase = previous

    def progress(self, revision):
        if type(revision) is not int or not self.revisions <= revision <= 1000:
            raise ValueError('invalid acknowledged revision')
        previous, self.revisions = self.revisions, revision
        if revision // 50 > previous // 50:
            self._status(self.current_phase, 'running')

    def failure(self, error, phase=None):
        phase = phase or self.failed_phase or self.last_phase
        if phase not in STAGES:
            phase = 'prepare.preflight'
        with self.lock:
            detail = failure_info(error, phase)
            if self.primary is None:
                self.primary = dict(ok=False, synthetic=True, **detail, secondary_failures=[])
            elif len(self.primary['secondary_failures']) < 2:
                self.primary['secondary_failures'].append(detail)
            try:
                self._atomic('failure.json', self.primary, 8192)
            except OSError:
                self.io_failed = True
            self._status(self.primary['phase'], 'failed')

    def native(self, command, *argv):
        offset = 3 if len(argv) > 3 and argv[1] == '--repository' else 1
        verb = argv[offset] if len(argv) > offset else None
        stages = {'init': 'native.init', 'import': 'native.import', 'fsck': 'native.fsck',
                  'checkout': 'native.checkout', 'root': 'native.root_ls'}
        stage = stages.get(verb, 'native.other')
        if verb == 'archive' and len(argv) > offset + 1:
            stage = {'verify': 'native.archive_verify', 'create': 'native.archive_create'}.get(
                argv[offset + 1], 'native.other')
        with self.span(stage):
            return command(*argv)

    def close(self):
        with self.lock:
            if not self.closed:
                if self.fd is not None:
                    try:
                        os.close(self.fd)
                    except OSError:
                        self.io_failed = True
                self.closed = True

    def finish(self, expected_tags=None):
        self.close()
        with self.lock:
            if expected_tags is not None and (type(expected_tags) not in (set, frozenset, list, tuple)
                    or len(expected_tags) > MAX_TAGS or
                    any(type(tag) is not int or not 0 < tag < 2**31 for tag in expected_tags)):
                raise ValueError('invalid expected request tags')
            expected = set(expected_tags) if expected_tags is not None else None
            if expected is not None and (len(expected) > MAX_TAGS or
                    any(type(tag) is not int or not 0 < tag < 2**31 for tag in expected)):
                raise ValueError('invalid expected request tags')
            missing = len(expected - self.tags.keys()) if expected is not None else 0
            unexpected = len(self.tags.keys() - expected) if expected is not None else 0
            duplicates = sum(count != 1 for count in self.tags.values())
            summary = dict(enabled=self.enabled, complete=not (self.io_failed or self.dropped
                or missing or unexpected or duplicates), records_written=self.records,
                records_dropped=self.dropped, bytes_written=self.bytes, max_records=self.max_records,
                max_bytes=self.max_bytes, write_overhead_ns=self.overhead_ns, io_failed=self.io_failed,
                missing_requests=missing, unexpected_requests=unexpected, duplicate_requests=duplicates)
            self._atomic('stage-timing-summary.json', summary, 2048)
            return summary
