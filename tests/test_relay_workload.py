"""Independent contracts for paced, distributed synthetic two-operator qualification."""
import copy
import io
import json
import math
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

import relay_workload as workload


def conflict(code=409):
    return HTTPError('unused', code, 'synthetic', {}, io.BytesIO(b'{}'))


class WorkloadTests(unittest.TestCase):
    def test_full_arrivals_are_independent_and_end_aged_store_at_one_thousand(self):
        plan = workload.make_plan(initial_revisions=840)
        self.assertIn('clients', plan)
        self.assertEqual(plan['final_revisions'], 1000)
        self.assertTrue(plan['full_profile'])
        for name, offset in [('alpha', 0), ('beta', 30)]:
            jobs = plan['clients'][name]
            pubs = [j for j in jobs if j['operation'] == 'publish']
            normal = [j for j in pubs if j['phase'] == 'normal']
            burst = [j for j in pubs if j['phase'] == 'burst']
            self.assertEqual([j['at'] for j in normal], list(range(offset, 3600, 60)))
            self.assertEqual([j['at'] for j in burst], list(range(3600, 4200, 30)))
            self.assertEqual(len({j['logical_id'] for j in pubs}), 80)
            self.assertEqual(len([j for j in jobs if j['operation'] == 'consult']), 80)
            self.assertEqual(len([j for j in jobs if j['operation'] == 'updates']), 840)
            self.assertEqual({j['task'] for j in pubs}, {f'task-{name}-0', f'task-{name}-1'})
        a = [j for j in plan['clients']['alpha'] if j['operation'] == 'publish' and j['phase'] == 'burst']
        b = [j for j in plan['clients']['beta'] if j['operation'] == 'publish' and j['phase'] == 'burst']
        self.assertEqual([j['base'] for j in a], list(range(960, 1000, 2)))
        self.assertEqual([j['base'] for j in a], [j['base'] for j in b])

    def test_short_smoke_profile_cannot_claim_the_full_matrix(self):
        plan = workload.make_plan(30, 20, 10, 12)
        self.assertIn('full_profile', plan)
        self.assertFalse(plan['full_profile'])
        self.assertEqual(plan['final_revisions'], 26)

    def test_plan_rejects_unbounded_nonfinite_or_inconsistent_inputs(self):
        for args in [(3600, 600, 0, 6), (3600, 600, math.nan, 6),
                     (3601, 600, 60, 6), (3600, 601, 60, 6),
                     (3600, 600, 60, 841), (3600, 600, 60, True),
                     (0, 600, 60, 6), (3600, 600, 60, 5)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                workload.make_plan(*args)

    def test_clock_translation_uses_the_best_bounded_round_trip(self):
        value = workload.clock_mapping([(10, 110.5, 11), (20, 120.01, 20.02)])
        self.assertIn('offset', value)
        self.assertAlmostEqual(value['offset'], 100)
        self.assertAlmostEqual(value['uncertainty'], .01)

    def test_clock_translation_rejects_unbounded_or_nonfinite_samples(self):
        for samples in [[], [(1, 2, 0)], [(0, 5, 1)], [(0, math.inf, .01)]]:
            with self.subTest(samples=samples), self.assertRaises(ValueError):
                workload.clock_mapping(samples)

    def job(self, burst=True):
        return dict(logical_id='alpha-burst-0', base=12, task='task-alpha-0',
                    version=4, payload_bytes=4096, phase='burst' if burst else 'normal')

    def test_conflict_retry_has_a_new_attempt_id_and_preserves_logical_content(self):
        request = Mock(side_effect=[conflict(), {'revision': 13},
                                   {'pin': {'revision': 14}, 'cursor': 16}])
        result = workload.publish_logical(self.job(), request)
        self.assertEqual(request.call_count, 3)
        first, refresh, retry = request.call_args_list
        self.assertEqual(refresh.args, ('context', {}))
        self.assertEqual(first.args[1]['checkpoint'], retry.args[1]['checkpoint'])
        self.assertNotEqual(first.args[1]['request_id'], retry.args[1]['request_id'])
        self.assertEqual([first.args[1]['expected_revision'], retry.args[1]['expected_revision']], [12, 13])
        self.assertEqual(result['statuses'], [409, 200])
        self.assertEqual(result['logical_id'], 'alpha-burst-0')
        self.assertEqual(result['pin']['revision'], 14)

    def test_conflict_retry_cannot_become_an_unbounded_loop(self):
        request = Mock(side_effect=[conflict(), {'revision': 13}, conflict()])
        with self.assertRaises(HTTPError):
            workload.publish_logical(self.job(), request)
        self.assertEqual(request.call_count, 3)

    def test_normal_conflict_or_unexpected_http_error_never_gets_retried(self):
        for job, code in [(self.job(False), 409), (self.job(), 503), (self.job(), 401)]:
            request = Mock(side_effect=conflict(code))
            with self.subTest(code=code), self.assertRaises(HTTPError):
                workload.publish_logical(job, request)
            self.assertEqual(request.call_count, 1)

    def test_conflict_refresh_must_match_the_other_writers_one_commit(self):
        request = Mock(side_effect=[conflict(), {'revision': 99}])
        with self.assertRaises(ValueError):
            workload.publish_logical(self.job(), request)
        self.assertEqual(request.call_count, 2)

    def test_success_must_acknowledge_exactly_one_expected_commit(self):
        request = Mock(return_value={'pin': {'revision': 99}, 'cursor': 16})
        with self.assertRaises(ValueError):
            workload.publish_logical(self.job(), request)

    def test_event_pages_are_contiguous_and_cursor_cannot_skip_or_regress(self):
        good = dict(events=[{'cursor': 8, 'kind': 'publish'}, {'cursor': 9, 'kind': 'publish'}], next_cursor=9)
        self.assertEqual(workload.advance_cursor(good, 7), 9)
        for page in [dict(good, next_cursor=10), dict(events=[{'cursor': 9}], next_cursor=9),
                     dict(events=[], next_cursor=8), dict(events=[{'cursor': 7}], next_cursor=7)]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                workload.advance_cursor(page, 7)

    def test_ssh_is_strict_loopback_forwarding_without_credential_arguments(self):
        client = dict(host='synthetic-client', port=2222, checkout='/tmp/synthetic checkout',
                      python='python3', forward_port=18765)
        args = workload.ssh_argv(client, 12345)
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('ExitOnForwardFailure=yes', args)
        self.assertIn('BatchMode=yes', args)
        self.assertIn('127.0.0.1:18765:127.0.0.1:12345', args)
        self.assertNotIn('StrictHostKeyChecking=no', args)
        self.assertIn("'/tmp/synthetic checkout'", args[-1])

    def test_ssh_rejects_option_like_hosts_and_invalid_ports(self):
        for host, port in [('-oProxyCommand=bad', 22), ('good', 0), ('good', True)]:
            with self.subTest(host=host), self.assertRaises(ValueError):
                workload.ssh_argv(dict(host=host, port=port, checkout='/tmp/fixture',
                                      python='python3', forward_port=1234), 12345)

    def test_worker_source_or_binary_drift_is_rejected_before_credentials_are_sent(self):
        good = dict(source_sha256={'a.py': 'a'*64}, binary_sha256='b'*64,
                    system='Linux', machine='x86_64', hostname='synthetic')
        for field in ('source_sha256', 'binary_sha256'):
            wrong = copy.deepcopy(good)
            wrong[field] = {'a.py': 'c'*64} if field == 'source_sha256' else 'c'*64
            with self.subTest(field=field), self.assertRaises(ValueError):
                workload.validate_hello(wrong, good['source_sha256'], good['binary_sha256'])

    def test_backup_overlap_requires_definite_timed_work_not_just_a_running_phase(self):
        observations = [dict(start=10, end=11, uncertainty=.01),
                        dict(start=12, end=13, uncertainty=.01)]
        self.assertEqual(workload.overlap_count(observations, 10.5, 11.5), 1)
        self.assertEqual(workload.overlap_count(observations, 11.1, 11.9), 0)
        self.assertEqual(workload.overlap_count([dict(start=10, end=11, uncertainty=1)], 10, 11), 0)

    def test_aged_history_is_built_through_real_publish_and_memory_operations(self):
        calls, revision, cursor = [], 0, 0
        def handle(token, operation, body):
            nonlocal revision, cursor
            calls.append((operation, copy.deepcopy(body)))
            cursor += 1
            if operation != 'propose':
                revision += 1
            return dict(pin={'revision': revision}, cursor=cursor, proposal_id='synthetic-proposal')
        service = Mock(); service.workspace = 'synthetic'; service.handle.side_effect = handle
        result = workload.seed_history(service, 'synthetic-token', 12)
        self.assertEqual(revision, 12)
        self.assertEqual(cursor, 14)
        self.assertEqual([op for op, _ in calls].count('publish'), 10)
        self.assertEqual([op for op, _ in calls].count('propose'), 2)
        self.assertEqual([op for op, _ in calls].count('accept'), 2)
        self.assertEqual(result['revision'], 12)
        self.assertEqual(len(result['tasks']), 4)


if __name__ == '__main__':
    unittest.main()
