"""A completed full scan survives a following report-count validation failure."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import relay_fsck_benchmark as benchmark

REPORT='''physical repair: revision 12; checked 8 payload(s), 456 byte(s), 0 outboard(s)
traversal-spill-files-opened 0; traversal-spill-peak-bytes 0
physical-fsck-nanos 100
revision 12; checked 7 root(s), 8 object(s), 8 payload(s)
traversal-spill-files-opened 0; traversal-spill-peak-bytes 0
logical-fsck-nanos 200
'''

class ScanLedger(unittest.TestCase):
    def test_completed_scan_retained_before_root_count_mismatch(self):
        record={}
        with tempfile.TemporaryDirectory() as directory,patch.object(benchmark,'make_state'), \
             patch.object(benchmark,'verify_history'),patch.object(benchmark.shutil,'copytree'), \
             patch.object(benchmark,'state_measurement',return_value=dict(counts=dict(revisions=6,events=8,requests=8,proposals=2))), \
             patch.object(benchmark,'command',return_value=SimpleNamespace(returncode=0,stdout=REPORT)):
            with self.assertRaises(ValueError):
                benchmark.compare_case(Path(directory)/'case',{'baseline':Path('/synthetic/base'),'candidate':Path('/synthetic/checked')},6,1,record)
        completed=record['samples']['baseline']['fsck']
        self.assertEqual(len(completed),1,'completed full-scan timing was lost before count validation')
        self.assertGreaterEqual(completed[0]['milliseconds'],0)
        self.assertFalse(completed[0]['validation_passed'])

if __name__=='__main__':unittest.main()
