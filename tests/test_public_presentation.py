"""Generic examples and honest presentation of historical verification records."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import event_kit
import relay_workload_controller as controller
import relay_workload_plan as plan

ROOT=Path(__file__).resolve().parents[1]


class PublicPresentation(unittest.TestCase):
    def test_failed_event_kit_receipt_keeps_generic_service_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'kit';output.mkdir()
            # Path validation has its own tests; isolate the failed-receipt contract.
            with patch.object(event_kit,'new_output',return_value=output), \
                    patch.object(event_kit,'Casita',side_effect=RuntimeError('synthetic stop')):
                with self.assertRaisesRegex(RuntimeError,'synthetic stop'):
                    event_kit.run_demo('/synthetic/native',Path('output/kit'))
            receipt=json.loads((output/'receipt.json').read_text())
            self.assertIn('external_service_integration',receipt)
            self.assertIs(receipt['external_service_integration'],False)
            self.assertIs(receipt['ok'],False)

    def test_worker_accepts_generic_synthetic_namespace_and_removes_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=(root/'worker.log').open('wb')
            peer=controller.Peer([sys.executable,'-B',str(plan.HERE/'relay_workload_client.py')],log)
            completed=False;failure=None
            try:
                peer.send(dict(operation='hello',binary=str(Path(sys.executable).resolve())))
                hello=peer.receive('hello');plan.validate_hello(hello,plan.source_pins(),hello['binary_sha256'])
                config=dict(binary=str(Path(sys.executable).resolve()),output=str(root/'worker-output'),
                    credential=dict(workspace='synthetic-two-operator-test',token='synthetic-ephemeral-token-only'),
                    jobs=[],duration=0,cursor=8)
                peer.send(dict(operation='configure',config=config));peer.receive('ready')
                peer.send(dict(operation='start',epoch=time.monotonic()+.1))
                try:done=peer.receive('done',timeout=3);completed=True
                except (RuntimeError,ValueError) as error:failure=type(error).__name__
                self.assertTrue(completed,'generic synthetic namespace rejected: '+str(failure))
                self.assertTrue(done['credential_removed'])
                self.assertFalse((root/'worker-output/ephemeral-credential.json').exists())
            finally:peer.close();log.close()

    def test_historical_summaries_do_not_claim_a_new_validation_run(self):
        for name in ['relay-validation.json','casita-update-validation.json']:
            with self.subTest(name=name):
                value=json.loads((ROOT/'docs'/name).read_text())
                self.assertEqual(value.get('representation'),'privacy-summary')
                self.assertIs(value['new_validation_run'],False)
                self.assertRegex(value['source_record_sha256'],r'^[0-9a-f]{64}$')
                self.assertIsInstance(value['record'],dict)

    def test_projected_report_keeps_source_bindings_and_recommendation_text(self):
        source=json.loads((ROOT/'fixtures/docs-checkpoint-source.json').read_text())
        self.assertIn('report_provenance',source)
        provenance=source['report_provenance']
        self.assertEqual(provenance['representation'],'citation-omission-projection')
        self.assertEqual(provenance['original_report_sha256'],'67ad5461df1f925b65317ef144876a1e722d9e3bef303e19dc88e6f46b21151e')
        raw=(ROOT/'docs/docs-checkpoint-report.json').read_bytes();report=json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(),source['report_sha256'])
        self.assertEqual(report['source_commit'],source['commit'])
        self.assertEqual(sum(len(f['citations']) for f in report['findings']),12)
        self.assertEqual(len(report['findings']),2)
        items=[{key:f[key] for key in ['title','body','priority']} for f in report['findings']]
        digest=hashlib.sha256(json.dumps(items,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest,'64365fd11eb41b266d8dfb4331cf5e4f16f092cd7014708737945bfb7adb8a8d')


if __name__=='__main__':unittest.main()
