"""Preserve a synthetic background failure through the controller's wrapper."""
import json
from pathlib import Path
import tempfile
import unittest

from relay_workload_backup import BackupEvidence
from relay_workload_diagnostics import Diagnostics


class PrimaryBackupFailureTests(unittest.TestCase):
    def test_original_backup_category_survives_generic_and_cleanup_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.chmod(0o700)
            diagnostic = Diagnostics(root, enabled=False)
            try:
                evidence = BackupEvidence(diagnostic)
                evidence.failed(OSError('SYNTHETIC SECRET'))
                evidence.failed(ValueError('background verification failed'))
                evidence.failed(RuntimeError('cleanup failed'))
                value = json.loads((root / 'backup-overlap.json').read_text())
                self.assertEqual(value['error_type'], 'OSError')
                self.assertEqual(value['state'], 'failed')
                self.assertNotIn('SECRET', json.dumps(value))
            finally:
                diagnostic.close()


if __name__ == '__main__':
    unittest.main()
