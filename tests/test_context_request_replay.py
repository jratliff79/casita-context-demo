import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import context_request_replay as replay
import demo


class RecordedContextRequestChecks(unittest.TestCase):
    def test_both_public_source_allowlists_pin_every_path(self):
        self.assertEqual(replay.SOURCE['license'], 'MIT')
        self.assertEqual(replay.SOURCE['repository'], 'https://github.com/jratliff79/casita-context-demo')
        for name in ('initial', 'supplement'):
            source = replay.SOURCE[name]
            self.assertEqual(source['license'], 'MIT')
            for version in ('base', 'head'):
                self.assertEqual(set(source['files'][version]), set(source['spec']['paths']))
            self.assertIsNone(source['files']['base']['context_request.py'])
        self.assertEqual(replay.SOURCE['initial']['spec']['base_commit'], replay.SOURCE['supplement']['spec']['base_commit'])
        self.assertEqual(replay.SOURCE['initial']['spec']['head_commit'], replay.SOURCE['supplement']['spec']['head_commit'])

    def test_reports_and_request_are_the_pinned_recorded_bytes(self):
        raw = replay.recorded_artifacts()
        for name, value in raw.items():
            self.assertEqual(demo.digest(value), replay.SOURCE['artifact_sha256'][name])
        request = json.loads(raw['request'])
        initial = json.loads(raw['initial_report'])
        final = json.loads(raw['supplement_report'])
        for field in ('context_id', 'context_directory_key', 'base_commit', 'head_commit'):
            self.assertEqual(request[field], initial[field])
        self.assertNotEqual(initial['context_id'], final['context_id'])
        for field in ('base_commit', 'head_commit'):
            self.assertEqual(initial[field], final[field])
        self.assertEqual(len(request['selections']), 3)

    def test_edited_artifacts_reject_before_source_reads_keys_or_output(self):
        original = replay.auth.read_regular
        for ident, filename in replay.ARTIFACTS.items():
            def read(path, limit, filename=filename):
                raw = original(path, limit)
                if path.name == filename:
                    value = json.loads(raw)
                    if 'reviewer' in value:
                        value['reviewer'] = 'edited reviewer'
                    else:
                        value['reason'] = 'edited request'
                    return demo.canonical(value)
                return raw
            with tempfile.TemporaryDirectory() as name:
                args = argparse.Namespace(source=Path('unused'), casita='unused', output=Path(name)/'output')
                with self.subTest(artifact=ident), patch.object(replay.auth, 'read_regular', side_effect=read), \
                        patch.object(replay.public, 'verify_public_source') as source, \
                        patch.object(replay.auth, 'make_demo_key') as keys:
                    with self.assertRaisesRegex(ValueError, 'pinned artifact'):
                        replay.run(args)
                    source.assert_not_called()
                    keys.assert_not_called()
                self.assertFalse(args.output.exists())

    def test_public_source_mismatch_rejects_before_output(self):
        with tempfile.TemporaryDirectory() as name:
            args = argparse.Namespace(source=Path('unused'), casita='unused', output=Path(name)/'output')
            with patch.object(replay.public, 'verify_public_source', side_effect=ValueError('wrong public blob')):
                with self.assertRaisesRegex(ValueError, 'wrong public blob'):
                    replay.run(args)
            self.assertFalse(args.output.exists())

    def test_failure_removes_throwaway_keys_and_records_no_quality_claim(self):
        with tempfile.TemporaryDirectory() as name:
            args = argparse.Namespace(source=Path('unused'), casita='unused', output=Path(name)/'output')
            with patch.object(replay.public, 'verify_public_source'), \
                    patch.object(replay.auth, 'make_demo_key', side_effect=RuntimeError('controlled failure')):
                with self.assertRaisesRegex(RuntimeError, 'controlled failure'):
                    replay.run(args)
            self.assertFalse((args.output/'throwaway-keys').exists())
            receipt = json.loads((args.output/'receipt.json').read_bytes())
            self.assertTrue(receipt['throwaway_private_keys_removed'])
            self.assertFalse(receipt['fresh_ai_review'])
            self.assertFalse(receipt['review_quality_verified'])
            self.assertFalse(receipt['received_code_executed'])


if __name__ == '__main__':
    unittest.main()
