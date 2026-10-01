import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import demo
import environment


class EnvironmentChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.layout = self.folder / "layout"
        (self.layout / "blobs/sha256").mkdir(parents=True)
        self.config = {"os": "linux", "architecture": "arm64"}

        def blob(data):
            digest = demo.digest(data)
            (self.layout / "blobs/sha256" / digest).write_bytes(data)
            return {"digest": "sha256:" + digest, "size": len(data)}

        config = blob(demo.canonical(self.config))
        self.layer = blob(b"synthetic layer bytes; never executed")
        manifest = blob(demo.canonical({"schemaVersion": 2, "config": config, "layers": [self.layer]}))
        self.index = {"schemaVersion": 2, "manifests": [manifest]}
        (self.layout / "index.json").write_bytes(demo.canonical(self.index))
        (self.layout / "oci-layout").write_bytes(demo.canonical({"imageLayoutVersion": "1.0.0"}))
        self.pin = {"manifest_digest": manifest["digest"], "config_digest": config["digest"], "platform": "linux/arm64"}

    def test_exact_reachable_image_blobs_accepted(self):
        self.assertEqual(len(environment.verify_layout(self.layout, self.pin)), 5)

    def test_wrong_image_pin_rejected_before_runtime_or_tar_creation(self):
        wrong = dict(self.pin, manifest_digest="sha256:" + "0" * 64)
        with patch("environment.runtime_run") as run:
            with self.assertRaisesRegex(ValueError, "manifest pin mismatch"):
                environment.load_apple_image(self.layout, wrong, self.folder, [])
        run.assert_not_called()
        self.assertFalse((self.folder / "worker-image.tar").exists())

    def test_corrupted_layer_rejected_before_runtime(self):
        layer = self.layout / "blobs/sha256" / self.layer["digest"][7:]
        data = layer.read_bytes()
        layer.write_bytes(b"X" + data[1:])
        with patch("environment.runtime_run") as run:
            with self.assertRaisesRegex(ValueError, "blob digest"):
                environment.load_apple_image(self.layout, self.pin, self.folder, [])
        run.assert_not_called()

    def test_post_load_failures_delete_only_the_generated_image_name(self):
        for inspect_result in ("invalid JSON", RuntimeError("inspection failed"), "[]"):
            with self.subTest(inspect_result=inspect_result):
                with patch("environment.runtime_run", side_effect=["loaded", inspect_result, "deleted"]) as run:
                    with self.assertRaises((ValueError, RuntimeError)):
                        environment.load_apple_image(self.layout, self.pin, self.folder, [])
                calls = run.call_args_list
                self.assertEqual(len(calls), 3)
                inspected_name = calls[1].args[0][-1]
                self.assertTrue(inspected_name.startswith("localhost/casita-context-demo-worker:"))
                self.assertEqual(calls[2].args[0], ["container", "image", "delete", inspected_name])

    def test_extra_files_and_links_rejected(self):
        extra = self.layout / "extra"
        extra.write_bytes(b"not part of this image")
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            environment.verify_layout(self.layout, self.pin)
        extra.unlink()
        extra.symlink_to(self.layout / "index.json")
        with self.assertRaisesRegex(ValueError, "link"):
            environment.verify_layout(self.layout, self.pin)

    def test_wrong_platform_and_config_pins_rejected(self):
        for pin in (dict(self.pin, platform="linux/amd64"),
                    dict(self.pin, config_digest="sha256:" + "0" * 64)):
            with self.subTest(pin=pin):
                with self.assertRaises(ValueError):
                    environment.verify_layout(self.layout, pin)

    def test_invalid_manifest_descriptors_rejected(self):
        for descriptor in (None, {"digest": "../../outside", "size": 1},
                           {**self.index["manifests"][0], "size": True},
                           {**self.index["manifests"][0], "size": 1}):
            with self.subTest(descriptor=descriptor):
                (self.layout / "index.json").write_bytes(demo.canonical({"schemaVersion": 2, "manifests": [descriptor]}))
                with self.assertRaises(ValueError):
                    environment.verify_layout(self.layout, self.pin)

    def test_image_json_and_total_bytes_bounded(self):
        (self.layout / "index.json").write_bytes(b" " * (environment.MAX_JSON_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "JSON exceeds"):
            environment.verify_layout(self.layout, self.pin)
        with patch("environment.MAX_IMAGE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "byte limit"):
                environment.verify_layout(self.layout, self.pin)

    def test_runtime_readback_binds_name_manifest_platform_and_config(self):
        valid = [{"configuration": {"name": "local/worker:test"}, "variants": [
            {"digest": self.pin["manifest_digest"], "platform": {"os": "linux", "architecture": "arm64"},
             "config": self.config}]}]
        environment.verify_runtime_image(valid, "local/worker:test", self.pin, self.config)
        mutations = []
        wrong = copy.deepcopy(valid)
        wrong[0]["variants"][0]["digest"] = "sha256:" + "0" * 64
        # An expected digest elsewhere in the JSON must not satisfy the readback.
        wrong[0]["unrelated"] = self.pin["manifest_digest"]
        mutations.append(wrong)
        wrong = copy.deepcopy(valid)
        wrong[0]["variants"][0]["platform"]["architecture"] = "amd64"
        mutations.append(wrong)
        wrong = copy.deepcopy(valid)
        wrong[0]["variants"][0]["config"] = {}
        mutations.append(wrong)
        for inspected in [[], {}, [None], [{"configuration": None}], *mutations]:
            with self.subTest(inspected=inspected):
                with self.assertRaises(ValueError):
                    environment.verify_runtime_image(inspected, "local/worker:test", self.pin, self.config)
        with self.assertRaises(ValueError):
            environment.verify_runtime_image(valid, "local/other:test", self.pin, self.config)

    def test_reviewed_images_are_digest_pinned(self):
        for platform in ("linux/arm64", "linux/amd64"):
            pin = environment.approved_image(platform)
            self.assertEqual(pin["reference"], "docker.io/library/python@" + pin["manifest_digest"])
        with self.assertRaises(ValueError):
            environment.approved_image("linux/unreviewed")

    def test_existing_output_preserved(self):
        marker = self.folder / "keep.txt"
        marker.write_text("keep")
        args = argparse.Namespace(casita=sys.executable, output=self.folder, runtime="none", platform="linux/arm64")
        with self.assertRaises(FileExistsError):
            environment.run(args)
        self.assertEqual(marker.read_text(), "keep")
