#!/usr/bin/env python3
"""Opt-in encrypted relay backups. Configuration/credentials stay outside Git.

Linux operator tool, not a context receiver. Only known pinned runtime is copied;
snapshot contents are never executed. Cloud credentials need PutObject, a single
CloudWatch namespace, and a single SNS topic, never object read/delete access.
"""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

from relay_validation import (identifier, snapshot as validate_snapshot,
                              proposal as validate_proposal, checkpoint as validate_checkpoint,
                              citation as validate_citation, canonical as record_canonical,
                              digest as record_digest)


MAX_ENTRIES = 50_000
MAX_METADATA_BYTES = 8 * 1024 * 1024
RESERVE_BYTES = 256 * 1024 * 1024
SNAPSHOT_BYTES = 400_000
ARCHIVE_BYTES = 1_000_000

# Exact pilot schema belongs to the pinned server, including write-path tables
# and uniqueness/primary-key constraints. A future schema needs requalification.
RELAY_SCHEMA = {
    'settings': 'CREATE TABLE settings(workspace TEXT, owner TEXT)',
    'members': 'CREATE TABLE members(name TEXT PRIMARY KEY, token_hash TEXT UNIQUE, enabled INTEGER)',
    'revisions': 'CREATE TABLE revisions(revision INTEGER PRIMARY KEY, snapshot TEXT, pin TEXT)',
    'proposals': 'CREATE TABLE proposals(id TEXT PRIMARY KEY, payload TEXT, accepted INTEGER DEFAULT 0)',
    'events': 'CREATE TABLE events(cursor INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT)',
    'requests': 'CREATE TABLE requests(actor TEXT, id TEXT, hash TEXT, result TEXT, PRIMARY KEY(actor,id))',
}


class BackupError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise BackupError(message)


def sha256(path, limit=None):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        limit = info.st_size if limit is None else limit
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'unsafe digest input')
        total = 0
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            total += len(block)
            require(total <= limit, 'digest input exceeds byte limit')
            h.update(block)
        require(total == info.st_size, 'digest input changed size')
    return h.hexdigest()


def file_pin(path, limit):
    path = Path(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'unsafe artifact input')
    return dict(kind='file', mode=stat.S_IMODE(info.st_mode), uid=info.st_uid,
                gid=info.st_gid, size=info.st_size, sha256=sha256(path, info.st_size))


def private_json(path, owner=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == (os.getuid() if owner is None else owner)
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size < 1_000_000,
                'unsafe private configuration')
        return json.load(stream)


