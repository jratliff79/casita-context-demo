#!/usr/bin/env python3
"""Opt-in read-only relay monitoring; no context, paths or identities in metrics."""
import argparse
from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from relay_readiness import request_proof, response_proof

RESERVE_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 50_000
METRICS = {
    'Readiness': 'Count', 'MonitorComplete': 'Count', 'StoragePressure': 'Count',
    'AvailableDiskBytes': 'Bytes', 'DiskFreePercent': 'Percent',
    'InodesFreePercent': 'Percent', 'StateAllocatedBytes': 'Bytes',
    'RetainedRevisions': 'Count', 'RetainedEvents': 'Count',
    'SpoolAvailableDiskBytes': 'Bytes', 'SpoolDiskFreePercent': 'Percent',
    'SpoolInodesFreePercent': 'Percent',
}


class MonitorError(Exception):
    pass


def require(condition):
    if not condition:
        raise MonitorError('monitor input or observation rejected')


def private_json(path, owner):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 16_384)
        raw = stream.read(16_385)
        require(len(raw) <= 16_384)
    return json.loads(raw)


def validate_config(config):
    require(isinstance(config, dict) and set(config) == {
        'state', 'spool', 'credential', 'port', 'backup_max_bytes', 'free_percent',
        'inodes_free_percent', 'region', 'namespace', 'relay_name',
    })
    for name in ('state', 'spool', 'credential'):
        require(isinstance(config[name], str) and Path(config[name]).is_absolute())
    require(type(config['port']) is int and 1 <= config['port'] <= 65535)
    require(type(config['backup_max_bytes']) is int and 0 < config['backup_max_bytes'] <= 2 ** 40)
    for name in ('free_percent', 'inodes_free_percent'):
        require(type(config[name]) in (int, float) and 0 < config[name] < 100)
    for name in ('namespace', 'relay_name', 'region'):
        require(isinstance(config[name], str) and
                re.fullmatch(r'[A-Za-z0-9_/-]{1,100}', config[name]))
    require(not config['namespace'].startswith('AWS/'))
    return config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise MonitorError('readiness redirect rejected')


