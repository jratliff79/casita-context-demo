#!/usr/bin/env python3
"""Replay the recorded public missing-context review without invoking a model."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import authentication as auth
import context_request as request_api
import demo
import git_diff_example as public
import git_diff_review as diff
import git_review as review

SOURCE = json.loads((Path(__file__).parent / 'fixtures/context-request-source.json').read_bytes())
ARTIFACTS = {'initial_report': 'context-request-initial-report.json',
             'request': 'context-request-recorded-request.json',
             'supplement_report': 'context-request-supplement-report.json',
             'assessment': 'context-request-assessment.json'}


def recorded_artifacts():
    values = {}
    for name, filename in ARTIFACTS.items():
        limit = request_api.REQUEST_LIMIT if name == 'request' else demo.MAX_BYTES
        raw = auth.read_regular(Path(__file__).parent / 'docs' / filename, limit)
        if demo.digest(raw) != SOURCE['artifact_sha256'][name]:
            raise ValueError('recorded ' + name + ' differs from pinned artifact')
        values[name] = raw
    return values


def run(args):
    # Check pinned recorded bytes before creating artifacts or keys.
    raw = recorded_artifacts()
    for name in ('initial', 'supplement'):
        public.verify_public_source(args.source, SOURCE[name])
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    keys = output / 'throwaway-keys'
    keys.mkdir(mode=0o700)
    receipt = dict(schema='casita-context-demo.context-request-replay.v1',
                   public_source_snapshot=True, recorded_ai_reports=True,
                   scripted_report=False, fresh_ai_review=False,
                   received_code_executed=False, execution_attested=False,
                   review_quality_verified=False, os_sandbox_enforced=False,
                   patch_applied=False, negative_controls={})

    def role(mode, name, **kwargs):
        options = {k: None for k in ('source', 'spec', 'observation', 'signing_key',
                   'archive', 'pins', 'signature', 'allowed_signers', 'signer',
                   'context', 'report', 'original')}
        options.update(kwargs)
        return diff.run(argparse.Namespace(mode=mode, output=output / name,
                                          casita=args.casita, **options))

    def transfer(name, identity):
        folder = output / name
        return dict(archive=folder / 'handoff.casitar', pins=folder / 'pins.json',
                    signature=folder / 'pins.sig',
                    allowed_signers=output / (identity + '-allowed-signers'),
                    signer='recorded-context-request-' + identity)

    def report_return(name):
        artifact = 'initial_report' if name == 'initial' else 'supplement_report'
        path = output / (name + '-report.json')
        path.write_bytes(raw[artifact])
        role('return', name + '-returned', context=output / (name + '-receiver'),
             report=path, signing_key=keys / 'controller')
        return role('verify', name + '-verified', source=args.source,
                    original=output / (name + '-sender'),
                    **transfer(name + '-returned', 'controller'))

    try:
        for identity, namespace in (('sender', 'review-input'), ('controller', 'review-result')):
            auth.make_demo_key(keys / identity)
            auth.provision_demo_signer(keys / identity, output / (identity + '-allowed-signers'),
                                       'recorded-context-request-' + identity, namespace)
        for name in ('initial', 'supplement'):
            (output / (name + '-spec.json')).write_bytes(demo.canonical(SOURCE[name]['spec']))
        observation = output / 'initial-observation.json'
        observation.write_bytes(demo.canonical(SOURCE['initial']['observation']))
        parent = role('prepare', 'initial-sender', source=args.source,
                      spec=output / 'initial-spec.json', observation=observation,
                      signing_key=keys / 'sender')
        received_parent = role('receive', 'initial-receiver', **transfer('initial-sender', 'sender'))
        initial = report_return('initial')
        request = review.parse_json(raw['request'])
        context = output / 'initial-receiver/context'
        manifest, _ = diff.verify_context(context, parent['pins']['content_id'])
        try:
            request_api.validate_approval(SOURCE['initial']['spec'], request, manifest)
        except ValueError as error:
            if 'explicit approved spec' not in str(error):
                raise
            receipt['negative_controls']['unapproved_source'] = True
        else:
            raise ValueError('unapproved supplementary source accepted')
        request_api.preview_response(args.source, context, parent['pins'], request,
                                     SOURCE['supplement']['spec'], output / 'preview')
        supplement = role('prepare', 'supplement-sender', source=args.source,
                          spec=output / 'preview/spec.json', observation=output / 'preview/observation.json',
                          signing_key=keys / 'sender')
        received = role('receive', 'supplement-receiver', **transfer('supplement-sender', 'sender'))
        binding = request_api.verify_response(context, parent['pins'], request,
                        output / 'supplement-receiver/context', supplement['pins'])
        final = report_return('supplement')
        for field in ('context_id', 'base_commit', 'head_commit', 'context_directory_key'):
            bad = copy.deepcopy(request)
            bad[field] = 'wrong'
            try:
                request_api.validate_request(bad, context, parent['pins'])
            except ValueError:
                receipt['negative_controls']['wrong_' + field] = True
            else:
                raise ValueError('wrong request binding accepted')
        try:
            role('verify', 'rejected-supplement-report-for-parent', source=args.source,
                 original=output / 'initial-sender', **transfer('supplement-returned', 'controller'))
        except ValueError as error:
            if 'original Git diff context' not in str(error):
                raise
            receipt['negative_controls']['signed_report_for_other_context'] = True
        else:
            raise ValueError('supplement report accepted for initial capsule')
        reports = {k: review.parse_json(raw[k]) for k in ('initial_report', 'supplement_report')}
        receipt.update(ok=True, binding=binding, source_repository=SOURCE['repository'],
                       source_license=SOURCE['license'],
                       base_commit=SOURCE['initial']['spec']['base_commit'],
                       head_commit=SOURCE['initial']['spec']['head_commit'],
                       input_signatures_verified=received_parent['signature_verified'] and received['signature_verified'],
                       returned_signatures_verified=initial['signature_verified'] and final['signature_verified'],
                       original_git_verified=initial['original_git_verified'] and final['original_git_verified'],
                       citations_verified=initial['citations_verified'] and final['citations_verified'],
                       initial_finding_count=initial['finding_count'], final_finding_count=final['finding_count'],
                       initial_citation_count=sum(len(f['citations']) for f in reports['initial_report']['findings']),
                       final_citation_count=sum(len(f['citations']) for f in reports['supplement_report']['findings']))
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        shutil.rmtree(keys)
        receipt['throwaway_private_keys_removed'] = True
        (output / 'receipt.json').write_bytes(demo.canonical(receipt))
    print('PASS: recorded public context request, approved supplement and original-Git report returns')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--casita', default='casita')
    parser.add_argument('--output', type=Path, required=True)
    try:
        os.umask(0o077)
        run(parser.parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print('Context-request replay failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
