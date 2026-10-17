"""Separate missed-event recovery case on an owned copy, outside timed accounting."""
import json
from pathlib import Path
import subprocess
import sys
import time

import context_relay as relay
import relay_backup as backup
from relay_load import fixture, require
from relay_workload_plan import HERE, source_pins, ssh_argv, validate_hello, clock_mapping
from relay_workload_backup import OwnedServer


def check(original, binary, server, output, client=None, missed_events=100):
    from relay_workload_controller import Peer, validate_timing, close_resources
    require(type(missed_events) is int and 1 <= missed_events <= 100, 'invalid reconnect case')
    root, copied = Path(output), Path(output) / 'relay-copy'
    root.mkdir(mode=0o700)
    with relay.connect(original, readonly=True) as db:
        original_pin = json.loads(db.execute('select pin from revisions order by revision desc limit 1').fetchone()[0])
    server.pause()
    try:
        backup.copy_tree(original, copied, 512 * 1024**2)
    finally:
        server.start()
    service = relay.Relay(copied, binary)
    owner = backup.private_json(copied / 'credentials/alpha.json')
    member = backup.private_json(copied / 'credentials/beta.json')
    with relay.connect(copied, readonly=True) as db:
        old_cursor = db.execute('select max(cursor) from events').fetchone()[0]
    revision = original_pin['revision']
    for index in range(missed_events):
        result = service.handle(owner['token'], 'publish', dict(workspace=owner['workspace'],
            request_id=f'reconnect-prefix-{index}', expected_revision=revision,
            checkpoint=fixture('task-alpha-0', 'reconnect-' + str(index), 4096)))
        revision = result['pin']['revision']
    target = result['pin']
    with relay.connect(copied, readonly=True) as db:
        selected = relay.Relay.current(db)['tasks']['task-beta-0']
        expected_events = [dict(json.loads(row['payload']), cursor=row['cursor'])
                           for row in db.execute('select cursor,payload from events where cursor>? order by cursor',
                                                 (old_cursor,))]
    reconnect_server, peers, logs = OwnedServer(service), {}, []
    try:
        reconnect_server.start()
        log = (root / 'transport.stderr').open('wb'); logs.append(log)
        remote = (dict(binary=str(binary), output=str(root / 'client')) if client is None
                  else dict(client, output=client['output'] + '-reconnect'))
        argv = ([sys.executable, '-B', str(HERE / 'relay_workload_client.py')] if client is None
                else ssh_argv(remote, reconnect_server.port))
        peer = Peer(argv, log); peers['beta'] = peer
        peer.send(dict(operation='hello', binary=remote['binary']))
        hello = peer.receive('hello')
        validate_hello(hello, source_pins(), relay.digest(Path(binary).read_bytes()) if client is None
                       else remote['binary_sha256'])
        clocks = []
        for _ in range(5):
            sent = time.monotonic(); peer.send(dict(operation='ping'))
            pong = peer.receive('pong'); clocks.append((sent, pong['monotonic'], time.monotonic()))
        mapping = clock_mapping(clocks)
        job = dict(at=0, phase='reconnect', operation='reconnect', logical_id='beta-reconnect',
                   task='task-beta-0', checkpoint_sha256=relay.digest(relay.canonical(selected)),
                   target_pin=target, missed_events=missed_events)
        config = dict(binary=remote['binary'], output=remote['output'], credential=member,
            cursor=old_cursor, jobs=[job], duration=1, relay_port=reconnect_server.port,
            url='http://127.0.0.1:' + str(reconnect_server.port if client is None else remote['forward_port']))
        peer.send(dict(operation='configure', config=config)); peer.receive('ready')
        peer.send(dict(operation='start', epoch=time.monotonic() + mapping['offset'] + 1))
        observed = peer.receive('observation', timeout=35)
        require(observed['job'] == job, 'reconnect worker changed its case')
        validate_timing(observed)
        require(observed['result']['pin'] == target and observed['result']['events'] == expected_events
                and observed['result']['cursor'] == old_cursor + missed_events, 'reconnect did not recover exact backlog')
        done = peer.receive('done')
        require(done['credential_removed'] and done['owned_native_operations_finished'], 'reconnect cleanup incomplete')
        require(all(done[key] == hello[key] for key in ('source_sha256', 'binary_sha256')),
                'reconnect worker completion pins changed')
        peer.process.stdin.close()
        require(peer.process.wait(timeout=5) == 0, 'reconnect worker failed')
        with relay.connect(original, readonly=True) as db:
            unchanged = json.loads(db.execute('select pin from revisions order by revision desc limit 1').fetchone()[0])
        require(unchanged == original_pin, 'separate reconnect case changed timed-run state')
        return dict(missed_events=missed_events, observation=observed, worker=hello,
                    clock_calibration=mapping, original_timed_pin_unchanged=True,
                    separate_copy=True, exact_backlog_and_native_pin_verified=True)
    finally:
        close_resources(peers, reconnect_server, logs)
