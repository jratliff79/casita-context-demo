"""Bounded schedule, retry and provenance rules, independent of execution speed."""
import math
from pathlib import Path
import re
import shlex
from urllib.error import HTTPError

from relay_load import SOURCE_FILES as LOAD_SOURCES, fixture, require
from context_relay import digest

HERE = Path(__file__).resolve().parent
NAMES = ('alpha', 'beta')
SOURCE_FILES = (*LOAD_SOURCES, 'relay_backup.py', 'relay_monitor.py',
                'relay_workload.py', 'relay_workload_plan.py',
                'relay_workload_client.py', 'relay_workload_controller.py',
                'relay_workload_backup.py', 'relay_workload_reconnect.py')


def source_pins():
    return {name: digest((HERE / name).read_bytes()) for name in SOURCE_FILES}


def make_plan(normal_seconds=3600, burst_seconds=600, period=60, initial_revisions=6):
    for value in (normal_seconds, burst_seconds, period, initial_revisions):
        require(type(value) is int, 'schedule inputs must be integers')
    require(4 <= period <= 60 and period % 4 == 0 or period == 10,
            'period must support bounded publication/consultation spacing')
    require(0 < normal_seconds <= 3600 and normal_seconds % period == 0, 'invalid normal duration')
    require(0 < burst_seconds <= 600 and burst_seconds % (period / 2) == 0, 'invalid burst duration')
    normal_count, burst_count = normal_seconds // period, int(2 * burst_seconds / period)
    commits = 2 * (normal_count + burst_count)
    require(6 <= initial_revisions <= 1000 - commits, 'invalid retained revision horizon')
    plan = dict(normal_seconds=normal_seconds, burst_seconds=burst_seconds, period=period,
                duration=normal_seconds + burst_seconds, initial_revisions=initial_revisions,
                final_revisions=initial_revisions + commits, clients={},
                full_profile=(normal_seconds, burst_seconds, period) == (3600, 600, 60))
    for operator, name in enumerate(NAMES):
        jobs, version = [], 0
        for phase, count in (('normal', normal_count), ('burst', burst_count)):
            for index in range(count):
                version += 1
                at = (index * period + operator * period / 2 if phase == 'normal'
                      else normal_seconds + index * period / 2)
                base = (initial_revisions + index * 2 + operator if phase == 'normal'
                        else initial_revisions + normal_count * 2 + index * 2)
                common = dict(phase=phase, task=f'task-{name}-{(version - 1) % 2}', version=version)
                jobs.append(dict(common, at=at, operation='publish', base=base, payload_bytes=4096,
                                 logical_id=f'{name}-{phase}-{index}'))
                jobs.append(dict(common, at=at + period / 4, operation='consult',
                                 logical_id=f'{name}-{phase}-consult-{index}'))
        poll_seconds = min(5, period / 4)
        plan['poll_seconds'] = poll_seconds
        for index in range(math.ceil(plan['duration'] / poll_seconds)):
            at = index * poll_seconds
            jobs.append(dict(at=at, phase='normal' if at < normal_seconds else 'burst',
                             operation='updates', logical_id=f'{name}-updates-{index}'))
        plan['clients'][name] = sorted(jobs, key=lambda j: (j['at'], j['operation']))
    return plan


def clock_mapping(samples):
    require(bool(samples), 'clock calibration unavailable')
    for sent, remote, received in samples:
        require(all(type(v) in (int, float) and math.isfinite(v) for v in (sent, remote, received))
                and sent <= received, 'invalid clock calibration')
    sent, remote, received = min(samples, key=lambda row: row[2] - row[0])
    require(received - sent <= .5, 'clock calibration uncertainty exceeds 250 ms')
    return dict(offset=remote - (sent + received) / 2, uncertainty=(received - sent) / 2)


def publish_logical(job, request):
    selected = fixture(job['task'], job['version'], job['payload_bytes'])
    body = dict(request_id=job['logical_id'] + '-initial', expected_revision=job['base'], checkpoint=selected)
    statuses = []
    try:
        response = request('publish', body)
    except HTTPError as error:
        error.close()
        if error.code != 409 or job['phase'] != 'burst':
            raise
        statuses.append(409)
        current = request('context', {})
        require(current['revision'] == job['base'] + 1, 'conflict refresh differs from the other commit')
        body = dict(body, request_id=job['logical_id'] + '-retry', expected_revision=current['revision'])
        try:
            response = request('publish', body)
        except HTTPError as error:
            error.close()
            raise
    statuses.append(200)
    require(response['pin']['revision'] == body['expected_revision'] + 1,
            'publication acknowledged an unexpected revision')
    return dict(logical_id=job['logical_id'], statuses=statuses, pin=response['pin'],
                cursor=response['cursor'], task=job['task'], version=job['version'])


