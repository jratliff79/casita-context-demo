import json
from pathlib import Path
import shutil
import tempfile
import unittest

import event_kit


class VenueFixture:
    """Supply restored bytes while observing whether selection can change."""
    def __init__(self, source, snapshot, key):
        self.source = source
        self.names = {snapshot: key, event_kit.CURRENT: "previous-verified-key"}
        self.calls = []

    def roots(self):
        return dict(self.names)

    def run(self, *args):
        self.calls.append(args)
        if args[0] == "checkout":
            shutil.copytree(self.source, args[2])
        elif args[:2] == ("root", "set"):
            self.names[args[2]] = args[3]
        else:
            raise AssertionError("unexpected test operation")


class EventKitChecks(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "output"
        output.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first, self.second = self.root / "v1", self.root / "v2"
        self.pins = {"v1": event_kit.create_kit(self.first, "v1"),
                     "v2": event_kit.create_kit(self.second, "v2")}
        for version, pin in self.pins.items():
            pin["directory_key"] = f"synthetic-test-key-{version}"

    def repin(self, folder, pin):
        path = folder / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["files"] = {name: sha for name, sha in event_kit.file_map(folder).items()
                             if name != "manifest.json"}
        data = event_kit.canonical(manifest)
        path.write_bytes(data)
        return dict(pin, manifest_id=event_kit.digest(data))

    def test_update_changes_one_cue_and_preserves_asset_bytes(self):
        first = event_kit.verify_kit(self.first, self.pins["v1"])
        second = event_kit.verify_kit(self.second, self.pins["v2"])
        self.assertEqual(first["cues"][0], second["cues"][0])
        self.assertEqual(first["cues"][1]["time"], "18:10")
        self.assertEqual(second["cues"][1]["time"], "18:15")
        for name in event_kit.ASSETS:
            self.assertEqual((self.first / name).read_bytes(), (self.second / name).read_bytes())
        self.assertNotEqual(self.pins["v1"]["manifest_id"], self.pins["v2"]["manifest_id"])

    def test_pinned_sync_counter_requires_one_nonnegative_integer(self):
        self.assertEqual(event_kit.payloads_sent("revision example\npayloads-sent 3\n"), 3)
        for report in ("missing counter\n", "payloads-sent -1\n",
                       "payloads-sent 3\npayloads-sent 4\n"):
            with self.subTest(report=report), self.assertRaises(ValueError):
                event_kit.payloads_sent(report)

    def test_stale_snapshot_rejects_before_checkout_or_selection(self):
        venue = VenueFixture(self.first, event_kit.snapshot_name("v1"), self.pins["v1"]["directory_key"])
        destination = self.root / "stale"
        with self.assertRaisesRegex(ValueError, "snapshot directory key"):
            event_kit.select_kit(venue, event_kit.snapshot_name("v1"), self.pins["v2"], destination)
        self.assertEqual(venue.names[event_kit.CURRENT], "previous-verified-key")
        self.assertEqual(venue.calls, [])
        self.assertFalse(destination.exists())

    def test_missing_directory_pin_rejects_before_checkout(self):
        venue = VenueFixture(self.second, event_kit.snapshot_name("v2"), self.pins["v2"]["directory_key"])
        pin = dict(self.pins["v2"])
        del pin["directory_key"]
        with self.assertRaisesRegex(ValueError, "snapshot directory key"):
            event_kit.select_kit(venue, "missing-snapshot", pin, self.root / "not-created")
        self.assertEqual(venue.calls, [])

    def test_invalid_restored_asset_keeps_previous_selection(self):
        (self.second / "assets/title.svg").write_bytes(b"synthetic altered graphic")
        venue = VenueFixture(self.second, event_kit.snapshot_name("v2"), self.pins["v2"]["directory_key"])
        with self.assertRaisesRegex(ValueError, "file hashes"):
            event_kit.select_kit(venue, event_kit.snapshot_name("v2"), self.pins["v2"], self.root / "invalid")
        self.assertEqual(venue.names[event_kit.CURRENT], "previous-verified-key")
        self.assertEqual([call[0] for call in venue.calls], ["checkout"])

    def test_verified_snapshot_can_be_selected(self):
        venue = VenueFixture(self.second, event_kit.snapshot_name("v2"), self.pins["v2"]["directory_key"])
        event_kit.select_kit(venue, event_kit.snapshot_name("v2"), self.pins["v2"], self.root / "selected")
        self.assertEqual(venue.names[event_kit.CURRENT], self.pins["v2"]["directory_key"])
        event_kit.verify_kit(self.root / "selected", self.pins["v2"])

    def test_mixed_rundown_and_stale_manifest_reject(self):
        shutil.copyfile(self.first / "rundown.json", self.second / "rundown.json")
        with self.assertRaisesRegex(ValueError, "file hashes"):
            event_kit.verify_kit(self.second, self.pins["v2"])
        with self.assertRaisesRegex(ValueError, "manifest pin"):
            event_kit.verify_kit(self.first, self.pins["v2"])

    def test_missing_extra_and_linked_assets_reject(self):
        path = self.second / "assets/title.svg"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "file set"):
            event_kit.verify_kit(self.second, self.pins["v2"])
        path.write_bytes(original)
        extra = self.second / "extra.txt"
        extra.write_text("synthetic extra")
        with self.assertRaisesRegex(ValueError, "file set"):
            event_kit.verify_kit(self.second, self.pins["v2"])
        extra.unlink()
        path.unlink()
        path.symlink_to(self.first / "assets/title.svg")
        with self.assertRaisesRegex(ValueError, "link"):
            event_kit.verify_kit(self.second, self.pins["v2"])

    def test_rebound_event_rejects_with_new_manifest_pin(self):
        path = self.second / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["event_id"] = "synthetic-other-event"
        data = event_kit.canonical(manifest)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "event or version binding"):
            event_kit.verify_kit(self.second, dict(self.pins["v2"], manifest_id=event_kit.digest(data)))

    def test_invalid_clock_reference_and_duplicate_cue_reject_even_when_repinned(self):
        path = self.second / "rundown.json"
        original = path.read_bytes()
        for field, value, message in (("time", "25:00", "invalid cue"),
                                      ("asset", "../outside.svg", "asset reference"),
                                      ("id", "doors", "cue IDs")):
            with self.subTest(field=field):
                rundown = json.loads(original)
                rundown["cues"][1][field] = value
                path.write_bytes(event_kit.canonical(rundown))
                pin = self.repin(self.second, self.pins["v2"])
                with self.assertRaisesRegex(ValueError, message):
                    event_kit.verify_kit(self.second, pin)


if __name__ == "__main__":
    unittest.main()
