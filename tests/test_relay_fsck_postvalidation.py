"""A completed timed publication survives a subsequent failed history gate."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch

import relay_fsck_benchmark as benchmark


class PostValidationLedger(unittest.TestCase):
    def test_completed_time_retained_before_final_audit_failure(self):
        count=dict(revisions=6,events=8,requests=8,proposals=2)
        def service(*args):
            result=Mock(); result.store.run=Mock()
            def handle(*a):
                for name in ['import','root','archive','fsck','checkout']:
                    result.store.run(name,'--dry-run') if name=='fsck' else result.store.run(name)
                return dict(pin=dict(revision=7))
            result.handle=handle
            return result
        history=[None,None,RuntimeError('synthetic post-timing audit failed')]
        record={}
        with tempfile.TemporaryDirectory() as directory, \
            patch.object(benchmark,'make_state'),patch.object(benchmark,'verify_history',side_effect=history), \
            patch.object(benchmark,'state_measurement',side_effect=[dict(counts=count),dict(counts=dict(count,revisions=7,events=9,requests=9))]), \
            patch.object(benchmark,'scan',return_value=(('physical','spill','revision 6; checked 6 root(s)','spill'),dict(milliseconds=1))), \
            patch.object(benchmark.shutil,'copytree'),patch.object(benchmark.relay,'Relay',side_effect=service), \
            patch.object(benchmark.backup,'private_json',return_value={'token':'SYNTHETIC'}):
            with self.assertRaises(RuntimeError):
                benchmark.compare_case(Path(directory)/'case',{'baseline':Path('/synthetic/base'),'candidate':Path('/synthetic/checked')},6,1,record)
        completed=record['samples']['baseline']['publication']
        self.assertEqual(len(completed),1,'completed elapsed time was lost at the later audit failure')
        self.assertGreaterEqual(completed[0]['milliseconds'],0)
        self.assertFalse(completed[0]['validation_passed'])


if __name__=='__main__':unittest.main()
