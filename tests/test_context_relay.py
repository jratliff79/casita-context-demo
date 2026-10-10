import copy
from concurrent.futures import ThreadPoolExecutor
import gc
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import weakref
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import context_relay as relay


class IndexRelay(relay.Relay):
    """SQLite/HTTP tests isolate the object backend; CI also runs the real Casita pilot."""
    def __init__(self, state):
        self.state = state
        self.workspace, self.owner = "synthetic-team", "jp"

    def save_snapshot(self, db, snapshot):
        raw = relay.canonical(snapshot)
        pin = {"revision": snapshot["revision"], "directory_key": "test-only-key",
               "snapshot_sha256": relay.digest(raw), "archive_sha256": relay.digest(raw)}
        (self.state / "snapshots" / (pin["archive_sha256"] + ".casitar")).write_bytes(raw)
        db.execute("INSERT INTO revisions VALUES (?,?,?)",
                   (snapshot["revision"], raw.decode(), json.dumps(pin)))
        return pin


class RelayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        relay.initialize(self.state, "synthetic-team", "jp", ["cj"])
        self.service = IndexRelay(self.state)
        self.credentials = {p: self.state / ("credentials/" + p + ".json") for p in ("jp", "cj")}
        self.tokens = {p: json.loads(f.read_bytes())["token"] for p, f in self.credentials.items()}
        raw = "SYNTHETIC POLICY\nRun focused checks before handoff.\n"
        self.context = {"task_id": "test-task", "summary": "selected synthetic task", "completed": [],
                        "pending": ["review"], "blockers": [], "source_revision": "a" * 40,
                        "files": [{"path": "source/policy.txt", "text": raw,
                                   "sha256": relay.digest(raw.encode())}]}

    def call(self, operation, body, actor="jp"):
        return self.service.handle(self.tokens[actor], operation, {"workspace": "synthetic-team", **body})

    def publish(self, revision=0, request="publish-one", actor="jp"):
        return self.call("publish", {"request_id": request, "expected_revision": revision,
                                     "checkpoint": self.context}, actor)

    def proposal(self, request="proposal-one", actor="cj"):
        source = self.context["files"][0]
        return self.call("propose", {"request_id": request, "expected_revision": 1,
                                    "task_id": "test-task", "scope": "task", "entry_id": "checks", "statement": "Run focused checks.",
                                    "citation": {"path": source["path"], "sha256": source["sha256"],
                                                 "start_line": 2, "end_line": 2,
                                                 "excerpt": source["text"].splitlines()[1:2]}}, actor)

    def test_checkpoint_cas_only_one_concurrent_writer_commits(self):
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.publish, 0, "request-" + p, p) for p in ("jp", "cj")]
        results, rejected = [], []
        for future in futures:
            try:
                results.append(future.result())
            except relay.Rejected as error:
                rejected.append(error.status)
        self.assertEqual(len(results), 1)
        self.assertEqual(rejected, [409])
        self.assertEqual(len(self.call("updates", {"after": 0})["events"]), 1)

    def test_failed_object_write_does_not_publish_head_event_or_idempotency_result(self):
        with patch.object(self.service, "save_snapshot", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertEqual(self.call("context", {})["revision"], 0)
        self.assertEqual(self.call("updates", {"after": 0})["events"], [])
        self.assertEqual(self.publish()["pin"]["revision"], 1)

    def test_failed_transaction_after_object_write_does_not_advance_visible_head(self):
        original = self.service.save_snapshot
        def fail(db, snapshot):
            original(db, snapshot)
            raise RuntimeError("interrupted before metadata commit")
        with patch.object(self.service, "save_snapshot", side_effect=fail):
            with self.assertRaises(RuntimeError):
                self.publish()
        self.assertEqual(self.call("context", {})["revision"], 0)
        self.assertEqual(self.call("updates", {"after": 0})["events"], [])

    def test_retry_is_idempotent_after_restart_but_changed_payload_conflicts(self):
        published = self.publish()
        self.service = IndexRelay(self.state)
        self.assertEqual(self.publish(), published)
        self.context["summary"] = "changed summary"
        with self.assertRaisesRegex(relay.Rejected, "idempotency"):
            self.publish()
        self.assertEqual(len(self.call("updates", {"after": 0})["events"]), 1)

    def test_proposals_do_not_change_accepted_memory_and_competitors_remain(self):
        self.publish()
        a = self.proposal()
        b = self.proposal("other-proposal", "jp")
        with relay.connect(self.state) as db:
            self.assertEqual(self.service.current(db)["memory"], [])
            self.assertEqual(db.execute("SELECT count(*) FROM proposals").fetchone()[0], 2)
        self.assertNotEqual(a["proposal_id"], b["proposal_id"])
        self.call("accept", {"expected_revision": 1, "request_id": "accept-one",
                             "proposal_id": a["proposal_id"]})
        with relay.connect(self.state) as db:
            memory = self.service.current(db)["memory"]
            self.assertEqual(memory[0]["author"], "cj")
            self.assertEqual(memory[0]["accepted_by"], "jp")
        with self.assertRaisesRegex(relay.Rejected, "stale"):
            self.call("accept", {"expected_revision": 2, "request_id": "accept-other",
                                 "proposal_id": b["proposal_id"]})

    def test_member_cannot_accept_even_a_self_authored_proposal(self):
        self.publish()
        proposal = self.proposal()
        with self.assertRaisesRegex(relay.Rejected, "only the workspace owner"):
            self.call("accept", {"request_id": "unauthorized-accept", "expected_revision": 1,
                                 "proposal_id": proposal["proposal_id"]}, "cj")
        self.assertEqual(self.call("context", {})["revision"], 1)

    def test_revocation_blocks_reads_updates_and_idempotent_retries(self):
        self.publish(actor="cj")
        with relay.connect(self.state) as db:
            db.execute("UPDATE members SET enabled=0 WHERE name='cj'")
        for operation, body in (("context", {}), ("updates", {"after": 0}),
                                ("publish", {"request_id": "publish-one", "expected_revision": 0,
                                             "checkpoint": self.context})):
            with self.subTest(operation=operation), self.assertRaisesRegex(relay.Rejected, "unauthorized"):
                self.call(operation, body, "cj")
        self.assertEqual(self.call("context", {})["revision"], 1)

    def test_workspace_and_invalid_token_cannot_read_guessed_context(self):
        self.publish()
        with self.assertRaisesRegex(relay.Rejected, "workspace unavailable"):
            self.service.handle(self.tokens["cj"], "context", {"workspace": "other"})
        with self.assertRaisesRegex(relay.Rejected, "unauthorized"):
            self.service.handle("x" * 43, "context", {"workspace": "synthetic-team"})

    def test_offline_cursor_returns_all_missed_events_without_repetition(self):
        first = self.publish()
        proposal = self.proposal()
        self.call("accept", {"request_id": "accept-one", "expected_revision": 1,
                             "proposal_id": proposal["proposal_id"]})
        self.service = IndexRelay(self.state)
        events = self.call("updates", {"after": first["cursor"]})
        self.assertEqual([e["kind"] for e in events["events"]], ["propose", "accept"])
        self.assertFalse(self.call("updates", {"after": events["next_cursor"]})["events"])

    def test_forged_excerpt_rejected_even_when_source_hash_matches(self):
        with self.assertRaisesRegex(relay.Rejected, "differs"):
            relay.citation({"path": "source/policy.txt", "sha256": self.context["files"][0]["sha256"],
                            "start_line": 2, "end_line": 2, "excerpt": ["invented claim"]}, self.context)

    def test_citations_use_lf_lines_without_unicode_separator_or_trailing_phantom_lines(self):
        for raw, excerpt in (("first\u2028second", ["first\u2028second"]),
                             ("first\x85second", ["first\x85second"]),
                             ("one\r\ntwo\r\n", ["one", "two"])):
            context = copy.deepcopy(self.context)
            source = context["files"][0]
            source.update(text=raw, sha256=relay.digest(raw.encode()))
            valid = {"path": source["path"], "sha256": source["sha256"],
                     "start_line": 1, "end_line": len(excerpt), "excerpt": excerpt}
            relay.citation(valid, context)
            invented = dict(valid, start_line=len(excerpt) + 1, end_line=len(excerpt) + 1,
                            excerpt=["second"])
            with self.assertRaises(relay.Rejected):
                relay.citation(invented, context)

    def test_sole_owner_revocation_rejected_and_member_revocation_still_works(self):
        with self.assertRaisesRegex(relay.Rejected, "sole workspace owner"):
            relay.revoke_member(self.state, "jp")
        self.assertEqual(self.call("context", {})["revision"], 0)
        relay.revoke_member(self.state, "cj")
        with self.assertRaisesRegex(relay.Rejected, "unauthorized"):
            self.call("context", {}, "cj")

    def test_long_running_cli_diagnostics_do_not_retain_completed_command_output(self):
        class Output(str):
            pass
        outputs = []
        def command(argv, **kwargs):
            output = Output("synthetic root listing " * 5000)
            outputs.append(weakref.ref(output))
            return subprocess.CompletedProcess(argv, 0, output, "")
        store = relay.Casita("casita", self.root / "unused", relay.DiscardDiagnostics())
        with patch("demo.subprocess.run", side_effect=command):
            for _ in range(100):
                store.run("root", "ls")
        gc.collect()
        self.assertTrue(all(reference() is None for reference in outputs))

    def test_link_traversal_hidden_paths_unselected_fields_and_bad_hashes_rejected(self):
        for path in ("../private.txt", "/private.txt", "source/.env", "source/key.pem", "a//b", "a\\b"):
            with self.subTest(path=path):
                self.assertFalse(relay.safe_path(path))
        for mutation in (lambda c: c.update(private_chat="unselected"),
                         lambda c: c["files"][0].update(sha256="0" * 64),
                         lambda c: c.update(source_revision="main")):
            value = copy.deepcopy(self.context)
            mutation(value)
            with self.assertRaises(relay.Rejected):
                relay.checkpoint(value)

    def test_prepare_uses_only_selected_commit_bytes_excluding_dirty_untracked_and_symlink(self):
        repo = self.root / "git"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  check=True, text=True).stdout.strip()
        git("init")
        (repo / "selected.txt").write_text("approved commit bytes\n")
        (repo / "excluded.txt").write_text("SYNTHETIC EXCLUDED CANARY\n")
        (repo / "linked.txt").symlink_to("selected.txt")
        git("add", ".")
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-m", "synthetic source")
        revision = git("rev-parse", "HEAD")
        (repo / "selected.txt").write_text("DIRTY CANARY\n")
        (repo / "untracked.txt").write_text("UNTRACKED CANARY\n")
        context = relay.prepare(repo, revision, ["selected.txt"], "test-task", "Selected task", [], [], [])
        encoded = relay.canonical(context)
        self.assertIn(b"approved commit bytes", encoded)
        for canary in (b"EXCLUDED CANARY", b"DIRTY CANARY", b"UNTRACKED CANARY"):
            self.assertNotIn(canary, encoded)
        with self.assertRaisesRegex(relay.Rejected, "regular Git files"):
            relay.prepare(repo, revision, ["linked.txt"], "test-task", "Selected task", [], [], [])

    def test_prepare_ignores_replacement_refs_poisoned_git_environment_and_path_quoting(self):
        repo = self.root / "replacements"
        repo.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  check=True, text=True, env=relay.git_environment()).stdout.strip()
        git("init")
        filename = "café.txt"
        source = repo / filename
        source.write_text("ORIGINAL SELECTED BY COMMIT\n")
        def commit():
            git("add", ".")
            git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-m", "synthetic")
            return git("rev-parse", "HEAD")
        original = commit()
        source.write_text("REPLACEMENT CANARY\n")
        replacement = commit()
        git("replace", original, replacement)
        git("config", "core.quotePath", "true")
        with patch.dict("os.environ", {"GIT_DIR": str(self.root / "wrong-git-dir"),
                                       "GIT_WORK_TREE": str(self.root / "wrong-worktree"),
                                       "GIT_OBJECT_DIRECTORY": str(self.root / "wrong-objects")}):
            context = relay.prepare(repo, original, [filename], "test-task", "selected", [], [], [])
        self.assertEqual(context["source_revision"], original)
        self.assertEqual(context["files"][0]["path"], filename)
        self.assertEqual(context["files"][0]["text"], "ORIGINAL SELECTED BY COMMIT\n")
        self.assertNotIn(b"REPLACEMENT CANARY", relay.canonical(context))

    def test_new_task_preserves_other_checkpoint_and_consult_filters_scoped_memory(self):
        self.publish()
        old_context = copy.deepcopy(self.context)
        self.context["task_id"] = "another-task"
        self.publish(1, "another-publish")
        with relay.connect(self.state) as db:
            snapshot = self.service.current(db)
        self.assertEqual(snapshot["tasks"]["test-task"], old_context)
        snapshot["memory"] = [
            {"entry_id": "team", "scope": "workspace", "task_id": "test-task"},
            {"entry_id": "mine", "scope": "task", "task_id": "another-task"},
            {"entry_id": "elsewhere", "scope": "task", "task_id": "test-task"}]
        selected = relay.task_start(snapshot, "another-task", {"revision": 2})
        self.assertEqual([m["entry_id"] for m in selected["accepted_memory"]], ["team", "mine"])
        new = relay.task_start(snapshot, "brand-new-task", {"revision": 2})
        self.assertIsNone(new["checkpoint"])
        self.assertEqual([m["entry_id"] for m in new["accepted_memory"]], ["team"])
        self.assertFalse(new["execution_authorized"])

    def test_watch_resumes_cursor_and_deduplicates_event_saved_before_cursor_commit(self):
        self.publish()
        event = self.call("updates", {"after": 0})["events"][0]
        output = self.root / "watch"
        output.mkdir()
        # Simulate an interrupted watcher: event fsynced, cursor not yet replaced.
        (output / "1.json").write_bytes(relay.canonical(event))
        with patch.object(relay, "client", side_effect=lambda u,c,o,b: self.call(o,b)):
            result = relay.watch("http://127.0.0.1:8765", self.credentials["cj"], output, .03, .01)
            self.assertEqual(result["after"], 1)
            self.proposal()
            result = relay.watch("http://127.0.0.1:8765", self.credentials["cj"], output, .03, .01)
        self.assertEqual(result["after"], 2)
        self.assertEqual(sorted(p.name for p in output.glob('[0-9]*.json')), ["1.json", "2.json"])

    def test_exclusive_state_rejects_second_service_or_watcher(self):
        with relay.exclusive(self.root / "state.lock"):
            with self.assertRaisesRegex(relay.Rejected, "another process"):
                with relay.exclusive(self.root / "state.lock"):
                    self.fail("second lock accepted")

    def test_http_auth_origin_host_limits_and_normal_two_client_flow(self):
        server = relay.serve(self.service, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        result = relay.client(url, self.credentials["jp"], "publish", {
            "request_id": "via-http", "expected_revision": 0, "checkpoint": self.context})
        self.assertEqual(result["pin"]["revision"], 1)
        self.assertEqual(relay.client(url, self.credentials["cj"], "context", {})["revision"], 1)
        self.assertEqual(relay.client(url, self.credentials["cj"], "context", {},
                                      relay_port=server.server_port)["revision"], 1)
        with self.assertRaises(HTTPError) as wrong_host:
            relay.client(url, self.credentials["cj"], "context", {},
                         relay_port=1 if server.server_port != 1 else 2)
        self.assertEqual(wrong_host.exception.code, 403)
        for headers, status in (({}, 401), ({"Origin": "https://example.invalid"}, 403),
                                ({"Host": "example.invalid"}, 403)):
            request = Request(url + "/context", data=relay.canonical({"workspace": "synthetic-team"}),
                              headers=headers)
            with self.subTest(headers=headers), self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, status)

    def test_client_rejects_network_urls_before_opening_credential(self):
        for url in ("http://example.com:8765", "https://127.0.0.1:8765", "http://localhost:8765?x=1",
                    "http://secret@127.0.0.1:8765", "http://127.0.0.1:8765/elsewhere"):
            with self.subTest(url=url), self.assertRaisesRegex(relay.Rejected, "loopback"):
                relay.client(url, self.root / "missing", "context", {})

    def test_client_forwarded_port_sets_remote_loopback_host(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = relay.canonical({"workspace": "synthetic-team"})
        opener = unittest.mock.MagicMock()
        opener.open.return_value = response
        with patch.object(relay, "build_opener", return_value=opener):
            relay.client("http://127.0.0.1:43210", self.credentials["jp"], "context", {}, relay_port=8765)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:43210/context")
            self.assertEqual(request.get_header("Host"), "127.0.0.1:8765")
            relay.client("http://127.0.0.1:43210", self.credentials["jp"], "context", {})
            self.assertIsNone(opener.open.call_args.args[0].get_header("Host"))

    def test_client_rejects_invalid_remote_ports_before_credentials(self):
        for port in (True, False, 0, -1, 65536, "8765", 8765.0):
            with self.subTest(port=port), self.assertRaisesRegex(relay.Rejected, "relay port"):
                relay.client("http://127.0.0.1:43210", self.root / "missing", "context", {}, relay_port=port)

    def test_cli_init_credentials_and_state_are_owner_only_under_permissive_parent_umask(self):
        script = Path(relay.__file__).resolve()
        state = self.root / "cli-state"
        subprocess.run(["sh", "-c", 'umask 0022; exec "$@"', "sh", "python3", str(script),
                        "init", "--state", str(state), "--workspace", "synthetic-team",
                        "--owner", "jp", "--member", "cj"], check=True, capture_output=True)
        for path in [state, *state.rglob("*")]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_unreviewed_publish_payload_never_contacts_server(self):
        source = self.root / "input.json"
        source.write_text('{"checkpoint":"changed"}')
        result = subprocess.run(["python3", str(Path(relay.__file__).resolve()), "publish",
                                 "--credential", str(self.root / "missing"), "--input", str(source),
                                 "--approve-sha256", "0" * 64, "--output", str(self.root / "result")],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reviewed input hash mismatch", result.stderr)
        self.assertNotIn("missing", result.stderr)
        self.assertFalse((self.root / "result").exists())


if __name__ == "__main__":
    unittest.main()
