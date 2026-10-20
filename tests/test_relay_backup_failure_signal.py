"""The execute loop must not see a failure before original evidence is saved."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import relay_workload_controller as controller
from relay_workload_backup import BackupEvidence
from relay_workload_diagnostics import Diagnostics


class BackupFailureSignalTests(unittest.TestCase):
    def test_signal_is_published_after_primary_diagnostics_even_if_status_io_fails(self):
        record = getattr(controller, 'record_backup_failure', None)
        self.assertIsNotNone(record, 'backup failure must be recorded before publishing its signal')
        for broken_status in (False, True):
            with self.subTest(broken_status=broken_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); root.chmod(0o700)
                diagnostic = Diagnostics(root, enabled=False)
                evidence = BackupEvidence(diagnostic)
                class Signals(list):
                    def append(signals, value):
                        self.assertEqual(json.loads((root / 'backup-overlap.json').read_text())['error_type'],
                                         'OSError')
                        super().append(value)
                signals = Signals()
                try:
                    with patch.object(diagnostic, 'failure', side_effect=RuntimeError('synthetic')
                                      if broken_status else None):
                        if broken_status:
                            with self.assertRaises(RuntimeError):
                                record(OSError('synthetic capture'), diagnostic, evidence, signals)
                        else:
                            record(OSError('synthetic capture'), diagnostic, evidence, signals)
                    self.assertEqual(signals, ['OSError'])
                    evidence.failed(ValueError('generic controller failure'))
                    self.assertEqual(json.loads((root / 'backup-overlap.json').read_text())['error_type'], 'OSError')
                finally:
                    diagnostic.close()


if __name__ == '__main__':
    unittest.main()