def readiness(config):
    # A separate server-enforced readiness scope cannot read context or write state.
    owner = Path(config['state']).stat().st_uid
    credential = private_json(config['credential'], owner)
    require(isinstance(credential, dict) and set(credential) == {'workspace', 'token', 'scope'}
            and credential['scope'] == 'relay-readiness.v1')
    require(isinstance(credential['workspace'], str) and
            re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}', credential['workspace']))
    require(isinstance(credential['token'], str) and
            re.fullmatch(r'[A-Za-z0-9_-]{30,100}', credential['token']))
    nonce = secrets.token_hex(32)
    body = {'nonce': nonce}
    request = Request('http://127.0.0.1:' + str(config['port']) + '/readiness',
                      data=json.dumps(body).encode(),
                      headers={'Authorization': 'RelayReadiness ' + request_proof(credential['token'], body),
                               'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=3) as response:
        require(response.status == 200)
        raw = response.read(257)
        require(len(raw) <= 256)
        result = json.loads(raw)
        require(isinstance(result, dict) and set(result) == {'ready', 'nonce', 'proof'}
                and result['ready'] is True and result['nonce'] == nonce
                and isinstance(result['proof'], str) and re.fullmatch(r'[0-9a-f]{64}', result['proof'])
                and secrets.compare_digest(result['proof'], response_proof(
                    credential['token'], credential['workspace'], nonce)))


def filesystem(config, field='state'):
    info = os.statvfs(config[field])
    require(info.f_blocks > 0 and info.f_frsize > 0 and
            0 <= info.f_bavail <= info.f_blocks and info.f_files > 0 and
            0 <= info.f_favail <= info.f_files)
    prefix = '' if field == 'state' else 'Spool'
    return {prefix + 'AvailableDiskBytes': info.f_bavail * info.f_frsize,
            prefix + 'DiskFreePercent': 100 * info.f_bavail / info.f_blocks,
            prefix + 'InodesFreePercent': 100 * info.f_favail / info.f_files}


def storage_pressure(config, result):
    # Match relay_backup's reserve on the spool, not an unrelated state mount.
    return int(result['SpoolAvailableDiskBytes'] <= 2 * config['backup_max_bytes'] + RESERVE_BYTES or
               any(result[prefix + 'DiskFreePercent'] <= config['free_percent'] or
                   result[prefix + 'InodesFreePercent'] <= config['inodes_free_percent']
                   for prefix in ('', 'Spool')))


def state_size(config, clock=time.monotonic):
    started, entries, allocated = clock(), 1, 0
    state = Path(config['state'])
    require(stat.S_ISDIR(state.lstat().st_mode))
    # Bytes allocated, including directory entries; no following links, file reads
    # or native store commands. This is a sample, not a quiescent inventory.
    def visit(fd):
        nonlocal entries, allocated
        info = os.fstat(fd)
        require(entries <= MAX_ENTRIES and clock() - started <= 10)
        allocated += info.st_blocks * 512
        with os.scandir(fd) as children:
            for child in children:
                if fd == root_fd and child.name == 'credentials':
                    continue  # Deployment hides ordinary member credentials from the monitor.
                # Native maintenance may unlink entries during this live sample.
                # Count even vanished entries so churn cannot bypass either bound.
                entries += 1
                require(entries <= MAX_ENTRIES and clock() - started <= 10)
                try:
                    info = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                if stat.S_ISDIR(info.st_mode):
                    try:
                        nested = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    except FileNotFoundError:
                        continue
                    try:
                        visit(nested)
                    finally:
                        os.close(nested)
                else:
                    allocated += info.st_blocks * 512
    root_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(root_fd)
    finally:
        os.close(root_fd)
    return {'StateAllocatedBytes': allocated}


def counts(config):
    path = Path(config['state']) / 'relay.sqlite'
    require(stat.S_ISREG(path.lstat().st_mode))
    # mode=ro + query_only: never initialize Relay or its native Casita store.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        revisions, events = db.execute(
            'SELECT (SELECT count(*) FROM revisions), (SELECT count(*) FROM events)').fetchone()
        require(type(revisions) is int and revisions >= 0 and type(events) is int and events >= 0)
        return {'RetainedRevisions': revisions, 'RetainedEvents': events}


def sample(config):
    validate_config(config)
    result = {'Readiness': 0, 'MonitorComplete': 1}
    try:
        readiness(config)
        result['Readiness'] = 1
    except Exception:
        # No raw errors, HTTP bodies, credentials or filenames in diagnostics.
        pass
    for collector in (lambda: filesystem(config), lambda: filesystem(config, 'spool'),
                      lambda: state_size(config), lambda: counts(config)):
        try:
            result.update(collector())
        except Exception:
            result['MonitorComplete'] = 0
    try:
        result['StoragePressure'] = storage_pressure(config, result)
    except KeyError:
        result['MonitorComplete'] = 0
    return result


def publish(config, result, client=None):
    validate_config(config)
    require(set(result) <= set(METRICS) and {'Readiness', 'MonitorComplete'} <= set(result))
    require(all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                for value in result.values()))
    require(all(result[name] in (0, 1) for name in ('Readiness', 'MonitorComplete', 'StoragePressure')
                if name in result))
    if client is None:
        import boto3  # Optional operator dependency. No AWS access in normal demos/tests.
        from botocore.config import Config
        client = boto3.Session(region_name=config['region']).client('cloudwatch', config=Config(
            connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 2, 'mode': 'standard'}))
    client.put_metric_data(Namespace=config['namespace'], MetricData=[{
        'MetricName': name, 'Value': value, 'Unit': METRICS[name],
        'Dimensions': [{'Name': 'Relay', 'Value': config['relay_name']}],
    } for name, value in sorted(result.items())])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--publish', action='store_true', help='explicitly submit aggregate CloudWatch metrics')
    args = parser.parse_args()
    try:
        config = validate_config(private_json(args.config, os.getuid()))
        result = sample(config)
        if args.publish:
            publish(config, result)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['Readiness'] and result['MonitorComplete'] else 1
    except Exception:
        print('Relay monitoring failed; no private diagnostics emitted.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
