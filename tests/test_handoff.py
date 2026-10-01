import argparse
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import demo
import handoff


class PortableWorkerChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.pins = {"schema": "casita-context-demo.input-pins.v1", "archive_sha256": "0" * 64,
                     "v1": {"context_id": "1" * 64, "directory_key": "casita.directory.v1:" + "a" * 43},
                     "v2": {"context_id": "2" * 64, "directory_key": "casita.directory.v1:" + "b" * 43}}

    def test_only_expected_pin_schema_and_identifiers_accepted(self):
        path = self.folder / "pins.json"
        path.write_bytes(demo.canonical(self.pins))
        self.assertEqual(handoff.read_pins(path, "input"), self.pins)
        invalid = [[], {**self.pins, "extra": "field"}, {**self.pins, "v1": None},
                   {**self.pins, "archive_sha256": None},
                   {**self.pins, "schema": "casita-context-demo.result-pins.v1"}]
        wrong_key = copy.deepcopy(self.pins)
        wrong_key["v1"]["directory_key"] = "../../outside"
        invalid.append(wrong_key)
        for value in invalid:
            with self.subTest(value=value):
                path.write_bytes(demo.canonical(value))
                with self.assertRaises(ValueError):
                    handoff.read_pins(path, "input")

    def test_pin_file_size_bounded(self):
        path = self.folder / "pins.json"
        path.write_bytes(b" " * 10_001)
        with self.assertRaisesRegex(ValueError, "limit"):
            handoff.read_pins(path, "input")

    def test_linked_transfer_inputs_rejected(self):
        regular = self.folder / "regular.json"
        regular.write_bytes(demo.canonical(self.pins))
        linked = self.folder / "linked.json"
        linked.symlink_to(regular)
        with self.assertRaisesRegex(ValueError, "regular file"):
            handoff.read_pins(linked, "input")
        store = Mock()
        with self.assertRaisesRegex(ValueError, "regular file"):
            handoff.receive(store, linked, self.pins, self.folder, {})
        store.run.assert_not_called()

    def test_wrong_archive_pin_rejected_before_store_initialization(self):
        archive = self.folder / "input.casitar"
        archive.write_bytes(b"unexpected archive")
        store = Mock()
        with self.assertRaisesRegex(ValueError, "archive SHA"):
            handoff.receive(store, archive, self.pins, self.folder, {})
        store.run.assert_not_called()

    def test_oversized_archive_rejected_before_store_initialization(self):
        archive = self.folder / "input.casitar"
        archive.write_bytes(b"x" * (handoff.MAX_TRANSFER_BYTES + 1))
        store = Mock()
        with self.assertRaisesRegex(ValueError, "transfer limit"):
            handoff.receive(store, archive, self.pins, self.folder, {})
        store.run.assert_not_called()

    def test_existing_output_preserved(self):
        marker = self.folder / "keep.txt"
        marker.write_text("keep")
        args = argparse.Namespace(casita="python3", output=self.folder, mode="prepare")
        with self.assertRaises(FileExistsError):
            handoff.run(args)
        self.assertEqual(marker.read_text(), "keep")
