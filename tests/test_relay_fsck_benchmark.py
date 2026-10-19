"""Checked optimization must retain full scans and reject damaged old history."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEALTHY = '''physical repair: revision 12; checked 8 payload(s), 456 byte(s), 0 outboard(s)
traversal-spill-files-opened 0; traversal-spill-peak-bytes 0
physical-fsck-nanos 100
revision 12; checked 6 root(s), 8 object(s), 8 payload(s)
traversal-spill-files-opened 0; traversal-spill-peak-bytes 0
logical-fsck-nanos 200
'''


class CheckedFsckContract(unittest.TestCase):
    def module(self):
        self.assertTrue((ROOT / 'relay_fsck_benchmark.py').is_file(), 'permanent full-fsck benchmark unavailable')
        return importlib.import_module('relay_fsck_benchmark')

    def test_profile_optimizes_without_disabling_debug_or_overflow_checks(self):
        path = ROOT / 'ci/relay-checked-profile.toml'
        self.assertTrue(path.is_file(), 'checked optimization profile unavailable')
        # Assert the deliberately small complete config, independently of its loader.
        lines = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')]
        self.assertEqual(lines, ['[profile.relay-checked]', 'inherits = "dev"', 'opt-level = 3',
            'debug = 0', 'debug-assertions = true', 'overflow-checks = true', 'incremental = false'])

    def test_semantics_compare_every_report_line_except_explicit_timings(self):
        module = self.module()
        first = module.semantic_report(HEALTHY)
        self.assertEqual(first, module.semantic_report(HEALTHY.replace('nanos 100', 'nanos 999')))
        self.assertNotEqual(first, module.semantic_report(HEALTHY.replace('456 byte(s)', '457 byte(s)')))
        for raw in [HEALTHY.replace('logical-fsck-nanos 200\n', ''),
                    HEALTHY.replace('physical repair: ', 'audit: '),
                    HEALTHY + 'Corrupt MissingPayload blob:x missing\n']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                module.semantic_report(raw)

    def test_full_scan_argv_cannot_be_replaced_with_logical_only_audit(self):
        module = self.module()
        self.assertEqual(module.fsck_argv(Path('/synthetic/binary'), Path('/synthetic/store')),
                         ['/synthetic/binary', '--repository', '/synthetic/store', 'fsck', '--dry-run'])

    def test_checked_artifact_requires_actual_optimized_assertion_and_overflow_profile(self):
        module = self.module()
        artifact = dict(reason='compiler-artifact', target=dict(name='casita', kind=['bin']),
                        executable='/synthetic/relay-checked/casita', profile=dict(opt_level='3',
                        debuginfo=0, debug_assertions=True, overflow_checks=True, test=False))
        self.assertEqual(module.checked_artifact(artifact), '/synthetic/relay-checked/casita')
        for changes in [dict(debug_assertions=False), dict(overflow_checks=False), dict(opt_level='0'), dict(test=True)]:
            bad = dict(artifact, profile=dict(artifact['profile'], **changes))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                module.checked_artifact(bad)

    def test_benchmark_is_registered_in_all_and_persistent_ci_smoke(self):
        path = ROOT / 'benchmarks/manifest.json'
        self.assertTrue(path.is_file(), 'permanent benchmark corpus unavailable')
        manifest = json.loads(path.read_text())
        self.assertEqual(manifest['all'], ['relay-fsck'])
        self.assertEqual(manifest['cases']['relay-fsck']['horizons'], [6, 166, 840])
        self.assertTrue((ROOT / 'benchmark').is_file())
        self.assertIn('Checked relay fsck', (ROOT / '.github/workflows/ci.yml').read_text())

    def test_success_gate_requires_corruption_and_history_controls(self):
        module = self.module()
        record = dict(healthy_reports_equal=True, all_retained_snapshots_verified=True,
            full_fsck_on_every_publication=True, corruption_controls={
                'old_pack_corrupt': True, 'old_pack_missing': True,
                'latest_pack_corrupt': True, 'retained_archive_corrupt': True},
            all_cases_recorded=True)
        module.correctness_gate(record)
        for field in ['healthy_reports_equal', 'all_retained_snapshots_verified',
                      'full_fsck_on_every_publication', 'all_cases_recorded']:
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.correctness_gate(dict(record, **{field: False}))
        for name in record['corruption_controls']:
            bad = dict(record, corruption_controls=dict(record['corruption_controls'], **{name:False}))
            with self.subTest(name=name), self.assertRaises(ValueError):
                module.correctness_gate(bad)


if __name__ == '__main__':
    unittest.main()
