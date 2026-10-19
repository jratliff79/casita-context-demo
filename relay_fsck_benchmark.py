"""Full native fsck cost with unchanged history and fail-closed publication gates."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time

import context_relay as relay
import relay_backup as backup
from demo import file_map
from relay_load import fixture, require, latency_summary, state_measurement
from relay_workload_plan import seed_history, source_pins


REPORT = (
    r'physical repair: revision [^;\n]+; checked \d+ payload\(s\), \d+ byte\(s\), \d+ outboard\(s\)',
    r'traversal-spill-files-opened \d+; traversal-spill-peak-bytes \d+',
    r'physical-fsck-nanos \d+',
    r'revision [^;\n]+; checked \d+ root\(s\), \d+ object\(s\), \d+ payload\(s\)',
    r'traversal-spill-files-opened \d+; traversal-spill-peak-bytes \d+',
    r'logical-fsck-nanos \d+',
)
CONTROLS = {'old_pack_corrupt', 'old_pack_missing', 'latest_pack_corrupt', 'retained_archive_corrupt'}


def semantic_report(stdout):
    lines = stdout.splitlines()
    if len(lines) != len(REPORT) or any(re.fullmatch(pattern, line) is None for pattern, line in zip(REPORT, lines)):
        raise ValueError('full physical/logical fsck report is missing, damaged or contains findings')
    return tuple(line for line in lines if not re.fullmatch(r'(physical|logical)-fsck-nanos \d+', line))


def fsck_argv(binary, store):
    return [str(binary), '--repository', str(store), 'fsck', '--dry-run']


def checked_artifact(artifact):
    profile = artifact.get('profile', {})
    if (artifact.get('reason') != 'compiler-artifact' or artifact.get('target', {}).get('name') != 'casita'
            or artifact.get('target', {}).get('kind') != ['bin'] or not artifact.get('executable')
            or profile.get('opt_level') != '3' or profile.get('debug_assertions') is not True
            or profile.get('overflow_checks') is not True or profile.get('test') is not False):
        raise ValueError('native artifact must retain checked optimized profile')
    return artifact['executable']


def correctness_gate(record):
    if (any(record.get(key) is not True for key in ('healthy_reports_equal', 'all_retained_snapshots_verified',
            'full_fsck_on_every_publication', 'all_cases_recorded'))
            or set(record.get('corruption_controls', {})) != CONTROLS
            or any(value is not True for value in record['corruption_controls'].values())):
        raise ValueError('full fsck/history/corruption gates incomplete')


def write(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    require(len(raw) <= 2*1024**2, 'numeric benchmark receipt exceeds bound')
    pending = path.with_suffix(path.suffix + '.pending')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def command(argv):
    return subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=90)


def scan(binary, store, on_complete=None):
    before = time.monotonic()
    result = command(fsck_argv(binary, store))
    measured=dict(milliseconds=1000*(time.monotonic()-before),validation_passed=False)
    if on_complete is not None:on_complete(measured)
    require(result.returncode == 0, 'full fsck rejected healthy fixture')
    semantic = semantic_report(result.stdout)
    times = {part: int(re.search(part+r'-fsck-nanos (\d+)', result.stdout).group(1))
             for part in ('physical', 'logical')}
    measured.update(physical_ns=times['physical'],logical_ns=times['logical'])
    return semantic, measured


def verify_history(state, binary):
    with relay.connect(state, readonly=True) as db:
        require(db.execute('pragma integrity_check').fetchone()[0] == 'ok', 'SQLite damaged')
        backup.verify_schema(db); backup.verify_events(db)
        backup.verify_transitions(db, backup.verify_auxiliary_json(db))
        backup.verify_snapshots(db, state, binary)


def make_state(state, binary, horizon, canary=False):
    relay.initialize(state, 'synthetic-fsck-benchmark', 'alpha', ['beta'])
    service = relay.Relay(state, binary)
    old_pack, latest_pack, previous_packs = None, None, set()
    def packs():
        return set((state/'store/blobs/bao-packs/b3').glob('*/*'))
    if canary:
        folder = state.parent/'synthetic-canary'; folder.mkdir(mode=0o700)
        (folder/'canary.txt').write_bytes(b'SYNTHETIC OLD HISTORY CANARY\n' + bytes(range(256))*128)
        service.store.run('import', folder, '--root', 'synthetic/history-canary')
        candidates = sorted(packs()); require(candidates, 'historical canary pack missing')
        old_pack = candidates[0].relative_to(state)
    original, calls = service.store.run, Counter()
    def full_native(*args):
        if args[0] == 'fsck':
            require(args == ('fsck','--dry-run'), 'physical fsck scope changed')
        calls[args[0]] += 1
        return original(*args)
    service.store.run = full_native
    def progress(revision):
        nonlocal previous_packs, latest_pack
        if canary and revision == horizon-1:
            previous_packs = packs()
        if canary and revision == horizon:
            candidates = sorted(packs()-previous_packs)
            require(candidates, 'latest snapshot pack missing')
            latest_pack = candidates[0].relative_to(state)
        if revision % 100 == 0:
            print('Synthetic API seed acknowledged ' + str(revision), flush=True)
    credential = backup.private_json(state/'credentials/alpha.json')
    seed_history(service, credential['token'], horizon, on_progress=progress)
    require(calls['fsck'] == horizon and calls['import'] == horizon and calls['checkout'] == horizon,
            'per-publication full fsck or native verification count changed')
    return old_pack, latest_pack


def compare_case(root, binaries, horizon, repeats, result):
    root.mkdir(mode=0o700)
    original = root/'fixture'
    result.update(initial_revisions=horizon, repeats=repeats, status='running',
        samples={role:dict(fsck=[], publication=[]) for role in binaries})
    make_state(original, binaries['candidate'], horizon)
    expected = dict(revisions=horizon, events=horizon+2, requests=horizon+2, proposals=2)
    require(state_measurement(original)['counts'] == expected, 'API-built fixture accounting changed')
    for binary in binaries.values():
        verify_history(original, binary)
    reference, observations = None, result['samples']
    for iteration in range(repeats):
        order = list(binaries) if iteration % 2 == 0 else list(reversed(binaries))
        for role in order:
            with tempfile.TemporaryDirectory(dir=root) as temp:
                state = Path(temp)/'relay'; shutil.copytree(original, state)
                def completed_scan(measured):
                    measured.update(iteration=iteration,order=order.index(role))
                    observations[role]['fsck'].append(measured)
                report, measured = scan(binaries[role], state/'store',on_complete=completed_scan)
                reference = report if reference is None else reference
                require(report == reference, 'physical/logical healthy reports differ')
                require(int(re.search(r'checked (\d+) root', report[2]).group(1)) == horizon, 'retained root count differs')
                measured['validation_passed']=True
            # fsck --dry-run still writes process-pin metadata. Publication
            # gets a separate fixture copy that this standalone scan never saw.
            with tempfile.TemporaryDirectory(dir=root) as temp:
                state = Path(temp)/'relay'; shutil.copytree(original, state)
                service = relay.Relay(state, binaries[role]); credential=backup.private_json(state/'credentials/alpha.json')
                native_calls, original_run = [], service.store.run
                def observed_native(*args):
                    native_calls.append(args[0])
                    if args[0] == 'fsck':require(args == ('fsck','--dry-run'), 'fsck scope reduced')
                    return original_run(*args)
                service.store.run = observed_native
                start = time.monotonic()
                response = service.handle(credential['token'], 'publish', dict(workspace=service.workspace,
                    request_id='synthetic-benchmark-'+str(iteration), expected_revision=horizon,
                    checkpoint=fixture('task-alpha-0', 'benchmark-'+str(iteration), 4096)))
                elapsed = 1000*(time.monotonic()-start)
                observed=dict(milliseconds=elapsed,iteration=iteration,order=order.index(role),validation_passed=False)
                observations[role]['publication'].append(observed)
                require(native_calls == ['import','root','archive','fsck','checkout'], 'native publication order changed')
                require(response['pin']['revision'] == horizon+1, 'publication head differs')
                require(state_measurement(state)['counts'] == dict(expected, revisions=horizon+1, events=horizon+3, requests=horizon+3),
                        'publication changed history accounting')
                if iteration == repeats-1:verify_history(state, binaries[role])
                observed['validation_passed']=True
    result.update(status='passed',
        summaries={role:{kind:latency_summary(rows) for kind,rows in kinds.items()} for role,kinds in observations.items()},
        healthy_reports_equal=True, all_retained_snapshots_verified=True, full_fsck_on_every_publication=True)
    shutil.rmtree(root)
    return result


def corruption_controls(root, binaries, outcomes):
    root.mkdir(mode=0o700); original=root/'fixture'
    old_pack, latest_pack = make_state(original, binaries['candidate'], 6, canary=True)
    require(old_pack != latest_pack, 'old and latest controls share one pack')
    for binary in binaries.values():verify_history(original,binary)
    for kind in sorted(CONTROLS):
        outcomes[kind] = {}
        for role,binary in binaries.items():
            outcomes[kind][role] = False
            with tempfile.TemporaryDirectory(dir=root) as temp:
                state=Path(temp)/'relay';shutil.copytree(original,state)
                if kind=='retained_archive_corrupt':
                    with relay.connect(state,readonly=True) as db:
                        old_pin=json.loads(db.execute('select pin from revisions where revision=1').fetchone()[0])
                    (state/'snapshots'/(old_pin['archive_sha256']+'.casitar')).write_bytes(b'SYNTHETIC CORRUPTION')
                    scan(binary,state/'store')  # Native store remains healthy; archive audit must still reject.
                    rejected=False
                    try:verify_history(state,binary)
                    except backup.BackupError as error:
                        rejected = str(error) == 'referenced archive missing or damaged'
                    require(rejected,'historical archive corruption accepted')
                    outcomes[kind][role]=True
                    continue
                target=state/(latest_pack if kind=='latest_pack_corrupt' else old_pack)
                if kind=='old_pack_missing':target.unlink()
                else:target.write_bytes(b'SYNTHETIC CORRUPTION')
                credential=backup.private_json(state/'credentials/alpha.json');service=relay.Relay(state,binary)
                with relay.connect(state,readonly=True) as db:
                    current=json.loads(db.execute('select pin from revisions where revision=6').fetchone()[0])
                if kind.startswith('old_'):
                    destination=Path(temp)/'latest-verified'
                    service.store.run('checkout',current['directory_key'],destination,'--no-root')
                    require(file_map(destination)=={'snapshot.json':current['snapshot_sha256']}, 'old control also damaged latest head')
                require(command(fsck_argv(binary,state/'store')).returncode>0,
                        'full fsck accepted damage or terminated without a corruption result')
                before=state_measurement(state)['counts']; rejected=False
                failed_native, native_run = [], service.store.run
                def observed_failure(*args):
                    try:return native_run(*args)
                    except Exception:
                        failed_native.append(args[0]);raise
                service.store.run=observed_failure
                try:
                    service.handle(credential['token'],'publish',dict(workspace=service.workspace,request_id='synthetic-must-reject',
                        expected_revision=6,checkpoint=fixture('task-alpha-0','reject',4096)))
                except RuntimeError:rejected=True
                require(rejected and failed_native == ['fsck'] and state_measurement(state)['counts']==before,
                        'damaged history was not rejected by full publication fsck before commit')
                outcomes[kind][role]=True
    shutil.rmtree(root)
    require(all(set(rows)==set(binaries) and all(rows.values()) for rows in outcomes.values()),'corruption case missing')
    return {kind:all(rows.values()) for kind,rows in outcomes.items()}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True);parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args(argv);os.umask(0o077)
    output=args.output.resolve();output.mkdir(parents=True,mode=0o700,exist_ok=False)
    binaries={name:getattr(args,name).resolve() for name in ('baseline','candidate')}
    require(all(p.is_file() and os.access(p,os.X_OK) for p in binaries.values()),'native binaries unavailable')
    pins=source_pins();binary_pins={name:relay.digest(path.read_bytes()) for name,path in binaries.items()}
    runner_pin=relay.digest(Path(__file__).read_bytes())
    horizons,repeats=([6,12],2) if args.smoke else ([6,166,840],4)
    record=dict(schema='casita-relay-fsck-benchmark.v1',ok=False,synthetic=True,local_only=True,
        recorded_at=datetime.now(timezone.utc).isoformat(),source_sha256=pins,binary_sha256=binary_pins,
        machine=platform.machine(),python=platform.python_version(),horizons=horizons,repeats=repeats,cases=[],
        runner_sha256=runner_pin,corruption_attempts={},
        scan_publication_state_separate=True,cache_state_controlled=False,
        paced_workload_tested=False,capacity_qualified=False,aws_accessed=False)
    try:
        record['corruption_controls']=corruption_controls(output/'controls',binaries,record['corruption_attempts'])
        for horizon in horizons:
            case=dict(initial_revisions=horizon,status='starting');record['cases'].append(case)
            compare_case(output/('case-'+str(horizon)),binaries,horizon,repeats,case)
            write(output/'partial-results.json',record)
        require(source_pins()==pins and {n:relay.digest(p.read_bytes()) for n,p in binaries.items()}==binary_pins
                and relay.digest(Path(__file__).read_bytes())==runner_pin,'source or binary changed')
        for field in ('healthy_reports_equal','all_retained_snapshots_verified','full_fsck_on_every_publication'):
            record[field]=all(case[field] for case in record['cases'])
        record['all_cases_recorded']=[case['initial_revisions'] for case in record['cases']]==horizons
        correctness_gate(record);record['ok']=True
        write(output/'receipt.json',record)
        print('PASS: full physical/logical fsck, retained history and corruption controls; local only',flush=True)
    except BaseException:
        for case in record['cases']:
            if case['status']!='passed':case['status']='failed'
        write(output/'partial-results.json',record)
        print('FAIL: local fsck benchmark; retained failed/incomplete numeric evidence',flush=True)
        raise
