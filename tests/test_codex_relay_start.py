import json
import os
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

import codex_relay_start as startup


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.binary = self.root / "casita"
        self.binary.write_bytes(b"synthetic executable")
        self.write_private(self.root / "ssh-config", {})
        (self.root / "ssh-config").write_text("Host synthetic-relay\n HostName synthetic.invalid\n")
        self.write_private(self.root / "known-hosts", {})
        (self.root / "known-hosts").write_text("synthetic.invalid ssh-ed25519 SYNTHETIC\n")
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
        self.ssh_destination = "hostname synthetic-relay\nuser synthetic\nport 22\n"
        self.object_format = "sha1"
        self.revision = "a" * 40
        self.forward_failure = False
        self.client_blob = b"print('synthetic client')\n"
        self.client_oid = startup.hashlib.sha1(b"blob " + str(len(self.client_blob)).encode()
                                              + b"\0" + self.client_blob).hexdigest()

    @staticmethod
    def write_private(path, value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def fake_git(self, cwd, deadline, *args):
        if args == ("rev-parse", "--git-common-dir"):
            return str(self.root / ".git")
        if args == ("rev-parse", "--show-toplevel"):
            return str(self.root / "worktree") if "worktree" in str(cwd) else str(self.root)
        if args[-4:] == ("worktree", "list", "--porcelain", "-z"):
            return "worktree " + str(self.root) + "\0HEAD synthetic\0\0worktree " + str(self.root / "worktree") + "\0HEAD synthetic\0\0"
        if args == ("rev-parse", "HEAD"):
            return self.revision
        if args == ("rev-parse", "--show-object-format=storage"):
            return self.object_format
        if args == ("status", "--porcelain", "--untracked-files=normal"):
            return ""
        if args == ("ls-tree", "-r", "-z", self.revision):
            return "100644 blob " + self.client_oid + "\tcontext_relay.py\0"
        self.fail("unexpected Git command")

    def fake_run(self, argv, deadline, check=True, cwd=None, text=True):
        if "cat-file" in argv:
            return subprocess.CompletedProcess(argv, 0, self.client_blob, b"")
        self.commands.append(argv)
        if argv[0] == startup.SSH:
            if "-G" in argv:
                return subprocess.CompletedProcess(argv, 0, self.ssh_destination, "")
            if "forward" in argv and self.forward_failure:
                raise startup.Unavailable()
            return subprocess.CompletedProcess(argv, 0 if self.ssh_ready or "check" not in argv else 1, "", "")
        self.assertEqual(argv[1:5], ["-I", "-S", "-B", "-c"])
        self.assertIn("consult", argv)
        self.assertNotEqual(cwd, self.source)
        self.assertEqual((cwd / "context_relay.py").read_bytes(), self.client_blob)
        executable = Path(argv[argv.index("--casita") + 1])
        self.assertNotEqual(executable, self.binary)
        self.assertEqual(executable.read_bytes(), b"synthetic executable")
        self.assertEqual(executable.stat().st_mode & 0o777, 0o700)
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

    def test_private_json_reads_validated_fd_despite_path_replacement(self):
        self.write_private(self.config, {"selection": "reviewed"})
        original_fstat = os.fstat
        def replaced_entry(fd):
            mode = original_fstat(fd)
            self.config.rename(self.root / "old-settings.json")
            self.config.write_text('{"selection":"unqualified replacement"}')
            self.config.chmod(0o666)
            return mode
        with patch.object(startup.os, "fstat", side_effect=replaced_entry):
            self.assertEqual(startup.private_json(self.config), {"selection": "reviewed"})
        with self.assertRaises(startup.Unavailable):
            startup.private_json(self.config)
        self.config.unlink()
        self.config.symlink_to(self.root / "old-settings.json")
        with self.assertRaises(startup.Unavailable):
            startup.private_json(self.config)

    def test_documented_command_generator_preserves_paths_with_shell_syntax(self):
        docs = (Path(startup.__file__).parent / "docs/codex-task-start.md").read_text()
        snippet = docs.split("```python\n", 1)[1].split("```", 1)[0]
        adapter = self.root / "adapter space' $; name.py"
        settings = self.root / "settings space' $; name.json"
        adapter.write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
        snippet = snippet.replace('"/ABSOLUTE/PRIVATE/codex_relay_start.py"', repr(str(adapter)))
        snippet = snippet.replace('"/ABSOLUTE/PRIVATE/settings.json"', repr(str(settings)))
        generated = subprocess.check_output([sys.executable, "-c", snippet], text=True)
        command = json.loads(generated)["command"]
        self.assertEqual(shlex.split(command)[4:], [str(adapter), "--config", str(settings)])
        shadow = self.root / "python3"
        shadow.write_text("#!/bin/sh\nexit 76\n")
        shadow.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            result = subprocess.check_output(["/bin/sh", "-c", command], text=True)
        self.assertEqual(json.loads(result), ["--config", str(settings)])

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
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        self.invoke()
        ssh_commands = [c for c in self.commands if c[0] == startup.SSH]
        client = next(c for c in self.commands if c[0] != startup.SSH)
        self.assertEqual(client[client.index("--relay-port") + 1], "8765")
        self.assertEqual(len(ssh_commands), 4)
        self.assertIn("forward", ssh_commands[2])
        self.assertRegex(ssh_commands[2][ssh_commands[2].index("-L") + 1], r"127\.0\.0\.1:\d+:127\.0\.0\.1:8765")
        self.assertIn("cancel", ssh_commands[3])
        forwarded = ssh_commands[2][ssh_commands[2].index("-L") + 1]
        self.assertEqual(forwarded, ssh_commands[3][ssh_commands[3].index("-L") + 1])
        derived = ssh_commands[1][ssh_commands[1].index("-S") + 1]
        self.assertNotEqual(derived, self.settings["ssh"]["socket"])
        self.ssh_ready = False
        self.commands.clear()
        self.invoke()
        commands = [c for c in self.commands if c[0] == startup.SSH]
        self.assertEqual(len(commands), 5)
        self.assertIn("BatchMode=yes", commands[2])
        self.assertIn("StrictHostKeyChecking=yes", commands[2])
        self.assertIn("ExitOnForwardFailure=yes", commands[2])
        self.assertIn("ClearAllForwardings=yes", commands[2])
        self.assertNotIn("-L", commands[2])
        self.assertIn("forward", commands[3])

    def test_existing_master_without_available_exact_forward_never_receives_credential(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        self.forward_failure = True
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(len(self.commands), 4)
        self.assertTrue(all(c[0] == startup.SSH and "--credential" not in c for c in self.commands))
        self.assertEqual(self.outputs, [])

    def test_changed_effective_destination_or_alias_never_reuses_previous_master(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        sockets = []
        for host in ("first", "second"):
            self.ssh_destination = "hostname " + host + "\nuser synthetic\nport 22\n"
            self.invoke()
            check = [c for c in self.commands if "check" in c][-1]
            sockets.append(check[check.index("-S") + 1])
        self.settings["ssh"]["alias"] = "different-alias"
        self.invoke()
        check = [c for c in self.commands if "check" in c][-1]
        sockets.append(check[check.index("-S") + 1])
        self.assertEqual(len(set(sockets)), 3)

    def test_failed_client_cancels_only_its_ephemeral_forward(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        self.failure = True
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        forward = next(c for c in self.commands if "forward" in c)
        cancel = next(c for c in self.commands if "cancel" in c)
        self.assertEqual(forward[forward.index("-L") + 1], cancel[cancel.index("-L") + 1])
        self.assertFalse(self.outputs[-1].exists())
        self.assertTrue(all("exit" not in c for c in self.commands))

    def test_expired_consultation_deadline_still_spawns_bounded_cancellation(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        real_run = startup.run
        real_popen = subprocess.Popen
        self.write_private(self.config, self.settings)
        with patch.object(startup.time, "monotonic", return_value=100) as now:
            def expired_run(argv, deadline, *args, **kwargs):
                if "consult" in argv:
                    now.return_value = deadline + 1
                    raise subprocess.TimeoutExpired("synthetic client", 35)
                if "cancel" in argv:
                    return real_run(argv, deadline, *args, **kwargs)
                return self.fake_run(argv, deadline, *args, **kwargs)
            with patch.object(startup, "git", side_effect=self.fake_git), \
                    patch.object(startup, "run", side_effect=expired_run), \
                    patch.object(startup.subprocess, "Popen", side_effect=lambda argv, **kw: real_popen([sys.executable, "-c", "pass"], **kw)) as command:
                self.assertIn("unavailable", startup.consult(self.event, self.config)["systemMessage"])
                command.assert_called_once()
                self.assertIn("cancel", command.call_args.args[0])
                self.assertTrue(command.call_args.kwargs["start_new_session"])

    def test_long_master_socket_namespace_is_rejected_before_authentication(self):
        parent = self.root / ("s" * 90)
        parent.mkdir(mode=0o700)
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"), "socket": str(parent / "tunnel.sock"), "alias": "synthetic-relay"}
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(len(self.commands), 1)
        self.assertIn("-G", self.commands[0])
        self.assertEqual(self.outputs, [])

    def test_present_malformed_ssh_setting_is_rejected_before_any_client_invocation(self):
        for value in ({}, None, False, [], {"alias": "synthetic-relay"}):
            self.settings["ssh"] = value
            self.commands.clear()
            self.assertIn("unavailable", self.invoke()["systemMessage"])
            self.assertEqual(self.commands, [])

    def test_ssh_config_rejects_writable_symlink_and_include_inputs_before_ssh(self):
        config = self.root / "ssh-config"
        self.settings["ssh"] = {"config": str(config), "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"),
                                "alias": "synthetic-relay"}
        config.chmod(0o622)
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        config.chmod(0o600)
        for line in ("Include extras.conf", "iNcLuDe=extras.conf", "  Include /synthetic/*.conf"):
            config.write_text(line + "\n")
            self.assertIn("unavailable", self.invoke()["systemMessage"])
        config.unlink()
        config.symlink_to(self.credential)
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])

    def test_ssh_uses_protected_snapshot_despite_original_config_replacement(self):
        config = self.root / "ssh-config"
        original = config.read_bytes()
        self.settings["ssh"] = {"config": str(config), "known_hosts": str(self.root / "known-hosts"), "socket": str(self.root / "tunnel.sock"),
                                "alias": "synthetic-relay"}
        def changed_run(argv, *args, **kwargs):
            if argv[0] == startup.SSH and argv[argv.index("-F") + 1] != "none":
                snapshot = Path(argv[argv.index("-F") + 1])
                self.assertNotEqual(snapshot, config)
                self.assertEqual(snapshot.read_bytes(), original)
                self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
                config.write_text("ProxyCommand synthetic-unreviewed-command\n")
            return self.fake_run(argv, *args, **kwargs)
        self.write_private(self.config, self.settings)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=changed_run):
            self.assertIn("verified", startup.consult(self.event, self.config)["systemMessage"])
        self.assertEqual(list((self.root / "consultations").glob("ssh-config-*")), [])

    def test_original_executable_replacement_cannot_change_client_binary(self):
        def changed_run(argv, *args, **kwargs):
            if "cat-file" in argv:
                self.binary.write_bytes(b"synthetic unqualified replacement")
            return self.fake_run(argv, *args, **kwargs)
        self.write_private(self.config, self.settings)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=changed_run):
            self.assertIn("verified", startup.consult(self.event, self.config)["systemMessage"])
        self.assertEqual(list((self.root / "consultations").glob("reviewed-casita-*")), [])
        target = self.root / "original-binary"
        target.write_bytes(b"synthetic executable")
        self.binary.unlink()
        self.binary.symlink_to(target)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=changed_run):
            self.assertIn("verified", startup.consult(self.event, self.config)["systemMessage"])
        self.assertEqual(target.read_bytes(), b"synthetic unqualified replacement")

    def test_oversized_sparse_executable_rejects_before_copy_or_contact(self):
        with self.binary.open("wb") as stream:
            stream.truncate(startup.MAX_EXECUTABLE + 1)
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])
        self.assertEqual(list((self.root / "consultations").glob("reviewed-casita-*")), [])

    def test_executable_growth_is_bounded_even_with_a_matching_digest(self):
        raw = b"synthetic executable" * 100
        self.binary.write_bytes(raw)
        actual = self.binary.stat()
        fields = list(actual)
        fields[6] = 1  # Simulate the smaller size observed before a file grows.
        real_fstat = os.fstat
        def observed_before_growth(fd):
            mode = real_fstat(fd)
            return os.stat_result(fields) if mode.st_ino == actual.st_ino else mode
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with patch.object(startup, "MAX_EXECUTABLE", 64), patch.object(startup.os, "fstat", side_effect=observed_before_growth):
            with self.assertRaises(startup.Unavailable):
                with startup.reviewed_executable(self.binary, startup.hashlib.sha256(raw).hexdigest(), private, startup.time.monotonic() + 5):
                    self.fail("growth exceeded copy limit")
        self.assertEqual(list(private.iterdir()), [])

    def test_command_and_provider_ssh_directives_reject_before_config_evaluation(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"), "known_hosts": str(self.root / "known-hosts"),
                                "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        for directive in ("Match exec synthetic-command", "Match host *", "ProxyCommand=synthetic-helper",
                          "LocalCommand synthetic-helper", "KnownHostsCommand synthetic-helper",
                          "PKCS11Provider /synthetic/provider", "SecurityKeyProvider /synthetic/provider",
                          "XAuthLocation /synthetic/helper"):
            (self.root / "ssh-config").write_text(directive + "\n")
            self.assertIn("unavailable", self.invoke()["systemMessage"])
            self.assertEqual(self.commands, [])

    def test_output_root_in_checkout_or_linked_checkout_is_rejected_before_contact(self):
        for marker in ("directory", "file"):
            checkout = self.root / marker
            checkout.mkdir()
            if marker == "directory":
                (checkout / ".git").mkdir()
            else:
                (checkout / ".git").write_text("gitdir: /synthetic/common/worktrees/linked\n")
            self.settings["output_dir"] = str(checkout / "private")
            self.assertIn("unavailable", self.invoke()["systemMessage"])
            self.assertFalse((checkout / "private").exists())
        alias = self.root / "output-parent-link"
        alias.symlink_to(checkout, target_is_directory=True)
        self.settings["output_dir"] = str(alias / "private")
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])

    def test_private_output_and_socket_leaf_cannot_use_replaceable_ancestors(self):
        shared = self.root / "shared"
        shared.mkdir()
        shared.chmod(0o777)
        private = shared / "private"
        private.mkdir(mode=0o700)
        self.settings["output_dir"] = str(private)
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])
        self.settings["output_dir"] = str(self.root / "consultations")
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"), "known_hosts": str(self.root / "known-hosts"),
                                "socket": str(private / "tunnel.sock"), "alias": "synthetic-relay"}
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(len(self.commands), 1)
        self.assertIn("-G", self.commands[0])

    def test_allowlisted_enrollment_errors_require_visible_fallback(self):
        self.write_private(self.config, self.settings)
        for failing in (("rev-parse", "--show-toplevel"), ("worktree", "list", "--porcelain", "-z")):
            def failed_git(cwd, deadline, *args):
                if args == failing or args[-4:] == failing:
                    raise subprocess.TimeoutExpired("synthetic Git inspection", 1)
                return self.fake_git(cwd, deadline, *args)
            with patch.object(startup, "git", side_effect=failed_git), patch.object(startup, "run") as command:
                result = startup.consult(self.event, self.config)
                self.assertIn("unavailable", result["systemMessage"])
                self.assertIn("explicit fallback", result["hookSpecificOutput"]["additionalContext"])
                command.assert_not_called()

    def test_sha256_client_revision_and_blob_hashes_are_supported(self):
        self.object_format = "sha256"
        self.revision = "a" * 64
        self.settings["source_revision"] = self.revision
        self.client_oid = startup.hashlib.sha256(b"blob " + str(len(self.client_blob)).encode()
                                                + b"\0" + self.client_blob).hexdigest()
        self.assertIn("verified", self.invoke()["systemMessage"])

    def test_valid_large_memory_receipt_is_accepted_but_oversized_receipt_is_removed(self):
        self.memory = [{"scope": "workspace", "statement": "s" * 1000,
                        "citation": {"excerpt": ["synthetic evidence"]}} for _ in range(50)]
        self.assertIn("50 accepted memory", self.invoke()["systemMessage"])
        self.assertGreater((self.outputs[-1] / "task-start.json").stat().st_size, startup.MAX_INPUT)
        self.memory = [{"statement": "s" * startup.MAX_RECEIPT}]
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertFalse(self.outputs[-1].exists())

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
        with patch.object(startup.subprocess, "Popen") as command:
            with self.assertRaises(startup.Unavailable):
                startup.run(["synthetic"], startup.time.monotonic() - 1)
            command.assert_not_called()

    def test_subprocess_diagnostics_are_captured_and_git_overrides_removed(self):
        real_popen = subprocess.Popen
        with patch.dict(os.environ, {"GIT_DIR": "synthetic-other-repo", "PYTHONPATH": "synthetic-module-override", "PATH": str(self.root), "DEVELOPER_DIR": "synthetic-override"}), patch.object(startup.subprocess, "Popen", wraps=real_popen) as command:
            result = startup.run([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], startup.time.monotonic() + 5)
            kwargs = command.call_args.kwargs
            self.assertNotIn("GIT_DIR", kwargs["env"])
            self.assertNotIn("PYTHONPATH", kwargs["env"])
            self.assertNotIn("DEVELOPER_DIR", kwargs["env"])
            self.assertEqual(kwargs["env"]["PATH"], "/usr/bin:/bin")
            self.assertEqual(result.stdout, "out\n")
            self.assertEqual(result.stderr, "err\n")
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_both_subprocess_pipes_are_bounded_and_overflow_reaps_child(self):
        real_popen = subprocess.Popen
        processes = []
        def tracked(*args, **kw):
            process = real_popen(*args, **kw)
            processes.append(process)
            return process
        for stream in ("stdout", "stderr"):
            with patch.object(startup.subprocess, "Popen", side_effect=tracked):
                code = f"import sys,time; sys.{stream}.buffer.write(b'x'*{startup.MAX_COMMAND_OUTPUT + 100000}); sys.{stream}.flush(); time.sleep(60)"
                with self.assertRaises(startup.Unavailable):
                    startup.run([sys.executable, "-c", code], startup.time.monotonic() + 5)
            self.assertIsNotNone(processes[-1].poll())

    def test_stream_capture_deadline_kills_a_silent_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            startup.run([sys.executable, "-c", "import time; time.sleep(60)"], startup.time.monotonic() + 0.1)

    def test_forward_request_timeout_still_cancels_uncertain_mapping(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"),
                                "known_hosts": str(self.root / "known-hosts"),
                                "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        def timed_run(argv, deadline, *args, **kw):
            if "forward" in argv:
                self.commands.append(argv)
                raise subprocess.TimeoutExpired("synthetic forward", 20)
            return self.fake_run(argv, deadline, *args, **kw)
        self.write_private(self.config, self.settings)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=timed_run):
            self.assertIn("unavailable", startup.consult(self.event, self.config)["systemMessage"])
        forward = next(c for c in self.commands if "forward" in c)
        cancel = next(c for c in self.commands if "cancel" in c)
        self.assertEqual(forward[forward.index("-L") + 1], cancel[cancel.index("-L") + 1])
        self.assertEqual(self.outputs, [])

    def test_known_hosts_are_protected_snapshotted_and_bind_master_identity(self):
        known = self.root / "known-hosts"
        original = known.read_bytes()
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"), "known_hosts": str(known),
                                "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        known.chmod(0o666)
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])
        known.chmod(0o600)
        def replaced_run(argv, deadline, *args, **kw):
            if "-G" in argv:
                known.write_text("synthetic replacement host key\n")
            elif argv[0] == startup.SSH and "check" in argv:
                option = next(v for v in argv if v.startswith("UserKnownHostsFile="))
                snapshot = Path(json.loads(option.split("=", 1)[1]))
                self.assertEqual(snapshot.read_bytes(), original)
                for value in ("GlobalKnownHostsFile=none", "KnownHostsCommand=none", "VerifyHostKeyDNS=no"):
                    self.assertIn(value, argv)
            return self.fake_run(argv, deadline, *args, **kw)
        self.write_private(self.config, self.settings)
        with patch.object(startup, "git", side_effect=self.fake_git), patch.object(startup, "run", side_effect=replaced_run):
            self.assertIn("verified", startup.consult(self.event, self.config)["systemMessage"])
        first = next(c for c in self.commands if "check" in c)
        self.invoke()
        last = [c for c in self.commands if "check" in c][-1]
        self.assertNotEqual(first[first.index("-S") + 1], last[last.index("-S") + 1])
        known.unlink()
        known.symlink_to(self.credential)
        self.commands.clear()
        self.assertIn("unavailable", self.invoke()["systemMessage"])
        self.assertEqual(self.commands, [])

    def test_dynamic_known_hosts_command_and_dns_trust_are_rejected(self):
        self.settings["ssh"] = {"config": str(self.root / "ssh-config"), "known_hosts": str(self.root / "known-hosts"),
                                "socket": str(self.root / "tunnel.sock"), "alias": "synthetic-relay"}
        for option in ("knownhostscommand synthetic-dynamic-command", "verifyhostkeydns true", "verifyhostkeydns ask"):
            self.ssh_destination = "hostname synthetic-relay\n" + option + "\n"
            self.commands.clear()
            self.assertIn("unavailable", self.invoke()["systemMessage"])
            self.assertEqual(len(self.commands), 1)
            self.assertIn("-G", self.commands[0])


