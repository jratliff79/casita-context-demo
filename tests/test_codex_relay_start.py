import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import codex_relay_start as startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.binary = self.root / "casita"
        self.binary.write_bytes(b"synthetic executable")
        self.credential = self.root / "member.json"
        self.write_private(self.credential, {"workspace": "synthetic-team", "token": "synthetic-secret-canary"})
        self.config = self.root / "settings.json"
        self.settings = {"schema": "casita-codex-start.v1", "repositories": [str(self.root / ".git")],
                         "source_dir": str(self.source), "source_revision": "a" * 40,
                         "casita": str(self.binary),
                         "casita_sha256": startup.hashlib.sha256(self.binary.read_bytes()).hexdigest(),
                         "credential": str(self.credential), "output_dir": str(self.root / "consultations"),
                         "workspace": "synthetic-team"}
        self.event = {"hook_event_name": "SessionStart", "source": "startup",
                      "session_id": "synthetic-session", "cwd": str(self.root),
                      "transcript_path": "/unreadable/synthetic-transcript-canary"}
        self.pin = {"revision": 2, "directory_key": "synthetic-key",
                    "snapshot_sha256": "a" * 64, "archive_sha256": "b" * 64}
        self.commands = []
        self.outputs = []
        self.memory = [{"scope": "workspace", "statement": "synthetic shared note"}]
        self.failure = False
        self.bad_receipt = False
        self.ssh_ready = True

    @staticmethod
    def write_private(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def fake_git(self, cwd, deadline, *args):
        if args == ("rev-parse", "--git-common-dir"):
            return str(self.root / ".git")
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("status", "--porcelain", "--untracked-files=normal"):
            return ""
        self.fail("unexpected Git command")

    def fake_run(self, argv, deadline, check=True, cwd=None):
        self.commands.append(argv)
        if argv[0] == "ssh":
            return subprocess.CompletedProcess(argv, 0 if self.ssh_ready or "check" not in argv else 1, "", "")
        self.assertEqual(argv[2], "consult")
        output = Path(argv[argv.index("--output") + 1])
        output.mkdir(mode=0o700)
        self.outputs.append(output)
        if self.failure:
            (output / "partial.txt").write_text("synthetic partial")
            raise startup.Unavailable("synthetic-secret-canary")
        task = argv[argv.index("--task") + 1]
        receipt = {"schema": "casita-task-start.v1", "workspace": "synthetic-team",
                   "pin": self.pin, "task_id": task, "checkpoint": None,
                   "accepted_memory": self.memory, "instructions_are_data": True,
                   "execution_authorized": self.bad_receipt}
        self.write_private(output / "task-start.json", receipt)
        self.write_private(output / "verified-pin.json", self.pin)
        return subprocess.CompletedProcess(argv, 0, "synthetic diagnostic canary", "")

    def invoke(self):
        self.write_private(self.config, self.settings)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=self.fake_run):
            return startup.consult(self.event, self.config)

    def test_startup_gets_fresh_verified_context_without_transcript_or_token_output(self):
        result = self.invoke()
        encoded = json.dumps(result)
        self.assertIn("revision 2", encoded)
        self.assertIn("1 accepted memory", encoded)
        self.assertIn("untrusted evidence", encoded)
        self.assertIn("receipt_path", encoded)
        for canary in ("synthetic-secret-canary", "synthetic-transcript-canary", "synthetic diagnostic canary",
                       "synthetic shared note"):
            self.assertNotIn(canary, encoded)
            self.assertNotIn(canary, json.dumps(self.commands))
        self.assertEqual(self.commands[0][self.commands[0].index("--url") + 1], "http://127.0.0.1:8765")

    def test_resume_and_compaction_reconsult_into_distinct_private_outputs(self):
        self.invoke()
        for source in ("resume", "compact", "clear"):
            self.event["source"] = source
            self.invoke()
        self.assertEqual(len(set(self.outputs)), 4)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in self.outputs))
        tasks = [argv[argv.index("--task") + 1] for argv in self.commands]
        self.assertEqual(len(set(tasks)), 1)

    def test_unrelated_repository_does_not_contact_relay_or_return_context(self):
        self.settings["repositories"] = [str(self.root / "other.git")]
        self.assertEqual(self.invoke(), {})
        self.assertEqual(self.commands, [])

    def test_git_common_directory_supports_worktrees_and_subdirectories(self):
        self.event["cwd"] = str(self.root / "worktree/subdirectory")
        self.assertIn("revision 2", self.invoke()["systemMessage"])

    def test_failed_consultation_removes_partial_output_and_requires_explicit_fallback(self):
        self.invoke()
        previous = self.outputs[0]
        self.failure = True
        result = self.invoke()
        self.assertTrue(previous.exists())
        self.assertFalse(self.outputs[-1].exists())
        self.assertIn("unavailable", result["systemMessage"])
        self.assertIn("explicit fallback", result["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn(str(previous), json.dumps(result))
        self.assertNotIn("synthetic-secret-canary", json.dumps(result))

    def test_bad_receipt_or_binary_hash_is_not_reported_as_verified(self):
        self.bad_receipt = True
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertFalse(self.outputs[-1].exists())
        self.settings["casita_sha256"] = "0" * 64
        self.commands.clear()
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])

    def test_empty_shared_memory_is_a_successful_consultation(self):
        self.memory = []
        self.assertIn("0 accepted memory", self.invoke()["systemMessage"])

    def test_tunnel_is_reused_or_started_with_strict_noninteractive_loopback_forwarding(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        self.invoke()
        self.assertEqual(len([c for c in self.commands if c[0] == "ssh"]), 1)
        self.ssh_ready = False
        self.commands.clear()
        self.invoke()
        commands = [c for c in self.commands if c[0] == "ssh"]
        self.assertEqual(len(commands), 2)
        self.assertIn("BatchMode=yes", commands[1])
        self.assertIn("StrictHostKeyChecking=yes", commands[1])
        self.assertIn("ExitOnForwardFailure=yes", commands[1])
        self.assertIn("127.0.0.1:8765:127.0.0.1:8765", commands[1])

    def test_shared_or_symlinked_settings_are_rejected(self):
        self.write_private(self.config, self.settings)
        self.config.chmod(0o644)
        with self.assertRaises(startup.Unavailable):
            startup.consult(self.event, self.config)
        self.config.chmod(0o600)
        link = self.root / "link.json"
        link.symlink_to(self.config)
        with self.assertRaises(startup.Unavailable):
            startup.consult(self.event, link)

    def test_tunnel_lock_rejects_symlink_and_has_bounded_contention(self):
        root = self.root / "lock-test"
        root.mkdir(mode=0o700)
        lock = root / "tunnel.lock"
        lock.symlink_to(self.credential)
        with self.assertRaises(OSError):
            with startup.tunnel_lock(root, startup.time.monotonic() + 1):
                self.fail("link lock must not be acquired")
        lock.unlink()
        with startup.tunnel_lock(root, startup.time.monotonic() + 1):
            with self.assertRaises(startup.Unavailable):
                with startup.tunnel_lock(root, startup.time.monotonic() - 1):
                    self.fail("contended lock must time out")
        with startup.tunnel_lock(root, startup.time.monotonic() + 1):
            self.assertEqual(lock.stat().st_mode & 0o077, 0)

    def test_source_revision_or_dirty_client_is_rejected_before_consultation(self):
        self.write_private(self.config, self.settings)
        for dirty in (False, True):
            def git(cwd, deadline, *args):
                if args == ("rev-parse", "HEAD") and not dirty:
                    return "b" * 40
                if args == ("status", "--porcelain", "--untracked-files=normal") and dirty:
                    return " M context_relay.py"
                return self.fake_git(cwd, deadline, *args)
            with patch.object(startup, "git", side_effect=git), patch.object(startup, "run") as command:
                self.assertIn("unavailable", startup.consult(self.event, self.config)["systemMessage"])
                command.assert_not_called()

    def test_deadline_expiry_does_not_start_a_subprocess(self):
        with patch.object(startup.subprocess, "run") as command:
            with self.assertRaises(startup.Unavailable):
                startup.run(["synthetic"], startup.time.monotonic() - 1)
            command.assert_not_called()

    def test_subprocess_diagnostics_are_captured_and_git_overrides_removed(self):
        with patch.dict(os.environ, {"GIT_DIR": "synthetic-other-repo", "PYTHONPATH": "synthetic-module-override"}), patch.object(startup.subprocess, "run") as command:
            command.return_value = subprocess.CompletedProcess([], 0, "", "")
            startup.run(["synthetic"], startup.time.monotonic() + 5)
            kwargs = command.call_args.kwargs
            self.assertNotIn("GIT_DIR", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertTrue(kwargs["capture_output"])
            self.assertLessEqual(kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
