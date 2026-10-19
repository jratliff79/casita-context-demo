"""Standalone scans must not precondition publication state or trial order."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import relay_fsck_benchmark as benchmark


class TrialIsolation(unittest.TestCase):
    def test_scan_mutations_do_not_reach_publication_fixture(self):
        count=dict(revisions=6,events=8,requests=8,proposals=2)
        def make(state,*args):
            (state/'store').mkdir(parents=True)
        def scan(binary,store,on_complete=None):
            (store.parent/'SYNTHETIC_SCAN_MUTATION').write_text('process pin')
            measured=dict(milliseconds=1,validation_passed=False)
            if on_complete:on_complete(measured)
            return ('physical','spill','revision 6; checked 6 root(s)','spill'),measured
        def service(state,*args):
            self.assertFalse((state/'SYNTHETIC_SCAN_MUTATION').exists(),
                             'standalone scan preconditioned the publication fixture')
            result=Mock();result.workspace='synthetic';result.store.run=Mock()
            def handle(*args):
                for name in ['import','root','archive','fsck','checkout']:
                    result.store.run(name,'--dry-run') if name=='fsck' else result.store.run(name)
                return dict(pin=dict(revision=7))
            result.handle=handle;return result
        with tempfile.TemporaryDirectory() as directory,patch.object(benchmark,'make_state',side_effect=make), \
             patch.object(benchmark,'verify_history'),patch.object(benchmark,'scan',side_effect=scan), \
             patch.object(benchmark,'state_measurement',side_effect=[dict(counts=count)]+[dict(counts=dict(count,revisions=7,events=9,requests=9))]*4), \
             patch.object(benchmark.relay,'Relay',side_effect=service), \
             patch.object(benchmark.backup,'private_json',return_value={'token':'SYNTHETIC'}):
            benchmark.compare_case(Path(directory)/'case',{'baseline':Path('/synthetic/base'),'candidate':Path('/synthetic/checked')},6,2,{})

    def test_default_curve_counterbalances_first_and_second_positions(self):
        observed=[]
        def compare(root,binaries,horizon,repeats,result):
            observed.append(repeats)
            result.update(status='passed',healthy_reports_equal=True,all_retained_snapshots_verified=True,
                          full_fsck_on_every_publication=True)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binary=root/'native';binary.write_bytes(b'SYNTHETIC');binary.chmod(0o700)
            with patch.object(benchmark,'compare_case',side_effect=compare), \
                 patch.object(benchmark,'corruption_controls',return_value={key:True for key in benchmark.CONTROLS}):
                benchmark.main(['--baseline',str(binary),'--candidate',str(binary),'--output',str(root/'out')])
        self.assertEqual(len(observed),3)
        self.assertTrue(all(repeats>=4 and repeats%2==0 for repeats in observed),
                        'default trials give one binary an extra first/second position')


if __name__=='__main__':unittest.main()