class GitIsolationTests(unittest.TestCase):
    def test_task_path_cannot_shadow_qualified_git_or_ssh(self):
        shadow = self.root / "shadow"
        shadow.mkdir()
        for name in ("git", "ssh"):
            binary = shadow / name
            binary.write_text("#!/bin/sh\nexit 76\n")
            binary.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(shadow), "DEVELOPER_DIR": str(shadow)}):
            self.assertEqual(Path(startup.git(self.repo, self.deadline, "rev-parse", "--show-toplevel")).resolve(), self.repo.resolve())
            result = startup.run([startup.SSH, "-V"], self.deadline)
            self.assertIn("OpenSSH", result.stderr + result.stdout)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.deadline = startup.time.monotonic() + 20
        self.git("init", "-q")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "--allow-empty", "-qm", "synthetic fixture")
        self.common = startup.common_dir(self.repo, self.deadline)

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                                text=True, check=True)
        return result.stdout.strip()

    def test_real_registered_worktree_and_subdirectory_are_enrolled(self):
        linked = self.root / "linked worktree"
        self.git("worktree", "add", "--detach", str(linked))
        subdir = linked / "subdirectory"
        subdir.mkdir()
        self.assertTrue(startup.enrolled_repository(self.repo, [self.common], self.deadline))
        self.assertTrue(startup.enrolled_repository(subdir, [self.common], self.deadline))

    def test_git_file_or_symlink_to_enrolled_common_dir_does_not_enroll_lookalike(self):
        for kind in ("file", "symlink"):
            fake = self.root / kind
            fake.mkdir()
            if kind == "file":
                (fake / ".git").write_text("gitdir: " + str(self.common) + "\n")
            else:
                (fake / ".git").symlink_to(self.common, target_is_directory=True)
            self.assertEqual(startup.common_dir(fake, self.deadline), self.common)
            self.assertFalse(startup.enrolled_repository(fake, [self.common], self.deadline))
            # Explicit core.worktree can make Git report the registered primary top level.
            self.git("config", "core.worktree", str(self.repo))
            self.assertFalse(startup.enrolled_repository(fake, [self.common], self.deadline))
            self.git("config", "--unset", "core.worktree")

    def test_ignored_bytecode_and_source_mutations_cannot_enter_exported_client(self):
        import py_compile
        (self.repo / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        (self.repo / "context_relay.py").write_text(
            "import argparse\nimport helper\nimport json\nfrom pathlib import Path\n"
            "assert json.loads((Path(__file__).parent / 'fixtures/review-source.json').read_text()) == {'synthetic': True}\n"
            "print(helper.VALUE)\n")
        (self.repo / "helper.py").write_text("VALUE = 'reviewed synthetic source'\n")
        (self.repo / "fixtures").mkdir()
        (self.repo / "fixtures/review-source.json").write_text('{"synthetic": true}')
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "reviewed client fixture")
        revision = self.git("rev-parse", "HEAD")
        marker = self.root / "unreviewed-code-executed"
        payload = self.root / "payload.py"
        payload.write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n")
        py_compile.compile(str(payload), cfile=str(self.repo / "argparse.pyc"), doraise=True)
        # A modified working module is also ignored by the exporter: source comes from Git blobs.
        (self.repo / "helper.py").write_text("VALUE = 'unreviewed working source'\n")
        (self.repo / "fixtures/review-source.json").write_text('{"synthetic": false}')
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with startup.reviewed_client(self.repo, revision, private, self.deadline) as stage:
            self.assertEqual(stage.stat().st_mode & 0o077, 0)
            self.assertFalse((stage / "argparse.pyc").exists())
            result = startup.run(startup.client_command(stage), self.deadline, cwd=self.repo)
            self.assertEqual(result.stdout.strip(), "reviewed synthetic source")
            self.assertFalse(marker.exists())
        self.assertFalse(stage.exists())

    def test_tracked_python_symlink_is_rejected_before_execution(self):
        (self.repo / "context_relay.py").symlink_to("unreviewed.py")
        self.git("add", "context_relay.py")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "synthetic unsupported symlink")
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with self.assertRaises(startup.Unavailable):
            with startup.reviewed_client(self.repo, self.git("rev-parse", "HEAD"), private, self.deadline):
                self.fail("symlink must not be exported")

    def test_git_replace_refs_cannot_change_the_pinned_client_tree(self):
        script = self.repo / "context_relay.py"
        script.write_text("print('original reviewed tree')\n")
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "original synthetic client")
        revision = self.git("rev-parse", "HEAD")
        script.write_text("print('replacement tree must not execute')\n")
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "replacement synthetic client")
        replacement = self.git("rev-parse", "HEAD")
        self.git("reset", "--hard", revision)
        self.git("replace", revision, replacement)
        self.assertIn("replacement tree", self.git("show", revision + ":context_relay.py"))
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with startup.reviewed_client(self.repo, revision, private, self.deadline) as stage:
            result = startup.run(startup.client_command(stage), self.deadline, cwd=stage)
            self.assertEqual(result.stdout.strip(), "original reviewed tree")

    def test_real_sha256_repository_exports_matching_commit_and_blob_objects(self):
        self.repo = self.root / "sha256-repo"
        self.repo.mkdir()
        self.git("init", "-q", "--object-format=sha256")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        (self.repo / "context_relay.py").write_text("print('synthetic SHA-256 client')\n")
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "synthetic SHA-256 fixture")
        revision = self.git("rev-parse", "HEAD")
        self.assertEqual(len(revision), 64)
        private = self.root / "private"
        private.mkdir(mode=0o700)
        with startup.reviewed_client(self.repo, revision, private, self.deadline) as stage:
            result = startup.run(startup.client_command(stage), self.deadline, cwd=stage)
            self.assertEqual(result.stdout.strip(), "synthetic SHA-256 client")

    def test_background_status_preserves_index_bytes_and_existing_index_lock(self):
        script = self.repo / "context_relay.py"
        script.write_text("print('synthetic client')\n")
        self.git("add", ".")
        self.git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                 "commit", "-qm", "synthetic index fixture")
        index = self.common / "index"
        before = index.read_bytes()
        os.utime(script, (1, 1))
        self.assertEqual(startup.git(self.repo, self.deadline, "status", "--porcelain", "--untracked-files=normal"), "")
        self.assertEqual(index.read_bytes(), before)
        lock = self.common / "index.lock"
        lock.write_bytes(b"synthetic concurrent Git lock")
        self.assertEqual(startup.git(self.repo, self.deadline, "status", "--porcelain", "--untracked-files=normal"), "")
        self.assertEqual(index.read_bytes(), before)
        self.assertEqual(lock.read_bytes(), b"synthetic concurrent Git lock")


if __name__ == "__main__":
    unittest.main()