def fsync_directory(path):
    directory = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as stream:
            os.chmod(temporary, 0o600)
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(root, limit, exclude_cache=False, max_entries=MAX_ENTRIES,
              max_metadata_bytes=MAX_METADATA_BYTES):
    root = Path(root)
    result, size, metadata_bytes = {}, 0, 2
    require(root.is_dir() and not root.is_symlink(), 'unsafe input tree')

    def paths(folder):
        # scandir is consumed incrementally, including wide trees of empty files.
        with os.scandir(folder) as entries:
            for entry in entries:
                if exclude_cache and entry.name == '__pycache__':
                    continue
                path = Path(entry.path)
                yield path
                if entry.is_dir(follow_symlinks=False):
                    yield from paths(path)

    def bounded_paths():
        yield root
        yield from paths(root)

    for path in bounded_paths():
        require(len(result) < max_entries, 'input exceeds configured entry limit')
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
                'links and special files are not backed up')
        item = dict(kind='directory' if stat.S_ISDIR(info.st_mode) else 'file',
                    mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        if stat.S_ISREG(info.st_mode):
            size += info.st_size
            require(size <= limit, 'input exceeds configured size limit')
            item.update(size=info.st_size, sha256=sha256(path, info.st_size))
        name = path.relative_to(root).as_posix()
        metadata_bytes += len(json.dumps({name: item}).encode()) + 2
        require(metadata_bytes <= max_metadata_bytes,
                'input exceeds inventory metadata limit')
        result[name] = item
    return result


def disjoint_output(output, *sources):
    output = Path(output).resolve()
    for source in sources:
        source = Path(source).resolve()
        require(output != source and output not in source.parents and source not in output.parents,
                'staging and captured trees must not overlap')


def bounded_copy(source, destination, limit):
    """Reject oversized replacements before writing and cap every streamed write."""
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit,
                'copy input exceeds byte limit or is not regular')
        total = 0
        with Path(destination).open('xb') as output:
            while True:
                block = stream.read(min(1024 * 1024, limit - total + 1))
                if not block:
                    break
                require(total + len(block) <= limit, 'copy input exceeds byte limit')
                output.write(block)
                total += len(block)
            require(total == info.st_size, 'copy input changed size')
            os.fchown(output.fileno(), info.st_uid, info.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
    return str(destination)


def verify_schema(db):
    actual = dict(db.execute("select name,sql from sqlite_master where type='table' "
                             "and name not like 'sqlite_%'"))
    normalize = lambda sql: re.sub(r'\s+', '', sql).lower()
    require({name: normalize(sql) for name, sql in actual.items()}
            == {name: normalize(sql) for name, sql in RELAY_SCHEMA.items()},
            'relay schema mismatch')
    require(db.execute("select 1 from sqlite_master where type in ('trigger','view') or (type='index' and sql is not null) limit 1").fetchone()
            is None, 'unexpected relay schema objects')


def copy_tree(source, destination, limit, exclude_cache=False):
    """Caller quiesces all writers first. Verify bytes and original Linux metadata."""
    before = inventory(source, limit, exclude_cache)
    shutil.copytree(source, destination,
                    copy_function=lambda src, dst: bounded_copy(
                        src, dst, before[Path(src).relative_to(source).as_posix()]['size']),
                    ignore=shutil.ignore_patterns('__pycache__') if exclude_cache else None)
    for name, item in before.items():
        path = Path(destination) / name
        os.chown(path, item['uid'], item['gid'])
        os.chmod(path, item['mode'])
    require(inventory(destination, limit) == before, 'copy inventory mismatch')
    return before


def command(*args):
    # Never emit subprocess stderr: paths/content/errors may contain private data.
    return subprocess.run(args, check=True, capture_output=True, timeout=120).stdout


def health(config):
    credential = private_json(config['health_credential'], Path(config['state']).stat().st_uid)
    request = Request('http://127.0.0.1:' + str(config['port']) + '/updates',
                      data=json.dumps({'workspace': credential['workspace'], 'after': 0}).encode(),
                      headers={'Authorization': 'Bearer ' + credential['token'],
                               'Content-Type': 'application/json'})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise BackupError('readiness redirects are disabled')
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=3) as response:
        require(response.status == 200, 'authenticated readiness failed')


def decode_record(encoded):
    try:
        value = json.loads(encoded)
    except (ValueError, TypeError):
        raise BackupError('invalid persisted JSON') from None
    require(isinstance(value, dict), 'invalid persisted JSON object')
    return value


def validate_pin(pin):
    require(isinstance(pin, dict) and set(pin) == {
                'revision', 'directory_key', 'snapshot_sha256', 'archive_sha256'}
            and type(pin['revision']) is int and pin['revision'] > 0, 'invalid snapshot pin schema')
    require(all(isinstance(pin[key], str) and re.fullmatch(r'[0-9a-f]{64}', pin[key])
                for key in ('archive_sha256', 'snapshot_sha256')), 'invalid snapshot digest')
    require(isinstance(pin['directory_key'], str)
            and re.fullmatch(r'casita.directory.v1:[A-Za-z0-9_-]{43}', pin['directory_key']),
            'invalid snapshot directory key')


def verify_request_identity(db, request_id, request_hash, event, result):
    # New events preserve identity explicitly. Older events can still be checked
    # against the independently persisted hash by reconstructing the exact write.
    if 'request_id' in event or 'request_hash' in event:
        require(event.get('request_id') == request_id and event.get('request_hash') == request_hash,
                'saved request event identity mismatch')
    workspace = db.execute('select workspace from settings').fetchone()[0]
    kind = event['kind']
    common = dict(workspace=workspace, request_id=request_id)
    if kind == 'propose':
        proposal = result['proposal']
        common['expected_revision'] = proposal['base_revision']
        bodies = [{**common, **{key: proposal[key] for key in (
            'task_id', 'scope', 'entry_id', 'statement', 'citation')}}]
    else:
        revision = result['pin']['revision']
        snapshot = decode_record(db.execute('select snapshot from revisions where revision=?',
                                           (revision,)).fetchone()[0])
        require(snapshot['author'] == event['author'], 'saved request snapshot author mismatch')
        common['expected_revision'] = revision - 1
        if kind == 'publish':
            parent = db.execute('select snapshot from revisions where revision=?', (revision - 1,)).fetchone()
            require(revision == 1 or parent is not None, 'missing publish parent')
            previous = decode_record(parent[0])['tasks'] if parent else {}
            changed = [context for task, context in snapshot['tasks'].items()
                       if previous.get(task) != context]
            if 'request_id' not in event:
                require(len(changed) == 1, 'legacy publish requires a unique changed checkpoint')
            require(len(changed) <= 1, 'publish changed multiple tasks')
            candidates = changed or snapshot['tasks'].values()
            bodies = ({**common, 'checkpoint': context} for context in candidates)
        else:
            decision = snapshot['decision']
            require(isinstance(decision, dict), 'saved acceptance decision missing')
            bodies = [{**common, 'proposal_id': decision['proposal_id']}]
    require(any(record_digest(record_canonical(dict(operation=kind, body=body))) == request_hash
                for body in bodies), 'saved request content hash mismatch')


