import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import artifacts


class ArtifactChecks(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.render, self.recipe_sha = artifacts.load_builder()
        self.source, self.bundle = self.root / "source", self.root / "bundle"
        artifacts.create_source(self.source, "v1")
        self.pin = artifacts.build_bundle(self.source, self.bundle, "v1", self.render, self.recipe_sha)

    def rewrite_manifest(self, change):
        path = self.bundle / "manifest.json"
        manifest = json.loads(path.read_bytes())
        change(manifest)
        data = artifacts.canonical(manifest)
        path.write_bytes(data)
        return dict(self.pin, manifest_id=artifacts.digest(data))

    def test_restore_verification_needs_no_builder_or_source(self):
        import shutil
        shutil.rmtree(self.source)
        with patch.object(artifacts, "load_builder", side_effect=AssertionError("must not rebuild")):
            manifest = artifacts.verify_bundle(self.bundle, self.pin)
        self.assertEqual(manifest["build_inputs"]["recipe_sha256"], self.recipe_sha)
        self.assertEqual(set(artifacts.file_map(self.bundle)), artifacts.OUTPUT_FILES | {"manifest.json"})

    def test_repeat_build_is_exact_and_changed_source_changes_build_id(self):
        again = self.root / "again"
        same = artifacts.build_bundle(self.source, again, "v1", self.render, self.recipe_sha)
        self.assertEqual(same, self.pin)
        self.assertEqual(artifacts.file_map(again), artifacts.file_map(self.bundle))
        source = self.root / "v2-source"
        artifacts.create_source(source, "v2")
        other = self.root / "v2-bundle"
        changed = artifacts.build_bundle(source, other, "v2", self.render, self.recipe_sha)
        self.assertNotEqual(changed["build_id"], self.pin["build_id"])
        self.assertEqual((other / "outputs/style.css").read_bytes(), (self.bundle / "outputs/style.css").read_bytes())
        self.assertNotEqual((other / "outputs/index.html").read_bytes(), (self.bundle / "outputs/index.html").read_bytes())

    def test_altered_artifact_and_recomputed_manifest_reject(self):
        (self.bundle / "outputs/index.html").write_bytes(b"synthetic replacement")
        with self.assertRaisesRegex(ValueError, "output hashes"):
            artifacts.verify_bundle(self.bundle, self.pin)
        self.rewrite_manifest(lambda m: m["output_files"].update({
            "outputs/index.html": artifacts.digest(b"synthetic replacement")}))
        with self.assertRaisesRegex(ValueError, "manifest pin"):
            artifacts.verify_bundle(self.bundle, self.pin)

    def test_recipe_toolchain_and_source_rebinding_reject_even_with_new_manifest_pin(self):
        original = (self.bundle / "manifest.json").read_bytes()
        for change in (
            lambda i: i.update(recipe_sha256="0" * 64),
            lambda i: i["toolchain"].update(python="invented-version"),
            lambda i: i["source_files"].update({"page.json": "0" * 64}),
        ):
            with self.subTest(change=change):
                (self.bundle / "manifest.json").write_bytes(original)
                def rebind(manifest):
                    change(manifest["build_inputs"])
                    manifest["build_id"] = artifacts.digest(artifacts.canonical(manifest["build_inputs"]))
                pin = self.rewrite_manifest(rebind)
                with self.assertRaisesRegex(ValueError, "build input binding"):
                    artifacts.verify_bundle(self.bundle, pin)

    def test_malformed_input_schema_rejects_even_when_repinned(self):
        def malformed(manifest):
            manifest["build_inputs"] = []
            manifest["build_id"] = artifacts.digest(artifacts.canonical([]))
        pin = self.rewrite_manifest(malformed)
        pin["build_id"] = artifacts.digest(artifacts.canonical([]))
        with self.assertRaisesRegex(ValueError, "input schema"):
            artifacts.verify_bundle(self.bundle, pin)

    def test_wrong_version_and_wrong_build_pin_reject(self):
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            artifacts.verify_bundle(self.bundle, dict(self.pin, version="v2"))
        with self.assertRaisesRegex(ValueError, "build input binding"):
            artifacts.verify_bundle(self.bundle, dict(self.pin, build_id="0" * 64))

    def test_extra_file_and_link_reject(self):
        extra = self.bundle / "extra.txt"
        extra.write_text("synthetic extra file")
        with self.assertRaisesRegex(ValueError, "file set"):
            artifacts.verify_bundle(self.bundle, self.pin)
        extra.unlink()
        target = self.bundle / "outputs/style.css"
        target.unlink()
        target.symlink_to(self.source / "style.css")
        with self.assertRaisesRegex(ValueError, "link"):
            artifacts.verify_bundle(self.bundle, self.pin)

    def test_linked_source_rejected_before_builder_runs(self):
        target = self.source / "style.css"
        target.unlink()
        target.symlink_to(self.bundle / "outputs/style.css")
        with self.assertRaisesRegex(ValueError, "link"):
            artifacts.build_bundle(self.source, self.root / "not-built", "v1",
                                   lambda *args: self.fail("builder must not run"), self.recipe_sha)
        self.assertFalse((self.root / "not-built").exists())

    def test_changed_source_rejected_before_builder_consumes_unhashed_bytes(self):
        real_map = artifacts.file_map
        def change_after_hashing(folder):
            files = real_map(folder)
            (folder / "page.json").write_bytes(artifacts.canonical({"title": "changed", "body": "synthetic"}))
            return files
        with patch.object(artifacts, "file_map", side_effect=change_after_hashing):
            with self.assertRaisesRegex(ValueError, "source changed"):
                artifacts.build_bundle(self.source, self.root / "not-built", "v1",
                                       lambda *args: self.fail("builder must not run"), self.recipe_sha)
        self.assertFalse((self.root / "not-built").exists())

    def test_renderer_escapes_markup(self):
        result = self.render(artifacts.canonical({"title": "<synthetic>", "body": "<script>example</script>"}), b"css")
        self.assertIn(b"&lt;script&gt;", result["index.html"])
        self.assertNotIn(b"<script>", result["index.html"])

    def test_existing_output_and_links_preserved_before_casita_runs(self):
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        folder = artifacts.new_output(Path("output/trial"))
        marker = folder / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(FileExistsError):
            artifacts.run_demo("not-a-real-executable", Path("output/trial"))
        self.assertEqual(marker.read_text(), "keep")
        link = Path("output/link")
        link.symlink_to(folder, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            artifacts.new_output(link)
        self.assertTrue(link.is_symlink())

    def test_outside_nested_and_linked_output_parent_reject(self):
        original = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original)
        for path in (Path("outside"), Path("output/a/b"), Path("output/../outside"), self.root / "output/a"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                artifacts.new_output(path)
        self.assertFalse(Path("output").exists())
        Path("output").symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "link"):
            artifacts.new_output(Path("output/trial"))
        self.assertFalse((self.bundle / "trial").exists())


if __name__ == "__main__":
    unittest.main()
