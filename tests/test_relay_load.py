import io
import fcntl
import ast
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import relay_load as load


class LoadTests(unittest.TestCase):
    def test_source_pins_cover_reachable_local_imports(self):
        pending, seen = ["relay_load.py"], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            for node in ast.walk(ast.parse((load.HERE / name).read_text())):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    candidate = module.split(".")[0] + ".py"
                    if (load.HERE / candidate).is_file():
                        pending.append(candidate)
        self.assertTrue(seen.issubset(load.SOURCE_FILES), seen - set(load.SOURCE_FILES))

    def test_cleanup_failure_emits_only_failure_and_attempts_other_resources(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            broken = Mock()
            broken.close.side_effect = OSError("synthetic close failure")
            other = io.StringIO()
            receipt = {"ok": True, "verification": {}}
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                load.finish(output, receipt, None, [], [broken, other])
            self.assertTrue(other.closed)
            self.assertFalse((output / "receipt.json").exists())
            self.assertFalse((output / "receipt-pending.json").exists())
            self.assertFalse(json.loads((output / "failure.json").read_bytes())["ok"])

    def test_success_receipt_is_published_after_all_resources_close(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            log = Mock()
            def closed_before_receipt():
                self.assertFalse((output / "receipt.json").exists())
            log.close.side_effect = closed_before_receipt
            receipt = {"ok": True, "verification": {}}
            load.finish(output, receipt, None, [], [log])
            log.close.assert_called_once()
            self.assertTrue(json.loads((output / "receipt.json").read_bytes())[
                "verification"]["owned_resource_cleanup"])
            self.assertFalse((output / "failure.json").exists())

    def test_nearest_rank_tail_and_empty_samples(self):
        result = load.latency_summary([{"milliseconds": n} for n in range(1, 21)])
        self.assertEqual(result["p50_ms"], 10)
        self.assertEqual(result["p95_ms"], 19)
        self.assertEqual(result["max_ms"], 20)
        with self.assertRaisesRegex(ValueError, "no latency"):
            load.latency_summary([])

    def test_fixture_payload_bounds_and_versions_are_distinct(self):
        first = load.fixture("task-alpha", 0, 4096)
        second = load.fixture("task-alpha", 1, 4096)
        self.assertEqual(len(first["files"][0]["text"].encode()), 4096)
        self.assertTrue(first["files"][0]["text"].startswith("SYNTHETIC"))
        self.assertNotEqual(first["files"][0]["sha256"], second["files"][0]["sha256"])
        self.assertEqual(first, load.fixture("task-alpha", 0, 4096))

    def test_only_publish_conflicts_are_expected_and_not_retried(self):
        for operation, code, expected in (("publish", 409, True), ("context", 409, False),
                                          ("publish", 503, False), ("publish", 401, False)):
            rows = []
            response = io.BytesIO(b"{}")
            error = HTTPError("http://127.0.0.1:1", code, "synthetic failure", {}, response)
            with self.subTest(operation=operation, code=code), patch.object(
                    load, "client", side_effect=error) as request:
                if expected:
                    self.assertEqual(load.timed_request(rows, "unused", "unused", operation, {}),
                                     (409, None))
                else:
                    with self.assertRaises(HTTPError):
                        load.timed_request(rows, "unused", "unused", operation, {})
                self.assertEqual(request.call_count, 1)
                self.assertEqual(rows[0]["status"], code)
                self.assertTrue(response.closed)

    def test_storage_deduplicates_allocated_hardlinks_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "source"
            source.write_bytes(b"synthetic" * 1024)
            os.link(source, folder / "copy")
            result = load.storage(folder)
            self.assertEqual(result["allocated_bytes"],
                             (source.stat().st_blocks + folder.stat().st_blocks) * 512)
            self.assertEqual(result["logical_file_bytes"], source.stat().st_size * 2)
            (folder / "link").symlink_to(source)
            with self.assertRaisesRegex(ValueError, "symlink"):
                load.storage(folder)

    @unittest.skipUnless(load.platform.system() == "Linux", "requires Linux /proc")
    def test_invalid_or_existing_output_does_not_initialize_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(load, "initialize") as initialize:
            for kwargs in ({"rounds": 0}, {"tasks": 21}, {"payload_bytes": 16001},
                           {"sample_interval": 0}, {"sample_interval": float("nan")}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    load.run("/bin/true", Path(temp) / "new", **kwargs)
            with self.assertRaises(FileExistsError):
                load.run("/bin/true", Path(temp))
            initialize.assert_not_called()

    def run_worker(self, events, scopes):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "1.go").touch()
            config = {"output": temp, "credential": "unused", "name": "beta",
                      "gates": temp, "cursor": 8, "payload_bytes": 256,
                      "url": "unused", "binary": "unused"}
            path = folder / "config.json"
            path.write_text(json.dumps(config))
            responses = [HTTPError("unused", 409, "conflict", {}, io.BytesIO(b"{}")),
                         {"revision": 7, "pin": {"revision": 7}}, events,
                         {"events": [], "next_cursor": 9}]
            selected = {"checkpoint": {}, "accepted_memory": [{"scope": s} for s in scopes]}
            stream = io.StringIO()
            with patch.object(load.sys, "stdin", io.StringIO(
                    '{"round":1,"expected_revision":6}\n')), patch.object(load.sys, "stdout", stream), \
                    patch.object(load, "client", side_effect=responses) as request, \
                    patch.object(load, "receive", return_value={}), \
                    patch.object(load, "task_start", return_value=selected):
                load.worker(path)
            return json.loads(stream.getvalue()), request.call_count

    def test_worker_conflict_still_restores_and_catches_up_without_retry(self):
        result, requests = self.run_worker({"events": [{"cursor": 9, "kind": "publish",
                                                       "revision": 7, "pin": {"revision": 7}}],
                                            "next_cursor": 9}, ["workspace"])
        self.assertEqual(result["publish_status"], 409)
        self.assertEqual(result["cursor"], 9)
        self.assertEqual(requests, 4)

    def test_worker_rejects_skipped_events_and_task_memory_leakage(self):
        with self.assertRaisesRegex(ValueError, "noncontiguous"):
            self.run_worker({"events": [{"cursor": 10, "kind": "publish"}], "next_cursor": 10},
                            ["workspace"])
        with self.assertRaisesRegex(ValueError, "task memory selection"):
            self.run_worker({"events": [{"cursor": 9, "kind": "publish"}], "next_cursor": 9},
                            ["workspace", "task"])
        with self.assertRaisesRegex(ValueError, "event snapshot pin differs"):
            self.run_worker({"events": [{"cursor": 9, "kind": "publish", "revision": 7,
                                          "pin": {"revision": 6}}], "next_cursor": 9},
                            ["workspace"])

    @unittest.skipUnless(load.platform.system() == "Linux", "requires Linux /proc")
    def test_native_start_failure_emits_no_success_receipt_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "failed"
            with self.assertRaisesRegex(ValueError, "child process exited"):
                load.run("/bin/false", output, rounds=1, tasks=2, payload_bytes=256)
            self.assertFalse((output / "receipt.json").exists())
            self.assertFalse(json.loads((output / "failure.json").read_bytes())["ok"])
            with (output / "relay/server.lock").open("rb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_proc_stat_accounts_for_waited_children_and_command_parentheses(self):
        fields = ["0"] * 22
        for index, value in ((11, "10"), (12, "20"), (13, "30"), (14, "40"), (21, "5")):
            fields[index] = value
        with patch.object(Path, "read_text", return_value="1 (synthetic (command)) " + " ".join(fields)), \
                patch.object(load.os, "sysconf", side_effect=lambda name: {
                    "SC_CLK_TCK": 100, "SC_PAGE_SIZE": 4096}[name]):
            usage = load.proc_usage(1)
        self.assertEqual(usage["cpu_seconds_including_waited_children"], 1)
        self.assertEqual(usage["rss_bytes"], 20480)

    def test_resource_sampler_propagates_measurement_failure(self):
        process = type("SyntheticProcess", (), {"pid": 1})()
        sampler = load.Sampler({"server": process}, .01)
        with patch.object(load, "tree_rss", side_effect=PermissionError("synthetic denied")):
            sampler.thread.start()
            sampler.thread.join(timeout=2)
            with self.assertRaises(PermissionError):
                sampler.stop()


if __name__ == "__main__":
    unittest.main()