def verify_auxiliary_json(db):
    # These columns are parsed by accept and idempotent writes after restoration.
    requests = {}
    for table, column in (('proposals', 'payload'), ('requests', 'result')):
        require(db.execute('select count(*) from '+table).fetchone()[0] <= MAX_ENTRIES,
                'write-path index exceeds entry limit')
        require(db.execute('select 1 from '+table+' where typeof('+column+") != 'text' or "
                           'length(cast('+column+' as blob)) > ? limit 1',
                           (SNAPSHOT_BYTES,)).fetchone() is None, 'write-path JSON exceeds byte limit')
        if table == 'proposals':
            require(db.execute("select 1 from proposals where typeof(id) != 'text' "
                               "or length(cast(id as blob)) != 64 or typeof(accepted) != 'integer' "
                               "or accepted not in (0,1) limit 1").fetchone() is None,
                    'invalid saved proposal metadata')
            rows = db.execute('select id,payload,accepted from proposals')
        else:
            require(db.execute("select 1 from requests where typeof(actor) != 'text' "
                               "or length(cast(actor as blob)) > 64 or typeof(id) != 'text' "
                               "or length(cast(id as blob)) > 64 or typeof(hash) != 'text' "
                               "or length(cast(hash as blob)) != 64 limit 1").fetchone() is None,
                    'invalid saved request metadata')
            rows = db.execute('select actor,id,hash,result from requests')
        total = 0
        for row in rows:
            encoded = row[1] if table == 'proposals' else row[3]
            total += len(encoded.encode())
            require(total <= MAX_METADATA_BYTES, 'write-path JSON exceeds metadata limit')
            decoded = decode_record(encoded)
            if table == 'proposals':
                validate_proposal(decoded)
                require(row[0] == record_digest(record_canonical(decoded)), 'saved proposal identity mismatch')
                require(db.execute('select 1 from members where name=?', (decoded['author'],)).fetchone()
                        is not None, 'unknown proposal author')
                base = db.execute('select snapshot from revisions where revision=?',
                                  (decoded['base_revision'],)).fetchone()
                require(base is not None, 'missing proposal base snapshot')
                context = decode_record(base[0]).get('tasks', {}).get(decoded['task_id'])
                require(isinstance(context, dict) and context.get('source_revision') == decoded['source_revision'],
                        'proposal source revision mismatch')
                validate_checkpoint(context)
                validate_citation(decoded['citation'], context)
                if row[2]:
                    accepted = db.execute('select snapshot from revisions where revision=?',
                                          (decoded['base_revision'] + 1,)).fetchone()
                    decision = decode_record(accepted[0]).get('decision') if accepted else None
                    require(isinstance(decision, dict) and decision.get('proposal_id') == row[0],
                            'accepted proposal has no matching decision')
            else:
                actor, request_id, request_hash = row[:3]
                require(identifier(actor) and identifier(request_id)
                        and re.fullmatch(r'[0-9a-f]{64}', request_hash), 'invalid saved request metadata')
                require(type(decoded.get('cursor')) is int and decoded['cursor'] > 0,
                        'invalid saved request cursor')
                event_row = db.execute('select payload from events where cursor=?',
                                       (decoded['cursor'],)).fetchone()
                require(event_row is not None, 'missing saved request event')
                event = decode_record(event_row[0])
                require(db.execute('select 1 from members where name=?', (actor,)).fetchone() is not None,
                        'unknown saved request actor')
                require(event.get('author') == actor, 'saved request actor mismatch')
                if set(decoded) == {'pin', 'cursor'}:
                    validate_pin(decoded['pin'])
                    revision = db.execute('select pin from revisions where revision=?',
                                          (decoded['pin']['revision'],)).fetchone()
                    require(revision is not None and decoded['pin'] == decode_record(revision[0])
                            and event.get('kind') in ('publish', 'accept')
                            and event.get('pin') == decoded['pin']
                            and event.get('revision') == decoded['pin']['revision'], 'saved request pin mismatch')
                else:
                    require(set(decoded) == {'proposal_id', 'proposal', 'cursor'},
                            'invalid saved request result schema')
                    validate_proposal(decoded['proposal'])
                    saved = db.execute('select payload from proposals where id=?',
                                       (decoded['proposal_id'],)).fetchone()
                    require(saved is not None and decode_record(saved[0]) == decoded['proposal'],
                            'missing saved request proposal')
                    require(decoded['proposal_id'] == record_digest(record_canonical(decoded['proposal']))
                            and event.get('kind') == 'propose'
                            and event.get('proposal_id') == decoded['proposal_id']
                            and event.get('proposal') == decoded['proposal']
                            and event.get('revision') == decoded['proposal']['base_revision']
                            and decoded['proposal']['author'] == actor, 'saved request proposal mismatch')
                verify_request_identity(db, request_id, request_hash, event, decoded)
                require(decoded['cursor'] not in requests, 'duplicate saved request cursor')
                requests[decoded['cursor']] = decoded
    return requests

