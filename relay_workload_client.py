#!/usr/bin/env python3
"""Pinned SSH/stdin worker. Only freshly generated synthetic credentials are accepted."""
import json
import os
from pathlib import Path
import platform
import signal
import socket
import sys
import threading
import time
from urllib.error import HTTPError

import context_relay as relay
from relay_load import fixture, require
from relay_workload_plan import source_pins, publish_logical, advance_cursor, catch_up_events

PRINT_LOCK = threading.Lock()
MAX_LINE = 1_000_000


def emit(value):
    with PRINT_LOCK:
        print(json.dumps(value, allow_nan=False, separators=(',', ':')), flush=True)


def read_message():
    raw = sys.stdin.readline(MAX_LINE + 1)
    require(raw.endswith('\n') and len(raw) <= MAX_LINE, 'controller disconnected or message too large')
    value = json.loads(raw)
    require(type(value) is dict, 'invalid worker protocol')
    return value


def execute(config, epoch, expected_runtime):
    output = Path(config['output']).resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    credential = output / 'ephemeral-credential.json'
    require(config['credential']['workspace'].startswith('synthetic-jpcj-'), 'synthetic workspace required')
    credential.write_bytes(relay.canonical(config['credential']))
    credential.chmod(0o600)
    # SSH may not give its child a session of its own. Own the process group before
    # any native child starts so EOF/deadline cancellation cannot orphan restores.
    if os.getpgrp() != os.getpid():
        os.setsid()
    require(os.getpgrp() == os.getpid(), 'worker must own its process group')
    finished, cancelled = threading.Event(), threading.Event()
    locks = {name: threading.Lock() for name in ('publish', 'consult', 'updates', 'reconnect')}
    threads, failures, cursor = [], [], config['cursor']
    active, active_lock = set(), threading.Lock()

    def abort(reason):
        if not finished.is_set():
            credential.unlink(missing_ok=True)
            emit(dict(kind='failure', error_type=reason))
            os.killpg(os.getpgrp(), signal.SIGKILL)

    def watch_input():
        # Parent closes stdin on cancellation, including an interrupted SSH link.
        try:
            for line in sys.stdin:
                message = json.loads(line)
                if message.get('operation') == 'cancel':
                    break
                if message.get('operation') == 'probe':
                    with active_lock:
                        observed = sorted(active)
                    emit(dict(kind='probe', probe_id=message['probe_id'], active=observed))
            abort('ControllerDisconnected')
        except BaseException:
            abort('ControllerDisconnected')
    watcher = threading.Thread(target=watch_input, daemon=True)
    watcher.start()

    def operation(job):
        nonlocal cursor
        scheduled = epoch + job['at']
        started = time.monotonic()
        if job['operation'] in ('publish', 'consult'):
            with active_lock:
                active.add(job['logical_id'])
        hard = {'publish': 15, 'consult': 30, 'updates': 10, 'reconnect': 30}[job['operation']]
        timer = threading.Timer(max(.001, scheduled + hard - started), lambda: abort('OperationDeadline'))
        timer.daemon = True
        timer.start()
        wire = []
        def request(operation, body):
            before = time.monotonic()
            status = 200
            try:
                return relay.client(config['url'], credential, operation, body,
                                    relay_port=config['relay_port'])
            except HTTPError as error:
                status = error.code
                raise
            finally:
                wire.append(dict(operation=operation, status=status,
                                 milliseconds=1000 * (time.monotonic() - before)))
        try:
            if job['operation'] == 'publish':
                result = publish_logical(job, request)
            elif job['operation'] in ('consult', 'reconnect'):
                response = request('context', {})
                if job['operation'] == 'reconnect':
                    require(response['pin'] == job['target_pin'], 'reconnect restored a different target pin')
                native_start = time.monotonic()
                snapshot = relay.receive(response, config['binary'], output / job['logical_id'])
                selected = relay.task_start(snapshot, job['task'], response['pin'])
                if job['operation'] == 'consult':
                    require(selected['checkpoint'] == fixture(job['task'], job['version'], 4096),
                            'consulted checkpoint differs from acknowledged logical change')
                else:
                    require(relay.digest(relay.canonical(selected['checkpoint'])) == job['checkpoint_sha256'],
                            'reconnect changed the selected checkpoint')
                scopes = sorted(entry['scope'] for entry in selected['accepted_memory'])
                require(scopes == (['task', 'workspace'] if job['task'] == 'task-alpha-0' else ['workspace']),
                        'consultation task memory filtering mismatch')
                result = dict(pin=response['pin'], native_ms=1000 * (time.monotonic() - native_start),
                              checkpoint_verified=True, scoped_memory_verified=True)
                if job['operation'] == 'reconnect':
                    caught = catch_up_events(request, cursor, job['missed_events'])
                    cursor = caught['cursor']
                    result.update(caught)
            else:
                page = request('updates', dict(after=cursor))
                before = cursor
                cursor = advance_cursor(page, cursor)
                result = dict(cursor=cursor, events=page['events'], previous_cursor=before)
            with active_lock:
                end = time.monotonic()
                active.discard(job['logical_id'])
            require(end - scheduled <= hard, 'operation deadline exceeded')
            emit(dict(kind='observation', job=job, start=started, end=end, scheduled=scheduled,
                      milliseconds=1000 * (end - scheduled), dispatch_lag_ms=1000 * (started - scheduled),
                      wire=wire, result=result))
        except BaseException as error:
            failures.append(type(error).__name__)
            cancelled.set()
            emit(dict(kind='failure', logical_id=job['logical_id'], error_type=type(error).__name__))
        finally:
            with active_lock:
                active.discard(job['logical_id'])
            timer.cancel()
            locks[job['operation']].release()
    try:
        require(time.monotonic() < epoch, 'worker missed the agreed start')
        for job in config['jobs']:
            if cancelled.wait(max(0, epoch + job['at'] - time.monotonic())):
                break
            lock = locks[job['operation']]
            if not lock.acquire(blocking=False):
                failures.append('MissedArrival')
                emit(dict(kind='failure', logical_id=job['logical_id'], error_type='MissedArrival'))
                cancelled.set()
                break
            thread = threading.Thread(target=operation, args=(job,), daemon=True)
            threads.append(thread)
            thread.start()
        if not cancelled.is_set():
            cancelled.wait(max(0, epoch + config['duration'] - time.monotonic()))
        for thread in threads:
            thread.join(timeout=31)
        require(not failures and not any(thread.is_alive() for thread in threads), 'worker incomplete')
        runtime = dict(source_sha256=source_pins(),
                       binary_sha256=relay.digest(Path(config['binary']).read_bytes()))
        require(runtime == expected_runtime, 'worker source or executable changed during the workload')
        credential.unlink()
        finished.set()
        emit(dict(kind='done', cursor=cursor, credential_removed=True,
                  owned_native_operations_finished=True, **runtime))
    finally:
        credential.unlink(missing_ok=True)
        if any(thread.is_alive() for thread in threads):
            abort('WorkerCleanupIncomplete')
        finished.set()


def main():
    os.umask(0o077)
    greeting = read_message()
    require(greeting['operation'] == 'hello', 'worker handshake required')
    binary = Path(greeting['binary']).resolve()
    require(binary.is_file() and os.access(binary, os.X_OK), 'native executable unavailable')
    runtime = dict(source_sha256=source_pins(), binary_sha256=relay.digest(binary.read_bytes()))
    emit(dict(kind='hello', **runtime,
              system=platform.system(), machine=platform.machine(), hostname=socket.gethostname()))
    while True:
        message = read_message()
        if message['operation'] == 'ping':
            emit(dict(kind='pong', monotonic=time.monotonic()))
        elif message['operation'] == 'configure':
            config = message['config']
            require(config['binary'] == str(binary), 'configured executable changed')
            emit(dict(kind='ready'))
        elif message['operation'] == 'start':
            execute(config, message['epoch'], runtime)
            return
        else:
            raise ValueError('unexpected worker command')


if __name__ == '__main__':
    try:
        main()
    except BaseException as error:
        emit(dict(kind='failure', error_type=type(error).__name__))
        sys.exit(1)