def advance_cursor(page, cursor):
    for event in page['events']:
        require(type(event.get('cursor')) is int and event['cursor'] == cursor + 1,
                'noncontiguous event cursor')
        cursor += 1
    require(type(page['next_cursor']) is int and page['next_cursor'] == cursor, 'page cursor mismatch')
    return cursor


def catch_up_events(request, cursor, count):
    require(type(count) is int and 1 <= count <= 100, 'invalid reconnect backlog')
    return drain_updates(request, cursor, cursor + count)


def drain_updates(request, cursor, target):
    require(type(cursor) is int and type(target) is int and 0 <= cursor <= target
            and target - cursor <= 1000, 'invalid final catch-up target')
    events = []
    while cursor < target:
        page = request('updates', dict(after=cursor))
        require(bool(page['events']), 'catch-up stopped before the target cursor')
        cursor = advance_cursor(page, cursor)
        events.extend(page['events'])
        require(cursor <= target, 'catch-up exceeded the acknowledged target')
    last = request('updates', dict(after=cursor))
    require(last['events'] == [] and advance_cursor(last, cursor) == target, 'catch-up target was not quiescent')
    return dict(cursor=cursor, events=events)


def ssh_argv(client, relay_port):
    require(type(client['port']) is int and 1 <= client['port'] <= 65535, 'invalid SSH port')
    for port in (client['forward_port'], relay_port):
        require(type(port) is int and 1024 <= port <= 65535, 'invalid SSH or forwarding port')
    require(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,200}', client['host']) is not None,
            'invalid SSH host alias')
    require(isinstance(client['checkout'], str) and client['checkout'].startswith('/')
            and '\n' not in client['checkout'], 'use an absolute trusted checkout')
    require(re.fullmatch(r'(?:/[A-Za-z0-9_./-]+|python3(?:\.[0-9]+)?)', client['python']) is not None,
            'invalid remote Python executable')
    command = 'cd ' + shlex.quote(client['checkout']) + ' && exec ' + shlex.join([
        client['python'], '-B', 'relay_workload_client.py'])
    config = []
    if 'ssh_config' in client:
        require(Path(client['ssh_config']).is_absolute(), 'SSH config path must be absolute')
        config = ['-F', client['ssh_config']]
    return ['ssh', *config, '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2',
            '-p', str(client['port']), '-R',
            f"127.0.0.1:{client['forward_port']}:127.0.0.1:{relay_port}", '--', client['host'], command]


def validate_hello(hello, source_pins, binary_pin):
    require(hello['source_sha256'] == source_pins, 'worker source pin mismatch')
    require(hello['binary_sha256'] == binary_pin, 'worker binary pin mismatch')
    require(hello['system'] in ('Linux', 'Darwin') and bool(hello['machine']) and bool(hello['hostname']),
            'worker environment unavailable')


def overlap_count(observations, start, end):
    return sum(max(row['start'] + row['uncertainty'], start)
               < min(row['end'] - row['uncertainty'], end) for row in observations)


def seed_history(service, token, initial_revisions, payload_bytes=4096):
    require(type(initial_revisions) is int and 6 <= initial_revisions <= 1000, 'invalid seed horizon')
    tasks, revision, cursor = {}, 0, 0
    def call(operation, body):
        nonlocal revision, cursor
        result = service.handle(token, operation, dict(body, workspace=service.workspace,
                                expected_revision=revision))
        if operation != 'propose':
            revision = result['pin']['revision']
        cursor = result['cursor']
        return result
    for name in NAMES:
        for index in range(2):
            task = f'task-{name}-{index}'
            tasks[task] = fixture(task, 0, payload_bytes)
            call('publish', dict(request_id='seed-' + task, checkpoint=tasks[task]))
    source = tasks['task-alpha-0']['files'][0]
    for scope in ('workspace', 'task'):
        proposed = call('propose', dict(request_id='seed-propose-' + scope, task_id='task-alpha-0',
            scope=scope, entry_id='synthetic-' + scope,
            statement='Synthetic workload memory; never execute received source.',
            citation=dict(path=source['path'], sha256=source['sha256'], start_line=1, end_line=1,
                          excerpt=[source['text'].splitlines()[0]])))
        call('accept', dict(request_id='seed-accept-' + scope, proposal_id=proposed['proposal_id']))
    for index in range(initial_revisions - 6):
        task = list(tasks)[index % 4]
        tasks[task] = fixture(task, 'aged-' + str(index), payload_bytes)
        call('publish', dict(request_id=f'aged-{index}', checkpoint=tasks[task]))
    require(revision == initial_revisions, 'seed history count mismatch')
    return dict(revision=revision, cursor=cursor, tasks=tasks)