def verify_transitions(db, requests):
    """Replay data-only operation semantics over the already bounded copied rows."""
    revision, current, proposals, count = 0, None, set(), 0
    for cursor, raw in db.execute('select cursor,payload from events order by cursor'):
        count += 1
        require(cursor in requests, 'event has no originating request')
        event = decode_record(raw)
        request = requests[cursor]
        kind = event['kind']
        if kind == 'propose':
            proposal = request['proposal']
            require(current is not None and event['revision'] == revision
                    and proposal['base_revision'] == revision
                    and event.get('created_at') == proposal['created_at']
                    and event['author'] == proposal['author']
                    and request['proposal_id'] not in proposals, 'invalid proposal transition')
            proposals.add(request['proposal_id'])
            continue
        require(event['revision'] == revision + 1, 'invalid revision transition')
        row = db.execute('select snapshot from revisions where revision=?', (revision + 1,)).fetchone()
        require(row is not None, 'missing transition snapshot')
        value = decode_record(row[0])
        require(value['author'] == event['author'] and value['created_at'] == event.get('created_at'),
                'transition author or timestamp mismatch')
        tasks = current['tasks'] if current else {}
        memory = current['memory'] if current else []
        if kind == 'publish':
            require(set(tasks) <= set(value['tasks'])
                    and sum(tasks.get(task) != checkpoint for task, checkpoint in value['tasks'].items()) <= 1
                    and value['memory'] == memory and value['decision'] is None,
                    'publish changed unrelated context')
        else:
            decision = value['decision']
            require(isinstance(decision, dict) and decision['proposal_id'] in proposals,
                    'acceptance has no preceding proposal')
            saved = db.execute('select payload,accepted from proposals where id=?', (decision['proposal_id'],)).fetchone()
            require(saved is not None and saved[1] == 1, 'acceptance proposal is not marked accepted')
            proposal = decode_record(saved[0])
            require(proposal['base_revision'] == revision and value['tasks'] == tasks,
                    'acceptance changed tasks or used a stale proposal')
            retained = [entry for entry in memory if not (
                entry['entry_id'] == proposal['entry_id'] and entry['scope'] == proposal['scope']
                and (entry['scope'] == 'workspace' or entry['task_id'] == proposal['task_id']))]
            accepted = dict(proposal, accepted_by=event['author'], accepted_at=value['created_at'],
                            proposal_id=decision['proposal_id'])
            require(value['memory'] == retained + [accepted], 'acceptance changed unrelated memory')
        revision, current = revision + 1, value
    require(count == len(requests)
            and revision == db.execute('select count(*) from revisions').fetchone()[0]
            and len(proposals) == db.execute('select count(*) from proposals').fetchone()[0],
            'history has unrecorded requests, revisions or proposals')


def verify_snapshots(db, state, binary, run=command, max_bytes=512 * 1024 * 1024):
    """Verify a disposable store copy without modifying recovery payload bytes."""
    store = state / 'store'
    require(store.is_dir() and not store.is_symlink(), 'missing native store')
    # Even fsck --dry-run and checkout write process-pin ledger files. Running
    # them as the backup operator can create files inaccessible to the restored
    # service user. Keep those writes out of the exact quiescent recovery copy.
    with tempfile.TemporaryDirectory(prefix='staging-native-', dir=state.parent.parent) as temp:
        verified_store = Path(temp) / 'store'
        copy_tree(store, verified_store, max_bytes)
        verify_snapshot_objects(db, state, binary, verified_store, run)


