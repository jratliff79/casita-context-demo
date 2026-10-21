"""Permanent fresh-store native restore curve with unchanged integrity gates."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time

import context_relay as relay
import relay_backup as backup
from relay_fsck_benchmark import CONTROLS, corruption_controls, make_state, verify_history, write
from relay_load import latency_summary, require
from relay_workload_plan import source_pins, validate_native_timings


def correctness_gate(record):
    require(all(record.get(k) is True for k in (
        'all_cases_recorded', 'all_restore_commands_verified',
        'all_retained_snapshots_verified', 'full_fsck_on_every_publication')),
        'restore benchmark correctness incomplete')
    require(set(record.get('corruption_controls', {})) == CONTROLS
            and all(v is True for v in record['corruption_controls'].values()),
            'restore benchmark corruption controls incomplete')


def receive_trial(response, binary, output, records):
    # Retain elapsed time and bounded native stages even if the call or a
    # later gate fails. Never retain exception messages, argv or payloads.
    item = dict(validation_passed=False, native_stages=[])
    records.append(item)
    start = time.monotonic()
    try:
        snapshot = relay.receive(response, binary, output, native_timing=item['native_stages'].append)
    finally:
        item['milliseconds'] = 1000 * (time.monotonic() - start)
    validate_native_timings(item['native_stages'], item['milliseconds'])
    require(relay.digest(relay.canonical(snapshot)) == response['pin']['snapshot_sha256'],
            'restored snapshot differs from the acknowledged pin')
    item['validation_passed'] = True
    return item


def compare_case(root, binaries, horizon, repeats, result):
    root.mkdir(mode=0o700)
    result.update(initial_revisions=horizon, status='running', repeats=repeats,
                  samples={role: [] for role in binaries})
    try:
        state = root / 'fixture'
        make_state(state, binaries['candidate'], horizon)
        for binary in binaries.values():
            verify_history(state, binary)
        service = relay.Relay(state, binaries['candidate'])
        token = backup.private_json(state / 'credentials/alpha.json')['token']
        response = service.handle(token, 'context', dict(workspace=service.workspace))
        require(response['revision'] == horizon, 'benchmark fixture head changed')
        for trial in range(repeats):
            order = list(binaries) if trial % 2 == 0 else list(reversed(binaries))
            for role in order:
                with tempfile.TemporaryDirectory(dir=root) as temporary:
                    sample = receive_trial(response, binaries[role], Path(temporary) / 'received',
                                           result['samples'][role])
                    sample.update(trial=trial, order=order.index(role))
        for binary in binaries.values():
            verify_history(state, binary)
        require(all(len(rows) == repeats and all(r['validation_passed'] for r in rows)
                    for rows in result['samples'].values()), 'missing or failed restore trial')
        result.update(status='passed', all_restore_commands_verified=True,
            all_retained_snapshots_verified=True, full_fsck_on_every_publication=True,
            summaries={role: latency_summary(rows) for role, rows in result['samples'].items()})
    finally:
        shutil.rmtree(root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    binaries = {name: getattr(args, name).resolve() for name in ('baseline', 'candidate')}
    require(all(p.is_file() and os.access(p, os.X_OK) for p in binaries.values()), 'native binaries unavailable')
    sources = source_pins()
    pins = {name: relay.digest(path.read_bytes()) for name, path in binaries.items()}
    runner_pin = relay.digest(Path(__file__).read_bytes())
    horizons, repeats = ([6, 12], 2) if args.smoke else ([6, 34, 118, 166, 840], 4)
    record = dict(schema='casita-native-restore-benchmark.v1', ok=False, synthetic=True,
        local_only=True, recorded_at=datetime.now(timezone.utc).isoformat(),
        machine=platform.machine(), python=platform.python_version(), source_sha256=sources,
        binary_sha256=pins, runner_sha256=runner_pin, horizons=horizons, repeats=repeats,
        cases=[], corruption_attempts={}, capacity_qualified=False, aws_accessed=False,
        cache_state_controlled=False, paced_workload_tested=False,
        timing_scope='Complete fresh-store receive, including every verification and observation overhead',
        children_cpu_scope='Process-wide child CPU deltas; not unique physical memory or per-thread CPU',
        sample_limit='Small repeated samples are descriptive, not a full-workload p95 or growth qualification')
    try:
        record['corruption_controls'] = corruption_controls(output / 'controls', binaries,
                                                          record['corruption_attempts'])
        for horizon in horizons:
            case = dict(initial_revisions=horizon, status='starting')
            record['cases'].append(case)
            compare_case(output / ('case-' + str(horizon)), binaries, horizon, repeats, case)
            write(output / 'partial-results.json', record)
        require(source_pins() == sources
                and {n: relay.digest(p.read_bytes()) for n, p in binaries.items()} == pins
                and relay.digest(Path(__file__).read_bytes()) == runner_pin, 'source or binary changed')
        for field in ('all_restore_commands_verified', 'all_retained_snapshots_verified',
                      'full_fsck_on_every_publication'):
            record[field] = all(case[field] for case in record['cases'])
        record['all_cases_recorded'] = [c['initial_revisions'] for c in record['cases']] == horizons
        correctness_gate(record)
        record['ok'] = True
        write(output / 'receipt.json', record)
        print('PASS: full fresh-store native restore and corruption gates; local microbenchmark only', flush=True)
    except BaseException:
        for case in record['cases']:
            if case['status'] != 'passed':
                case['status'] = 'failed'
        write(output / 'partial-results.json', record)
        raise


if __name__ == '__main__':
    main()
