"""Failed benchmark attempts retain partial samples and cannot publish success."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import relay_fsck_benchmark as benchmark


class FailureLedger(unittest.TestCase):
    def test_interrupted_case_retains_its_partial_measurement(self):
        def fail(root, binaries, horizon, repeats, result):
            result.update(status='running',samples={'baseline':{'fsck':[{'milliseconds':123.0}]}})
            raise TimeoutError('synthetic failed attempt')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(benchmark,'corruption_controls',return_value={key:True for key in benchmark.CONTROLS}), \
                patch.object(benchmark,'compare_case',side_effect=fail):
            output=Path(directory)/'case'
            with self.assertRaises(TimeoutError):
                benchmark.main(['--baseline',sys.executable,'--candidate',sys.executable,
                                '--output',str(output),'--smoke'])
            record=json.loads((output/'partial-results.json').read_text())
            self.assertFalse(record['ok'])
            self.assertEqual(record['cases'][0]['status'],'failed')
            self.assertEqual(record['cases'][0]['initial_revisions'],6)
            self.assertEqual(record['cases'][0]['samples']['baseline']['fsck'][0]['milliseconds'],123.0)
            self.assertFalse((output/'receipt.json').exists())


if __name__=='__main__':unittest.main()
