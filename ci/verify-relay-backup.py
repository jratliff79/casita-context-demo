#!/usr/bin/env python3
"""Native synthetic backup capture and damaged-history rejection; no AWS/systemd."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import context_relay as relay
import relay_backup as backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--casita', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    state, app, spool = (root / name for name in ('state', 'app', 'spool'))
    app.mkdir(); spool.mkdir()
    (app / 'synthetic-runtime.txt').write_text('SYNTHETIC PINNED RUNTIME\n')
    unit = root / 'synthetic.service'; unit.write_text('SYNTHETIC UNIT\n')
    index = root / 'runtime.json'
    backup.write_json(index, backup.inventory(app, 100_000))
    binary = args.casita.resolve()
    config = dict(state=str(state), app=str(app), spool=str(spool), binary=str(binary),
                  binary_pin=backup.file_pin(binary, 512*1024*1024), service_unit=str(unit),
                  service_unit_pin=backup.file_pin(unit, 100_000),
                  runtime_inventory=str(index), max_bytes=512*1024*1024,
                  service='synthetic.service', source_commit='a'*40, casita_commit='b'*40)
    relay.initialize(state, 'synthetic-team', 'jp', ['cj'])
    service = relay.Relay(state, binary)
    token = json.loads((state/'credentials/jp.json').read_text())['token']
    text = 'SYNTHETIC NOTE\n'
    context = dict(task_id='synthetic-task', summary='Synthetic capture qualification',
                   completed=[], pending=['review'], blockers=[], source_revision='a'*40,
                   files=[dict(path='note.txt', text=text, sha256=relay.digest(text.encode()))])
    pins = []
    for revision in range(2):
        result = service.handle(token, 'publish', dict(workspace='synthetic-team',
                        request_id='synthetic-publish-'+str(revision), expected_revision=revision,
                        checkpoint=context))
        pins.append(result['pin'])
    cj_token = json.loads((state/'credentials/cj.json').read_text())['token']
    proposal = service.handle(cj_token, 'propose', dict(
        workspace='synthetic-team', request_id='synthetic-proposal', expected_revision=2,
        task_id='synthetic-task', scope='workspace', entry_id='synthetic-memory',
        statement='Synthetic backup qualification includes owner-reviewed memory.',
        citation=dict(path='note.txt', sha256=relay.digest(text.encode()),
                      start_line=1, end_line=1, excerpt=['SYNTHETIC NOTE'])))
    result = service.handle(token, 'accept', dict(
        workspace='synthetic-team', request_id='synthetic-accept', expected_revision=2,
        proposal_id=proposal['proposal_id']))
    pins.append(result['pin'])
    calls = []
    def run(*argv):
        if argv[0] == '/usr/bin/systemctl':
            calls.append(argv[1])
            return b''  # Explicit synthetic service boundary; native commands are real.
        return backup.command(*argv)
    manifest = backup.capture(config, spool/'staging-valid', run=run, ready=lambda c: None)
    assert manifest['revision'] == 3 and manifest['pin'] == pins[-1]
    assert calls == ['is-active', 'stop', 'start']
    # SQLite and current head remain valid while a historical archive is absent.
    historical = state/'snapshots'/(pins[0]['archive_sha256']+'.casitar')
    historical.unlink()
    try:
        backup.capture(config, spool/'staging-damaged', run=run, ready=lambda c: None)
    except backup.BackupError as error:
        assert str(error) == 'referenced archive missing or damaged'
    else:
        raise AssertionError('missing historical archive was accepted')
    assert calls[-1] == 'start'
    backup.cleanup(spool)
    backup.write_json(root/'receipt.json', dict(schema='synthetic-backup-check.v1',
                      native_capture_passed=True, proposal_acceptance_captured=True,
                      historical_archive_rejected=True,
                      synthetic_service_only=True, aws_accessed=False))
    print('Synthetic native capture and historical archive rejection passed.')


if __name__ == '__main__':
    main()