def verify_snapshot_objects(db, state, binary, store, run):
    run(str(binary), '--repository', str(store), 'fsck', '--dry-run')
    require(db.execute('select count(*) from revisions').fetchone()[0] <= MAX_ENTRIES,
            'revision index exceeds limits')
    require(db.execute("select 1 from revisions where typeof(snapshot) != 'text' or typeof(pin) != 'text' "
                       "or length(cast(snapshot as blob)) > 400000 "
                       'or length(cast(pin as blob)) > 4096 limit 1').fetchone() is None,
            'revision index exceeds limits')
    count = 0
    for revision, snapshot, encoded_pin in db.execute('select revision,snapshot,pin from revisions'):
        count += 1
        require(count <= MAX_ENTRIES and len(snapshot.encode()) <= SNAPSHOT_BYTES
                and len(encoded_pin.encode()) <= 4096, 'revision index exceeds limits')
        pin = decode_record(encoded_pin)
        validate_pin(pin)
        require(type(revision) is int and pin['revision'] == revision, 'revision pin mismatch')
        workspace, owner = db.execute('select workspace,owner from settings').fetchone()
        decoded = validate_snapshot(decode_record(snapshot), workspace, revision)
        require(all(entry['accepted_by'] == owner for entry in decoded['memory'])
                and (decoded['decision'] is None or decoded['decision']['accepted_by'] == owner),
                'accepted memory must name the configured owner')
        require(hashlib.sha256(snapshot.encode()).hexdigest() == pin['snapshot_sha256'],
                'snapshot index hash mismatch')
        archive = state / 'snapshots' / (pin['archive_sha256'] + '.casitar')
        require(archive.is_file() and not archive.is_symlink()
                and archive.stat().st_size <= ARCHIVE_BYTES
                and sha256(archive) == pin['archive_sha256'], 'referenced archive missing or damaged')
        report = json.loads(run(str(binary), 'archive', 'verify', str(archive), '--json',
                                '--max-archive-bytes', str(ARCHIVE_BYTES), '--max-payload-bytes', str(SNAPSHOT_BYTES),
                                '--max-total-payload-bytes', str(SNAPSHOT_BYTES),
                                '--max-payloads', '100', '--max-records', '100'))
        require(report['validity'] == 'verified' and report['roots'] == [pin['directory_key']],
                'archive root mismatch')
        with tempfile.TemporaryDirectory(prefix='staging-verify-', dir=state.parent.parent) as temp:
            restored = Path(temp) / 'snapshot'
            run(str(binary), '--repository', str(store), 'checkout', pin['directory_key'],
                str(restored), '--no-root')
            files = inventory(restored, SNAPSHOT_BYTES)
            require({k: v['sha256'] for k, v in files.items() if v['kind'] == 'file'}
                    == {'snapshot.json': pin['snapshot_sha256']}, 'native snapshot content mismatch')
    require(count > 0, 'no committed relay snapshot')


def verify_events(db):
    require(db.execute('select count(*) from events').fetchone()[0] <= MAX_ENTRIES,
            'event index exceeds entry limit')
    require(db.execute("select 1 from events where typeof(payload) != 'text' or length(cast(payload as blob)) > 400000 "
                       'limit 1').fetchone() is None, 'event payload exceeds byte limit')
    total, previous = 0, 0
    for cursor, encoded in db.execute('select cursor,payload from events order by cursor'):
        total += len(encoded.encode())
        require(total <= MAX_METADATA_BYTES, 'event index exceeds metadata limit')
        require(type(cursor) is int and cursor == previous + 1, 'invalid event cursor')
        event = decode_record(encoded)
        require(isinstance(event, dict) and 'cursor' not in event
                and event.get('kind') in ('publish', 'propose', 'accept')
                and isinstance(event.get('author'), str)
                and type(event.get('revision')) is int and event['revision'] > 0,
                'invalid event payload')
        previous = cursor
    require(db.execute('select count(*) from sqlite_sequence').fetchone()[0] <= 1
            and db.execute("select 1 from sqlite_sequence where typeof(name) != 'text' or name != 'events' "
                           "or typeof(seq) != 'integer' or seq < 0 or seq >= 9223372036854775807 "
                           'limit 1').fetchone() is None, 'invalid event sequence')
    sequence = db.execute("select seq from sqlite_sequence where name='events'").fetchone()
    require((sequence is None and previous == 0)
            or (sequence is not None and sequence[0] == previous), 'inconsistent event sequence')
    return previous


