import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import authentication as auth
import demo
import handoff
import signed_handoff


class SignatureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.key = self.folder / "sender"
        self.outsider = self.folder / "outsider"
        auth.make_demo_key(self.key)
        auth.make_demo_key(self.outsider)
        self.allowed = self.folder / "allowed"
        auth.provision_demo_signer(self.key, self.allowed, "synthetic-sender", "input")
        self.pins = self.folder / "pins.json"
        self.signature = self.folder / "pins.sig"
        self.data = {"schema": "casita-context-demo.input-pins.v1", "archive_sha256": "0" * 64,
            "v1": {"context_id": "1" * 64, "directory_key": "casita.directory.v1:" + "a" * 43},
            "v2": {"context_id": "2" * 64, "directory_key": "casita.directory.v1:" + "b" * 43}}
        self.pins.write_bytes(demo.canonical(self.data))
        auth.sign_demo_pins(self.pins, self.key, self.signature, "input")
        self.args = argparse.Namespace(pins=self.pins, signature=self.signature,
            allowed_signers=self.allowed, signer="synthetic-sender", archive=self.folder / "absent.casitar")

    def test_real_authorized_signature_accepts_exact_pin_bytes(self):
        receipt = {}
        self.assertEqual(handoff.transfer_pins(self.args, "input", receipt), self.data)
        self.assertTrue(receipt["authentication"]["verified"])
        self.assertEqual(receipt["authentication"]["pins_sha256"], demo.digest(self.pins.read_bytes()))

    def assert_rejected_before_store(self):
        store = Mock()
        with self.assertRaisesRegex(ValueError, "pin signature rejected"):
            handoff.work(store, self.args, self.folder, {})
        store.run.assert_not_called()

    def test_modified_pin_bytes_rejected_before_import(self):
        self.pins.write_bytes(self.pins.read_bytes() + b" ")
        self.assert_rejected_before_store()

    def test_outsider_cannot_claim_authorized_identity(self):
        auth.sign_demo_pins(self.pins, self.outsider, self.signature, "input")
        self.assert_rejected_before_store()

    def test_result_namespace_cannot_authenticate_input(self):
        auth.sign_demo_pins(self.pins, self.key, self.signature, "result")
        self.assert_rejected_before_store()

    def test_wrong_identity_and_empty_trust_file_rejected(self):
        self.args.signer = "other"
        self.assert_rejected_before_store()
        self.args.signer = "synthetic-sender"
        self.allowed.write_bytes(b"")
        self.assert_rejected_before_store()

    def test_return_signature_rejected_before_return_store(self):
        self.args.original = self.folder / "original"
        self.args.original.mkdir()
        (self.args.original / "pins.json").write_bytes(demo.canonical(self.data))
        store = Mock()
        with self.assertRaisesRegex(ValueError, "pin signature rejected"):
            handoff.verify_return(store, self.args, self.folder, {})
        store.run.assert_not_called()

    def test_parse_uses_verified_bytes_even_if_pin_path_changes(self):
        original_keygen = auth.keygen
        def replace_after_verification(*args, **kwargs):
            result = original_keygen(*args, **kwargs)
            self.pins.write_bytes(b"changed after verification")
            return result
        with patch.object(auth, "keygen", side_effect=replace_after_verification):
            self.assertEqual(handoff.transfer_pins(self.args, "input", {}), self.data)

    def test_signed_invalid_schema_still_rejected_before_store(self):
        self.pins.write_bytes(json.dumps({"unexpected": "schema"}).encode())
        auth.sign_demo_pins(self.pins, self.key, self.signature, "input")
        store = Mock()
        with self.assertRaisesRegex(ValueError, "schema"):
            handoff.work(store, self.args, self.folder, {})
        store.run.assert_not_called()

    def test_partial_options_do_not_fall_back_to_unsigned(self):
        self.args.signature = None
        store = Mock()
        with self.assertRaisesRegex(ValueError, "requires"):
            handoff.work(store, self.args, self.folder, {})
        store.run.assert_not_called()

    def test_signature_files_bounded_regular_and_unlinked(self):
        for path, limit in ((self.pins, 10_000), (self.signature, 16_384), (self.allowed, 16_384)):
            with self.subTest(path=path):
                raw = path.read_bytes()
                path.write_bytes(b"x" * (limit + 1))
                with self.assertRaisesRegex(ValueError, "limit"):
                    auth.verify_pins(self.pins, self.signature, self.allowed, "synthetic-sender", "input")
                path.write_bytes(raw)
                linked = self.folder / "linked"
                linked.symlink_to(path)
                with self.assertRaises((ValueError, OSError)):
                    auth.read_regular(linked, limit)
                linked.unlink()
        fifo = self.folder / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):
            auth.read_regular(fifo, 100)

    def test_demo_preserves_existing_output(self):
        marker = self.folder / "keep"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            signed_handoff.run(argparse.Namespace(casita="python3", output=self.folder))
        self.assertEqual(marker.read_text(), "keep")

    def test_failed_demo_removes_throwaway_private_keys(self):
        output = self.folder / "failed-demo"
        with patch.object(signed_handoff, "run_role", side_effect=RuntimeError("controlled failure")):
            with self.assertRaisesRegex(RuntimeError, "controlled failure"):
                signed_handoff.run(argparse.Namespace(casita="python3", output=output))
        self.assertFalse((output / "throwaway-keys").exists())
        receipt = json.loads((output / "receipt.json").read_bytes())
        self.assertFalse(receipt["ok"])
        self.assertTrue(receipt["throwaway_private_keys_removed"])
