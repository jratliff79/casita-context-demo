"""Client timings are bounded evidence, never a substitute for whole-operation gates."""
import copy
import unittest

import relay_workload_plan as plan


class NativeTimingProtocol(unittest.TestCase):
    def check(self, records, native_ms=1):
        check = getattr(plan, 'validate_native_timings', None)
        self.assertIsNotNone(check, 'bounded native timing validation is missing')
        return check(records, native_ms)

    def records(self):
        return [dict(stage=stage, start_monotonic_ns=1000 + i * 100,
                     elapsed_ns=50, children_cpu_ns=30, outcome='ok')
                for i, stage in enumerate(['init', 'archive_verify', 'archive_import', 'checkout', 'fsck'])]

    def test_complete_five_command_chain_is_accepted(self):
        self.check(self.records())

    def test_missing_reordered_duplicate_failed_or_extra_command_is_rejected(self):
        good = self.records()
        bad = [good[:-1], list(reversed(good)), good + [good[-1]],
               [dict(x, stage='checkout') for x in good],
               [dict(x, outcome='error') for x in good]]
        for records in bad:
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.check(records)

    def test_private_fields_and_noninteger_negative_or_boolean_times_are_rejected(self):
        good = self.records()
        for change in [dict(argv=['/private/secret']), dict(elapsed_ns=-1),
                       dict(elapsed_ns=True), dict(children_cpu_ns=float('inf')),
                       dict(start_monotonic_ns=-1), dict(stage='PRIVATE')]:
            bad = copy.deepcopy(good); bad[0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check(bad)

    def test_overlapping_or_out_of_envelope_commands_are_rejected(self):
        bad = self.records(); bad[1]['start_monotonic_ns'] = 1001
        with self.assertRaises(ValueError):
            self.check(bad)
        with self.assertRaises(ValueError):
            self.check(self.records(), native_ms=.0001)


if __name__ == '__main__':
    unittest.main()