def verify_member_credential(state, name, token_hash, workspace):
    path = state / 'credentials' / (name + '.json')
    file_pin(path, 4096)  # Reject large/special inputs before JSON decoding.
    credential = private_json(path, state.stat().st_uid)
    require(isinstance(credential, dict) and set(credential) == {'workspace', 'token'}
            and credential['workspace'] == workspace and isinstance(credential['token'], str)
            and 30 <= len(credential['token']) <= 100
            and record_digest(credential['token'].encode()) == token_hash,
            'enabled member credential mismatch')


def capture(config, payload, run=command, ready=health):
    state, app = Path(config['state']), Path(config['app'])
    binary = Path(config['binary'])
    disjoint_output(payload, state, app)
    require(file_pin(binary, config['max_bytes']) == config['binary_pin'],
            'native binary pin mismatch')
    unit = Path(config['service_unit'])
    require(file_pin(unit, config['max_bytes']) == config['service_unit_pin'],
            'service unit pin mismatch')
    expected = private_json(config['runtime_inventory'])
    actual = inventory(app, config['max_bytes'], exclude_cache=True)
    require(actual == expected,
            'runtime source pin mismatch')
    fixed_bytes = binary.stat().st_size + unit.stat().st_size
    require(fixed_bytes < config['max_bytes'], 'runtime artifacts exceed size limit')
    run('/usr/bin/systemctl', 'is-active', '--quiet', config['service'])
    start = time.monotonic()
    try:
        # Finally starts the service even if stop/copy raises. ExecStopPost also
        # restarts it when this controller is killed before Python can clean up.
        run('/usr/bin/systemctl', 'stop', config['service'])
        state_inventory = copy_tree(state, payload / 'state', config['max_bytes'] - fixed_bytes)
    finally:
        run('/usr/bin/systemctl', 'start', config['service'])
    last = None
    for _ in range(30):
        try:
            ready(config)
            last = None
            break
        except Exception as error:
            last = error
            time.sleep(0.2)
    require(last is None, 'authenticated readiness failed')
    pause = round(time.monotonic() - start, 3)
    state_bytes = sum(item.get('size', 0) for item in state_inventory.values())
    copied = copy_tree(app, payload / 'runtime-app',
                       config['max_bytes'] - fixed_bytes - state_bytes, exclude_cache=True)
    require(copied == expected,
            'copied runtime source pin mismatch')
    runtime_bytes = sum(item.get('size', 0) for item in copied.values())
    remaining = config['max_bytes'] - state_bytes - runtime_bytes
    bounded_copy(binary, payload / 'linux-casita', remaining)
    require(file_pin(payload / 'linux-casita', remaining) == config['binary_pin'],
            'copied native binary pin mismatch')
    remaining -= (payload / 'linux-casita').stat().st_size
    bounded_copy(unit, payload / 'context-relay.service', remaining)
    require(file_pin(payload / 'context-relay.service', remaining) == config['service_unit_pin'],
            'copied service unit pin mismatch')
    db = sqlite3.connect('file:' + str(payload / 'state/relay.sqlite') + '?mode=ro', uri=True)
    try:
        require(db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'SQLite integrity failed')
        verify_schema(db)
        require(db.execute('select count(*) from settings').fetchone()[0] == 1,
                'relay needs exactly one settings row')
        require(db.execute("select 1 from settings where typeof(workspace) != 'text' "
                           "or length(cast(workspace as blob)) > 64 or typeof(owner) != 'text' "
                           "or length(cast(owner as blob)) > 64 limit 1").fetchone() is None,
                'invalid relay settings metadata')
        workspace, owner = db.execute('select workspace,owner from settings').fetchone()
        require(identifier(workspace) and identifier(owner)
                and db.execute('select 1 from members where name=? and enabled=1', (owner,)).fetchone()
                is not None, 'relay owner must be an enabled member')
        cursor = verify_events(db)
        require(db.execute('select count(*) from members').fetchone()[0] <= 11,
                'member index exceeds pilot limit')
        require(db.execute("select 1 from members where typeof(name) != 'text' "
                           "or length(cast(name as blob)) > 64 or typeof(token_hash) != 'text' "
                           "or length(cast(token_hash as blob)) != 64 or typeof(enabled) != 'integer' "
                           "or enabled not in (0,1) limit 1").fetchone() is None,
                'member metadata exceeds pilot limit')
        members = []
        for name, token_hash, enabled in db.execute('select name,token_hash,enabled from members order by name'):
            require(identifier(name) and re.fullmatch(r'[0-9a-f]{64}', token_hash)
                    and type(enabled) is int and enabled in (0, 1), 'invalid relay member')
            if enabled:
                verify_member_credential(payload / 'state', name, token_hash, workspace)
            members.append({'name': name, 'enabled': bool(enabled)})
        require(len(json.dumps(members).encode()) <= 2048, 'member metadata exceeds limit')
        # Bound and validate referenced rows before auxiliary cross-lookups can
        # materialize snapshot, event or pin JSON.
        verify_snapshots(db, payload / 'state', payload / 'linux-casita', run,
                         max_bytes=config['max_bytes'])
        requests = verify_auxiliary_json(db)
        verify_transitions(db, requests)
        head = db.execute('select revision,pin from revisions order by revision desc limit 1').fetchone()
        require(head is not None, 'no committed relay snapshot')
    finally:
        db.close()
    manifest = dict(schema='relay-private-recovery.v1', source_commit=config['source_commit'],
                    casita_commit=config['casita_commit'], pin=json.loads(head[1]),
                    revision=head[0], event_cursor=cursor, service_pause_seconds=pause,
                    members=members,
                    inventory=inventory(payload, config['max_bytes']))
    require(len(json.dumps(manifest, indent=2).encode()) + 1 <= MAX_METADATA_BYTES + 64 * 1024,
            'recovery manifest exceeds metadata limit')
    write_json(payload / 'recovery-manifest.json', manifest)
    return manifest


