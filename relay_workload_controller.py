"""Own the synthetic server, paced workers and local encrypted recovery check."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import context_relay as relay
import relay_backup as backup
import relay_monitor as monitor
from relay_load import fixture, latency_summary, require, state_measurement, stop, tree_rss
from relay_workload_plan import (HERE, NAMES, make_plan, source_pins, seed_history,
                                 ssh_argv, clock_mapping, validate_hello, advance_cursor)
from relay_workload_backup import OwnedServer, capture_restore


class Peer:
    def __init__(self, argv, log):
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=log, text=True, bufsize=1, start_new_session=True)
        self.messages = queue.Queue(maxsize=8)
        def read():
            try:
                while True:
                    line = self.process.stdout.readline(1_000_001)
                    if not line:
                        break
                    require(line.endswith('\n') and len(line) <= 1_000_000, 'worker response exceeds limit')
                    value = json.loads(line)
                    require(type(value) is dict, 'invalid worker response')
                    self.messages.put(value)
            except BaseException:
                self.messages.put(dict(kind='failure', error_type='WorkerProtocol'))
            finally:
                self.messages.put(dict(kind='eof'))
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def send(self, value):
        self.process.stdin.write(json.dumps(value, allow_nan=False) + '\n')
        self.process.stdin.flush()

    def receive(self, expected, timeout=20):
        value = self.messages.get(timeout=timeout)
        require(value.get('kind') == expected, 'worker handshake failed')
        return value

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        stop(self.process)
        self.process.stdout.close()


def host_sample(state, spool):
    memory = {line.split(':')[0]: int(line.split()[1]) * 1024
              for line in Path('/proc/meminfo').read_text().splitlines()}
    vm = {key: int(value) for key, value in (line.split() for line in Path('/proc/vmstat').read_text().splitlines())}
    cpu = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    filesystems = {}
    for name, path in [('state', state), ('spool', spool)]:
        info = os.statvfs(path)
        filesystems[name] = dict(total_bytes=info.f_blocks * info.f_frsize,
            available_bytes=info.f_bavail * info.f_frsize, total_inodes=info.f_files, available_inodes=info.f_favail)
    return dict(monotonic=time.monotonic(), mem_available_bytes=memory['MemAvailable'],
                swap_total_bytes=memory['SwapTotal'], oom_kill=vm['oom_kill'],
                swap_in=vm['pswpin'], swap_out=vm['pswpout'], cpu_ticks=cpu,
                owned_controller_tree_rss_bytes=tree_rss(os.getpid()), filesystems=filesystems)


def causal_overlap(probes, start, end):
    """A challenge sent and answered inside the backup proves causal overlap.

    A worker snapshots active operations after receiving the challenge. The
    snapshot therefore happened between these controller-clock timestamps;
    clock drift on the client cannot turn non-overlap into evidence of overlap.
    """
    return len({logical for probe in probes if start <= probe['sent'] <= probe['received'] <= end
                for logical in probe['active']})


def device_observations(environments):
    names = [environments[name]['hostname'] for name in NAMES]
    return dict(remote_devices_tested=False, hostname_observations=dict(
        controller=socket.gethostname(), clients=dict(zip(NAMES, names)),
        distinct_client_names=len(set(names)) == len(names),
        all_client_names_differ_from_controller=all(name != socket.gethostname() for name in names)))


def source_revision():
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=HERE, stderr=subprocess.PIPE).decode().strip()
        changes = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'],
                                          cwd=HERE, stderr=subprocess.PIPE)
        return commit if not changes and re.fullmatch('[0-9a-f]{40}', commit) else '0' * 40
    except (OSError, subprocess.CalledProcessError):
        return '0' * 40  # Packaged/edited source is identified by its complete file hashes.


def validate_timing(row):
    require(all(type(row[key]) in (int, float) and math.isfinite(row[key]) and row[key] >= 0
                for key in ('start', 'end', 'scheduled', 'milliseconds', 'dispatch_lag_ms')),
            'invalid worker timings')
    require(row['scheduled'] <= row['start'] <= row['end'], 'worker timing order changed')
    require(math.isclose(row['milliseconds'], 1000 * (row['end'] - row['scheduled']), abs_tol=.001)
            and math.isclose(row['dispatch_lag_ms'], 1000 * (row['start'] - row['scheduled']), abs_tol=.001),
            'latency must include scheduled queue delay')
    deadline = {'publish': 15, 'consult': 30, 'updates': 10, 'reconnect': 30}[row['job']['operation']]
    require(row['end'] - row['scheduled'] <= deadline, 'operation hard deadline exceeded')


def history_windows(plan, rows):
    if not plan['full_profile']:
        return dict(evaluated=False, reason='short smoke profile has no full 15-minute windows')
    result = {}
    for name in NAMES:
        matching = [row for row in rows[name] if row['job']['phase'] == 'normal'
                    and row['job']['operation'] == 'publish']
        first = latency_summary([row for row in matching if row['job']['at'] < 900])
        last = latency_summary([row for row in matching if row['job']['at'] >= plan['normal_seconds'] - 900])
        require(first['count'] == last['count'] == 15, 'history windows lost scheduled arrivals')
        result[name] = dict(first=first, last=last,
            ratio_passed=last['p95_ms'] <= 2 * first['p95_ms'],
            absolute_budgets_passed=first['p95_ms'] <= 2000 and last['p95_ms'] <= 2000)
    return result


def close_resources(peers, server, logs):
    failures = []
    for close in ([peer.close for peer in peers.values()] +
                  ([server.close] if server is not None else []) + [log.close for log in logs]):
        try:
            close()
        except BaseException as error:
            failures.append(error)
    if failures:
        raise RuntimeError('owned workload cleanup failed') from failures[0]


def check_results(plan, rows, done, initial, state):
    total = plan['final_revisions'] - plan['initial_revisions']
    with relay.connect(state, readonly=True) as db:
        pins = {row['revision']: json.loads(row['pin']) for row in db.execute('select revision,pin from revisions')}
        events = {row['cursor']: json.loads(row['payload']) for row in db.execute('select cursor,payload from events')}
        counts = {table: db.execute('select count(*) from ' + table).fetchone()[0]
                  for table in ('revisions', 'events', 'requests')}
    require(counts == dict(revisions=plan['final_revisions'], events=initial['cursor'] + total,
                           requests=initial['cursor'] + total), 'final history accounting mismatch')
    committed, expected_tasks, summaries = [], dict(initial['tasks']), {}
    for name in NAMES:
        require(done[name]['credential_removed'] and done[name]['owned_native_operations_finished'],
                'worker cleanup was not acknowledged')
        require(done[name]['cursor'] == initial['cursor'] + total, 'worker did not reach final event cursor')
        require(len(rows[name]) == len(plan['clients'][name]), 'scheduled arrivals were lost')
        jobs = {job['logical_id']: job for job in plan['clients'][name]}
        require(len({row['job']['logical_id'] for row in rows[name]}) == len(jobs), 'duplicate worker observation')
        cursor = initial['cursor']
        for row in sorted(rows[name], key=lambda row: row['job']['at']):
            if row['job']['operation'] == 'updates':
                result = row['result']
                require(result['previous_cursor'] == cursor, 'scheduled update chain changed')
                cursor = advance_cursor(dict(events=result['events'], next_cursor=result['cursor']), cursor)
        final = done[name]['final_updates']
        validate_timing(final)
        require(final['start'] >= max(row['end'] for row in rows[name])
                and final['previous_cursor'] == cursor, 'final catch-up preceded drained work')
        require(advance_cursor(dict(events=final['events'], next_cursor=final['cursor']), cursor)
                == initial['cursor'] + total, 'final catch-up did not reach the acknowledged target')
        for event in final['events']:
            require(event == dict(events[event['cursor']], cursor=event['cursor']), 'final event payload changed')
        for row in rows[name]:
            job, result = row['job'], row['result']
            require(job == jobs[job['logical_id']], 'worker changed its scheduled job')
            validate_timing(row)
            if job['operation'] in ('publish', 'consult'):
                require(result['pin'] == pins[result['pin']['revision']], 'worker pin differs from committed history')
            if job['operation'] == 'publish':
                committed.append(result['pin']['revision'])
                require(result['cursor'] == result['pin']['revision'] + 2, 'publication cursor mismatch')
            elif job['operation'] == 'updates':
                for event in result['events']:
                    expected = dict(events[event['cursor']], cursor=event['cursor'])
                    require(event == expected, 'caught-up event differs from retained event')
            else:
                require(result['checkpoint_verified'] and result['scoped_memory_verified'], 'consultation unverified')
        for job in plan['clients'][name]:
            if job['operation'] == 'publish':
                expected_tasks[job['task']] = fixture(job['task'], job['version'], 4096)
        summaries[name] = {}
        for phase in ('normal', 'burst'):
            summaries[name][phase] = {}
            for operation in ('publish', 'consult', 'updates'):
                matching = [row for row in rows[name] if row['job']['phase'] == phase
                            and row['job']['operation'] == operation]
                summary = latency_summary(matching)
                budget = {'publish': (2000, 5000), 'consult': (5000, 10000), 'updates': (1000, 2000)}[operation][phase == 'burst']
                summaries[name][phase][operation] = dict(summary, p95_budget_ms=budget,
                                                         p95_budget_passed=summary['p95_ms'] <= budget)
            native = [dict(milliseconds=row['result']['native_ms']) for row in rows[name]
                      if row['job']['phase'] == phase and row['job']['operation'] == 'consult']
            summary = latency_summary(native)
            budget = 2000 if phase == 'normal' else 3000
            summaries[name][phase]['native_restore'] = dict(summary, p95_budget_ms=budget,
                                                            p95_budget_passed=summary['p95_ms'] <= budget)
    require(sorted(committed) == list(range(initial['revision'] + 1, plan['final_revisions'] + 1)),
            'logical changes were lost or duplicated')
    for at in sorted({job['at'] for job in plan['clients']['alpha']
                      if job['operation'] == 'publish' and job['phase'] == 'burst'}):
        pair = [row['result']['statuses'] for name in NAMES for row in rows[name]
                if row['job']['operation'] == 'publish' and row['job']['at'] == at]
        require(sorted(pair) == [[200], [409, 200]], 'contention pair must commit both logical changes with one retry')
    with relay.connect(state, readonly=True) as db:
        final = relay.Relay.current(db)
    require(final['tasks'] == expected_tasks, 'final task checkpoints differ from all acknowledged changes')
    return summaries


def run(binary, output, plan, clients=None, casita_commit=None):
    require(platform.system() == 'Linux', 'controller requires Linux host observations')
    require(all(Path(path).is_file() for path in ('/usr/bin/age', '/usr/bin/age-keygen')), 'install age and age-keygen first')
    binary, output = Path(binary).resolve(), Path(output).resolve()
    require(binary.is_file() and os.access(binary, os.X_OK), 'Casita executable unavailable')
    pins, binary_pin = source_pins(), relay.digest(binary.read_bytes())
    require(clients is None or set(clients) == set(NAMES), 'exactly alpha and beta client definitions required')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    owned_binary = output / 'trusted-casita'
    state = output / 'relay'
    peers, logs, mappings, environments, rows, done = {}, [], {}, {}, {n: [] for n in NAMES}, {}
    drained, finishing = set(), False
    server, job_thread, sample_thread = None, None, None
    sample_stop, job_done = threading.Event(), threading.Event()
    samples, sample_failures, monitor_rows, backup_result, backup_errors = [], [], [], {}, []
    backup_progress, probes, pending_probes = {}, [], {}
    next_probe, probe_id = 0, 0
    receipt = None
    try:
        shutil.copyfile(binary, owned_binary); owned_binary.chmod(0o700)
        require(relay.digest(owned_binary.read_bytes()) == binary_pin, 'copied native binary changed')
        relay.initialize(state, 'synthetic-jpcj-' + secrets.token_hex(8), 'alpha', ['beta'])
        relay.provision_readiness(state)
        service = relay.Relay(state, owned_binary)
        credential = backup.private_json(state / 'credentials/alpha.json')
        initial = seed_history(service, credential['token'], plan['initial_revisions'])
        # Re-verify every retained object and transition before an aged run starts.
        with relay.connect(state, readonly=True) as db:
            backup.verify_events(db)
            backup.verify_transitions(db, backup.verify_auxiliary_json(db))
            backup.verify_snapshots(db, state, owned_binary)
        before = state_measurement(state)
        server = OwnedServer(service); server.start()
        url = 'http://127.0.0.1:' + str(server.port)
        for name in NAMES:
            log = (output / (name + '-transport.stderr')).open('wb'); logs.append(log)
            local = dict(binary=str(owned_binary), output=str(output / name))
            client = local if clients is None else clients[name]
            argv = ([sys.executable, '-B', str(HERE / 'relay_workload_client.py')]
                    if clients is None else ssh_argv(client, server.port))
            peer = Peer(argv, log); peers[name] = peer
            peer.send(dict(operation='hello', binary=client['binary']))
            hello = peer.receive('hello')
            validate_hello(hello, pins, binary_pin if clients is None else client['binary_sha256'])
            environments[name] = hello
            clocks = []
            for _ in range(5):
                sent = time.monotonic(); peer.send(dict(operation='ping'))
                pong = peer.receive('pong'); received = time.monotonic()
                clocks.append((sent, pong['monotonic'], received))
            mappings[name] = dict(clock_mapping(clocks), samples=clocks)
            worker_credential = backup.private_json(state / 'credentials' / (name + '.json'))
            config = dict(binary=client['binary'], output=client['output'], credential=worker_credential,
                          cursor=initial['cursor'], jobs=plan['clients'][name], duration=plan['duration'],
                          final_cursor=initial['cursor'] + plan['final_revisions'] - plan['initial_revisions'],
                          relay_port=server.port, url=url if clients is None else
                          'http://127.0.0.1:' + str(client['forward_port']))
            peer.send(dict(operation='configure', config=config)); peer.receive('ready')
        epoch = time.monotonic() + 5
        for name in NAMES:
            peers[name].send(dict(operation='start', epoch=epoch + mappings[name]['offset']))
        spool = output / 'sample-spool'; spool.mkdir()
        def sample():
            try:
                while not sample_stop.is_set():
                    samples.append(host_sample(state, spool))
                    sample_stop.wait(1)
            except BaseException as error:
                sample_failures.append(type(error).__name__)
        sample_thread = threading.Thread(target=sample, daemon=True); sample_thread.start()
        def background_backup():
            try:
                source_commit = source_revision()
                backup_result.update(capture_restore(output / 'overlap-backup', state, owned_binary,
                    server, source_commit, casita_commit or '0' * 40,
                    on_start=lambda value: backup_progress.update(start=value)))
            except BaseException as error:
                backup_errors.append(type(error).__name__)
            finally:
                job_done.set()
        next_monitor = epoch
        monitor_config = dict(state=str(state), spool=str(spool), credential=str(state / 'monitoring/readiness.json'),
                              port=server.port, backup_max_bytes=512 * 1024**2, free_percent=15,
                              inodes_free_percent=10, region='synthetic', namespace='Synthetic/Workload', relay_name='synthetic')
        while len(done) < 2 or not job_done.is_set() or time.monotonic() < epoch + plan['duration']:
            now = time.monotonic()
            require(now < epoch + plan['duration'] + 60, 'workload drain or backup deadline exceeded')
            require(not sample_failures and not backup_errors, 'background verification failed')
            if job_thread is None and now >= epoch + plan['normal_seconds'] / 2:
                job_thread = threading.Thread(target=background_backup, daemon=True); job_thread.start()
            if now >= next_monitor and now < epoch + plan['duration']:
                monitor_rows.append(dict(monotonic=time.monotonic(), metrics=monitor.sample(monitor_config)))
                next_monitor += 300
            if (backup_progress and not job_done.is_set() and now >= next_probe
                    and not any(probe['active'] for probe in probes)):
                for name, peer in peers.items():
                    if name not in done and not any(key[0] == name for key in pending_probes):
                        probe_id += 1
                        pending_probes[(name, probe_id)] = time.monotonic()
                        peer.send(dict(operation='probe', probe_id=probe_id))
                next_probe = now + .25
            for name, peer in peers.items():
                try:
                    message = peer.messages.get_nowait()
                except queue.Empty:
                    continue
                kind = message.get('kind')
                if kind == 'observation':
                    require(name not in done, 'worker emitted observations after cleanup')
                    rows[name].append(message)
                    with (output / (name + '-observations.jsonl')).open('a') as stream:
                        stream.write(json.dumps(message, allow_nan=False) + '\n')
                elif kind == 'done':
                    require(name in drained and name not in done, 'worker completed before drain or repeated completion')
                    done[name] = message
                    peer.process.stdin.close()
                elif kind == 'drained':
                    require(name not in drained and len(rows[name]) == len(plan['clients'][name]),
                            'worker drained before all observations or repeated drain')
                    drained.add(name)
                elif kind == 'probe':
                    sent = pending_probes.pop((name, message['probe_id']))
                    active = message['active']
                    valid = {job['logical_id'] for job in plan['clients'][name]
                             if job['operation'] in ('publish', 'consult')}
                    require(type(active) is list and set(active) <= valid, 'invalid active operation probe')
                    probes.append(dict(name=name, sent=sent, received=time.monotonic(), active=active))
                elif kind == 'eof' and name in done:
                    pass
                else:
                    raise ValueError('worker failed or disconnected; see private partial observations')
            if len(drained) == 2 and not finishing:
                for peer in peers.values():
                    peer.send(dict(operation='finish', cursor=initial['cursor'] +
                        plan['final_revisions'] - plan['initial_revisions']))
                finishing = True
            time.sleep(.01)
        require(not sample_failures and not backup_errors, 'background verification failed')
        for peer in peers.values():
            require(peer.process.wait(timeout=5) == 0, 'worker exited unsuccessfully')
        for name in NAMES:
            require(all(done[name][key] == environments[name][key]
                        for key in ('source_sha256', 'binary_sha256')), 'worker completion pins changed')
        summaries = check_results(plan, rows, done, initial, state)
        overlap = causal_overlap(probes, backup_result['start'], backup_result['end'])
        require(overlap > 0 and epoch <= backup_result['start'] < backup_result['end'] <= epoch + plan['duration'],
                'backup did not definitely overlap timed client work')
        sample_stop.set(); sample_thread.join(timeout=5)
        require(not sample_thread.is_alive() and not sample_failures, 'host sampling incomplete')
        after = state_measurement(state)
        from relay_workload_reconnect import check as check_reconnect
        reconnect = check_reconnect(state, owned_binary, server, output / 'reconnect-case',
            None if clients is None else clients['beta'], 100 if plan['full_profile'] else 3)
        require(source_pins() == pins and relay.digest(owned_binary.read_bytes()) == binary_pin
                and relay.digest(binary.read_bytes()) == binary_pin, 'controller source or executable changed')
        receipt = dict(schema='casita-relay-paced-workload.v1', ok=True, synthetic=True,
            recorded_at=datetime.now(timezone.utc).isoformat(), plan=plan, source_sha256=pins,
            casita_binary_sha256=binary_pin, casita_source_commit_claim=casita_commit,
            client_environments=environments, clock_calibration=mappings, start_monotonic=epoch,
            transport='ssh-reverse-loopback' if clients is not None else 'local-subprocess-loopback',
            **device_observations(environments),
            client_final_catch_up={name: done[name]['final_updates'] for name in NAMES},
            client_summaries=summaries, history_windows=history_windows(plan, rows),
            storage_before=before, storage_after=after,
            host_samples=samples, monitor_observations=monitor_rows,
            reconnect_case=reconnect,
            backup=dict(backup_result, definitely_overlapping_operations=overlap, causal_probes=probes),
            verification=dict(all_arrivals_accounted=True, logical_changes_exact=True,
                scoped_native_restores=True, retained_history_verified=True, encrypted_restore_verified=True,
                completion_source_and_binary_pins_verified=True),
            capacity_qualified=False, provider_cpu_buckets_verified=False, depleted_credit_state_verified=False,
            limits=['One run, not the three-fresh/three-aged matrix',
                    'Synthetic service adapter, not installed systemd timer execution',
                    'Local encrypted sink; no off-host durability or cloud integration claim',
                    'Client clock calibration is an observation, not host attestation',
                    'Actual JP/CJ device and sustained depleted-credit qualification remain external gates'])
        return receipt
    except BaseException as error:
        (output / 'failure.json').write_bytes(relay.canonical(dict(ok=False, synthetic=True, error_type=type(error).__name__)))
        raise
    finally:
        sample_stop.set()
        cleanup_errors = []
        try:
            close_resources(peers, None, [])
        except BaseException as error:
            cleanup_errors.append(error)
        if job_thread is not None and job_thread.is_alive():
            job_thread.join(timeout=5)
            if job_thread.is_alive():
                # Only main() may run the controller; it owns this process group.
                # Persist failure before terminating a hung backup's native children.
                (output / 'failure.json').write_bytes(relay.canonical(dict(ok=False, error_type='BackupCleanupTimeout')))
                for key in output.glob('overlap-backup/ephemeral-age-identity'):
                    key.unlink(missing_ok=True)
                os.killpg(os.getpgrp(), signal.SIGKILL)
        if sample_thread is not None:
            sample_thread.join(timeout=5)
        try:
            close_resources({}, server, logs)
        except BaseException as error:
            cleanup_errors.append(error)
        if cleanup_errors:
            (output / 'failure.json').write_bytes(relay.canonical(dict(ok=False, error_type='CleanupFailure')))
            raise RuntimeError('workload cleanup failed') from cleanup_errors[0]
        if receipt is not None:
            receipt['verification']['owned_resource_cleanup'] = True
            pending = output / 'receipt-pending.json'
            pending.write_bytes(relay.canonical(receipt)); pending.replace(output / 'receipt.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--casita', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument('--local-clients', action='store_true')
    choices.add_argument('--clients', type=Path, help='private JSON with alpha/beta SSH worker definitions')
    parser.add_argument('--smoke', action='store_true', help='short code exercise; never the full workload profile')
    parser.add_argument('--initial-revisions', type=int, default=6)
    parser.add_argument('--casita-commit', help='operator-supplied source claim; the harness verifies executable bytes')
    args = parser.parse_args()
    if args.casita_commit:
        require(re.fullmatch('[0-9a-f]{40}', args.casita_commit) is not None, 'invalid Casita source claim')
    if os.getpgrp() != os.getpid():
        os.setsid()
    require(os.getpgrp() == os.getpid(), 'controller must own its process group')
    os.umask(0o077)
    clients = backup.private_json(args.clients) if args.clients else None
    plan = (make_plan(30, 20, 10, args.initial_revisions) if args.smoke
            else make_plan(initial_revisions=args.initial_revisions))
    receipt = run(args.casita, args.output, plan, clients, args.casita_commit)
    print('Synthetic paced workload completed; capacity_qualified=' + str(receipt['capacity_qualified']))
