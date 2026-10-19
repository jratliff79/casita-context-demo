#!/usr/bin/env python3
"""Native short-profile regression; optional SSH server exists only inside Docker."""
import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from context_relay import digest
from relay_load import require, stop


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--casita', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ssh-fixture', action='store_true', help='Docker-only, isolated loopback SSH regression')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False, mode=0o700)
    binary = args.casita.resolve()
    argv = [sys.executable, '-B', str(HERE / 'relay_workload.py'), '--casita', str(binary),
            '--output', str(root / 'workload'), '--smoke', '--initial-revisions', '12', '--stage-timing',
            '--casita-commit', '9d7a2f42b14d86189662e21241f72a55ccb42cb6']
    sshd, log, process = None, None, None
    keys = []
    try:
        if args.ssh_fixture:
            require(Path('/.dockerenv').is_file() and os.geteuid() == 0,
                    'SSH fixture is restricted to a disposable root Docker container')
            user = pwd.getpwuid(os.getuid()).pw_name
            hostkey, clientkey = root / 'host-key', root / 'client-key'
            keys = [hostkey, clientkey]
            for key in keys:
                subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                               check=True, capture_output=True, timeout=10)
            ssh_port = port()
            known = root / 'known-hosts'
            known.write_text('[127.0.0.1]:' + str(ssh_port) + ' ' +
                             ' '.join(hostkey.with_suffix('.pub').read_text().split()[:2]) + '\n')
            config = root / 'sshd-config'
            config.write_text('\n'.join([
                'Port ' + str(ssh_port), 'ListenAddress 127.0.0.1', 'HostKey ' + str(hostkey),
                'PidFile ' + str(root / 'sshd.pid'), 'AuthorizedKeysFile ' + str(clientkey.with_suffix('.pub')),
                'PasswordAuthentication no', 'KbdInteractiveAuthentication no', 'PermitRootLogin prohibit-password',
                'UsePAM yes', 'StrictModes yes', 'AllowTcpForwarding remote', 'GatewayPorts no', 'AllowUsers ' + user,
            ]) + '\n')
            Path('/run/sshd').mkdir(exist_ok=True)
            log = (root / 'sshd.stderr').open('wb')
            sshd = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(config)],
                                    stdout=log, stderr=log, start_new_session=True)
            deadline = time.monotonic() + 5
            while True:
                require(sshd.poll() is None and time.monotonic() < deadline, 'fixture SSH server failed')
                try:
                    with socket.create_connection(('127.0.0.1', ssh_port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.02)
            client_config = root / 'ssh-client-config'
            client_config.write_text('\n'.join([
                'Host synthetic-fixture', ' HostName 127.0.0.1', ' User ' + user,
                ' IdentityFile ' + str(clientkey), ' UserKnownHostsFile ' + str(known),
                ' IdentitiesOnly yes', ' StrictHostKeyChecking yes',
            ]) + '\n')
            clients = {name: dict(host='synthetic-fixture', port=ssh_port, checkout=str(HERE),
                python=sys.executable, ssh_config=str(client_config), forward_port=port(),
                binary=str(binary), binary_sha256=digest(binary.read_bytes()),
                output=str(root / ('remote-' + name))) for name in ('alpha', 'beta')}
            require(clients['alpha']['forward_port'] != clients['beta']['forward_port'], 'fixture port collision')
            path = root / 'clients.json'; path.write_text(json.dumps(clients)); path.chmod(0o600)
            argv += ['--clients', str(path)]
        else:
            argv += ['--local-clients']
        process = subprocess.Popen(argv, start_new_session=True)
        try:
            require(process.wait(timeout=180) == 0, 'native paced workload failed')
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGINT)
            raise
        data = json.loads((root / 'workload/receipt.json').read_text())
        diagnostic = data['diagnostics']
        require(diagnostic['enabled'] and diagnostic['complete'] and diagnostic['records_written'] > 0
                and diagnostic['records_dropped'] == 0 and not diagnostic['io_failed']
                and diagnostic['missing_requests'] == diagnostic['unexpected_requests'] == diagnostic['duplicate_requests'] == 0,
                'bounded stage diagnostics or request correlation incomplete')
        trace = [json.loads(line) for line in (root / 'workload/stage-timing.jsonl').read_text().splitlines()]
        require({'native.import', 'native.fsck', 'native.archive_create', 'native.checkout',
                 'http.accepted', 'http.response_write', 'prepare.seed_history', 'prepare.verify_snapshots'}
                <= {row['stage'] for row in trace}, 'server/native/preparation stages missing')
        require(data['ok'] and not data['capacity_qualified'] and not data['plan']['full_profile'], 'smoke scope changed')
        require(data['storage_before']['counts']['revisions'] == 12
                and data['storage_after']['counts']['revisions'] == 26, 'aged/paced revision conservation failed')
        require(data['backup']['definitely_overlapping_operations'] > 0 and data['backup']['real_age_encryption']
                and data['backup']['full_inventory_verified'] and data['backup']['native_history_verified']
                and data['backup']['authenticated_members_restored'], 'backup overlap or full recovery failed')
        require(data['reconnect_case']['missed_events'] == 3
                and data['reconnect_case']['original_timed_pin_unchanged']
                and data['reconnect_case']['exact_backlog_and_native_pin_verified'], 'separate reconnect failed')
        require(data['verification']['owned_resource_cleanup'], 'cleanup was not verified')
        require(all(row['cursor'] == 28 for row in data['client_final_catch_up'].values()),
                'final quiescent catch-up did not reach all acknowledged changes')
        require(not data['remote_devices_tested'], 'loopback SSH must not claim independent physical devices')
        conflicts = 0
        for name in ('alpha', 'beta'):
            rows = [json.loads(line) for line in (root / 'workload' / (name + '-observations.jsonl')).read_text().splitlines()]
            publishes = [row for row in rows if row['job']['operation'] == 'publish']
            require(len(publishes) == 7 and len([r for r in rows if r['job']['operation'] == 'consult']) == 7,
                    'logical arrivals were lost')
            conflicts += sum(row['result']['statuses'].count(409) for row in publishes)
        require(conflicts == 4, 'expected contention pairs did not make bounded successful retries')
        require(not list((root / 'workload').rglob('ephemeral-age-identity'))
                and not list(root.rglob('ephemeral-credential.json')), 'ephemeral recovery/worker credentials remain')
        (root / 'verification.json').write_text(json.dumps(dict(native_smoke_passed=True,
            ssh_protocol_exercised=args.ssh_fixture, actual_remote_devices=False,
            retained_revisions_before=12, retained_revisions_after=26,
            real_age_capture_restore=True, separate_reconnect_verified=True,
            capacity_qualified=False, aws_accessed=False), indent=2) + '\n')
        print('PASS: paced native aged-history, encrypted backup overlap and separate reconnect; SSH=' + str(args.ssh_fixture))
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGINT)
                process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                stop(process)
        if sshd is not None:
            stop(sshd)
        if log:
            log.close()
        for key in keys:
            key.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