def encrypt(payload, destination, recipient):
    # Stream plaintext directly to age, never create a plaintext tar file.
    with Path(destination).open('xb') as output, tempfile.TemporaryFile() as errors:
        os.chmod(destination, 0o600)
        tar = subprocess.Popen(['/usr/bin/tar', '-czf', '-', '-C', str(payload), '.'],
                               stdout=subprocess.PIPE, stderr=errors)
        age = subprocess.Popen(['/usr/bin/age', '-r', recipient], stdin=tar.stdout,
                               stdout=output, stderr=errors)
        tar.stdout.close()
        try:
            require(age.wait(timeout=120) == 0 and tar.wait(timeout=20) == 0,
                    'archive encryption failed')
        finally:
            for process in (age, tar):
                if process.poll() is None:
                    process.kill()
                process.wait()
    with Path(destination).open('rb') as stream:
        require(stream.read(22) == b'age-encryption.org/v1\n', 'not an age archive')


def object_keys(now, run_id, include_daily):
    name = now.strftime('%Y/%m/%d/%Y%m%dT%H%M%SZ-') + run_id + '.tar.gz.age'
    return ['hourly/' + name] + (['daily/' + name] if include_daily else [])


class Cloud:
    def __init__(self, config):
        import boto3  # Optional operator dependency; ordinary demo/tests need none.
        from botocore.config import Config
        session = boto3.Session(region_name=config['region'])
        options = Config(connect_timeout=5, read_timeout=30,
                         retries={'max_attempts': 3, 'mode': 'standard'})
        self.s3 = session.client('s3', config=options)
        self.metrics = session.client('cloudwatch', config=options)
        self.sns = session.client('sns', config=options)
        self.config = config

    def upload(self, archive, key):
        checksum = base64.b64encode(bytes.fromhex(sha256(archive))).decode()
        with Path(archive).open('rb') as stream:
            response = self.s3.put_object(Bucket=self.config['bucket'], Key=key, Body=stream,
                                          ServerSideEncryption='AES256', ChecksumSHA256=checksum,
                                          ContentType='application/octet-stream')
        require(response.get('ChecksumSHA256') == checksum, 'S3 checksum acknowledgement mismatch')

    def age(self, seconds):
        self.metrics.put_metric_data(Namespace=self.config['namespace'], MetricData=[{
            'MetricName': 'BackupAgeSeconds', 'Value': seconds, 'Unit': 'Seconds',
            'Dimensions': [{'Name': 'Relay', 'Value': self.config['relay_name']}]}])

    def alert(self, message):
        self.sns.publish(TopicArn=self.config['topic_arn'],
                         Subject=('TEST: ' if self.config.get('qualification_test') else '')
                         + 'Context relay backup status', Message=message)


def backup(config, cloud, now=None, make_capture=capture, make_archive=encrypt, clock=time.monotonic):
    started = clock()
    now = now or dt.datetime.now(dt.timezone.utc)
    spool = Path(config['spool'])
    status_path = spool / 'status.json'
    previous = private_json(status_path) if status_path.exists() else {}
    phase, run_id = 'preflight', uuid.uuid4().hex
    stage = spool / ('staging-' + run_id)
    archive = spool / ('pending-' + run_id + '.age')
    try:
        disjoint_output(spool, config['state'], config['app'])
        require(shutil.disk_usage(spool).free > 2 * config['max_bytes'] + RESERVE_BYTES,
                'insufficient staging capacity')
        stage.mkdir(mode=0o700)
        phase = 'capture'
        manifest = make_capture(config, stage)
        phase = 'encryption'
        make_archive(stage, archive, config['recipient'])
        # Delete plaintext before any remote upload. Always delete on failure too.
        shutil.rmtree(stage)
        fsync_directory(spool)
        phase = 'upload'
        daily_date = now.date().isoformat()
        keys = object_keys(now, run_id, previous.get('daily_date') != daily_date)
        for key in keys:
            cloud.upload(archive, key)
        result = dict(schema='relay-backup-status.v1', last_success=now.timestamp(),
                      daily_date=daily_date, keys=keys, ciphertext_sha256=sha256(archive),
                      ciphertext_bytes=archive.stat().st_size, revision=manifest['revision'],
                      event_cursor=manifest['event_cursor'], failed=False)
        write_json(status_path, result)
        # Once S3 and the status file acknowledge success, telemetry cannot roll it back.
        try:
            cloud.age(max(0, clock() - started))
        except Exception:
            pass  # The separate monitor retries freshness publication.
        if previous.get('failed'):
            try:
                cloud.alert('Recovery: encrypted off-host backup completed. No context or credentials included in this alert.')
            except Exception:
                pass  # Notification submission is independent of backup success.
        return result
    except Exception:
        # Do not report raw exceptions or paths to the cloud or journal.
        failure = dict(previous, failed=True, failure_phase=phase,
                       failure_at=dt.datetime.now(dt.timezone.utc).timestamp())
        write_json(status_path, failure)
        try:
            cloud.alert('Backup failed during ' + phase + '. Inspect private operator logs/configuration; no secret or source content is included.')
        except Exception:
            pass  # An independently evaluated missing heartbeat catches host/network failures.
        raise BackupError('backup failed during ' + phase) from None
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        archive.unlink(missing_ok=True)
        fsync_directory(spool)


def monitor(config, cloud, now=None):
    status = private_json(Path(config['spool']) / 'status.json')
    current = time.time() if now is None else now
    success = status.get('last_success', 0)
    require(type(success) in (int, float) and math.isfinite(success)
            and 0 <= success <= current, 'invalid backup success timestamp')
    age = current - success
    cloud.age(age)
    return age


def cleanup(spool):
    """Root-only fixed spool; also used after controller SIGKILL by systemd."""
    removed = False
    try:
        for path in Path(spool).iterdir():
            if path.name.startswith('staging-') and not path.is_symlink() and path.is_dir():
                removed = True
                shutil.rmtree(path)
            elif path.name.startswith('pending-') and not path.is_symlink() and path.is_file():
                removed = True
                path.unlink()
    finally:
        if removed:
            fsync_directory(spool)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'monitor', 'cleanup'])
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    require(os.getuid() == 0, 'run only through the reviewed root operator service')
    config = private_json(args.config)
    require(config['service'] == 'context-relay.service', 'unexpected service')
    spool = Path(config['spool'])
    require(spool.is_dir() and not spool.is_symlink() and spool.stat().st_uid == 0
            and stat.S_IMODE(spool.stat().st_mode) == 0o700, 'unsafe spool directory')
    disjoint_output(spool, config['state'], config['app'])
    with (spool / 'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.operation in ('backup', 'cleanup'):
            cleanup(spool)
        if args.operation == 'cleanup':
            return
        cloud = Cloud(config)
        if args.operation == 'backup':
            result = backup(config, cloud)
            print('Encrypted off-host backup complete; revision ' + str(result['revision']))
        else:
            monitor(config, cloud)
            print('Backup freshness metric published')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Backup controller failed; inspect private operator configuration. No sensitive error details emitted.')
        raise SystemExit(1)
