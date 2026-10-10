#!/usr/bin/env python3
"""Two scripted client processes, real HTTP and Casita, synthetic source only."""
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError

from context_relay import client, canonical, digest, checkpoint

SCRIPT = Path(__file__).with_name("context_relay.py").resolve()


def run(binary, output):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = str(Path(binary).resolve())
    state = output / "relay"
    processes = []
    logs = []
    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=60, check=True)
    cli("init", "--state", state, "--workspace", "synthetic-team", "--owner", "jp", "--member", "cj")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = "http://127.0.0.1:" + str(port)
    jp, cj = state / "credentials/jp.json", state / "credentials/cj.json"
    def start():
        log = (output / ("server-" + str(len(logs)) + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen([sys.executable, str(SCRIPT), "serve", "--state", str(state),
                                    "--casita", binary, "--port", str(port)], stdout=log, stderr=log)
        processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("relay failed to start")
            try:
                client(url, jp, "updates", {"after": 0})
                return process
            except OSError:
                time.sleep(.05)
        raise RuntimeError("relay start timed out")
    def stop(process):
        process.terminate()
        process.wait(timeout=10)
    controls = {}
    def rejected(label, credential, operation, body, status):
        try:
            client(url, credential, operation, body)
        except HTTPError as error:
            if error.code != status:
                raise
            controls[label] = True
        else:
            raise ValueError("negative control accepted: " + label)
    def submit(person, operation, body, label):
        path = output / (label + ".json")
        raw = canonical(body)
        path.write_bytes(raw)
        response = output / (label + "-response.json")
        cli(operation, "--url", url, "--credential", person, "--input", path,
            "--approve-sha256", digest(raw), "--output", response)
        return json.loads(response.read_bytes())
    try:
        service = start()
        policy = "SYNTHETIC EVENTOOLS TASK\nRun the focused checks before handing off the change.\n"
        context = checkpoint({"task_id": "synthetic-eventools-task", "summary": "Synthetic sharing trial",
                              "completed": ["read selected policy"], "pending": ["choose focused checks"],
                              "blockers": [], "source_revision": "0" * 40,
                              "files": [{"path": "source/policy.txt", "text": policy,
                                         "sha256": digest(policy.encode())}]})
        publish = {"request_id": "jp-checkpoint-1", "expected_revision": 0, "checkpoint": context}
        first = submit(jp, "publish", publish, "publish")
        assert client(url, jp, "publish", publish) == first
        controls["duplicate_submission_reused"] = True
        altered = copy.deepcopy(publish)
        altered["checkpoint"]["summary"] = "different bytes"
        rejected("idempotency_reuse", jp, "publish", altered, 409)
        cli("context", "--url", url, "--credential", cj, "--casita", binary,
            "--output", output / "cj-context-1")
        received = json.loads((output / "cj-context-1/received/snapshot.json").read_bytes())
        assert received["tasks"][context["task_id"]] == context and received["memory"] == []
        propose = {"request_id": "cj-proposal-1", "expected_revision": 1,
                   "task_id": context["task_id"], "scope": "workspace", "entry_id": "handoff-checks", "statement": "Include focused check evidence in handoffs.",
                   "citation": {"path": "source/policy.txt", "sha256": digest(policy.encode()),
                                "start_line": 2, "end_line": 2,
                                "excerpt": [policy.splitlines()[1]]}}
        proposal = submit(cj, "propose", propose, "proposal")
        forged = copy.deepcopy(propose)
        forged["request_id"] = "forged-citation"
        forged["citation"]["excerpt"] = ["not selected source"]
        rejected("forged_citation", cj, "propose", forged, 400)
        accept = {"request_id": "jp-accept-1", "expected_revision": 1,
                  "proposal_id": proposal["proposal_id"]}
        rejected("member_cannot_accept", cj, "accept", accept, 403)
        submit(jp, "accept", accept, "accept")
        assert client(url, jp, "accept", accept)["pin"]["revision"] == 2
        stale = dict(publish, request_id="stale-publish")
        rejected("stale_write", cj, "publish", stale, 409)
        stale_proposal = dict(propose, request_id="stale-proposal")
        rejected("stale_proposal", cj, "propose", stale_proposal, 409)
        stop(service)
        service = start()
        controls["restart_recovery"] = client(url, jp, "context", {})["revision"] == 2
        # CJ missed the proposal/acceptance live; it resumes using its saved cursor.
        events = client(url, cj, "updates", {"after": first["cursor"]})
        assert [e["kind"] for e in events["events"]] == ["propose", "accept"]
        assert not client(url, cj, "updates", {"after": events["next_cursor"]})["events"]
        controls["offline_catchup"] = True
        cli("context", "--url", url, "--credential", cj, "--casita", binary,
            "--output", output / "cj-context-2")
        accepted = json.loads((output / "cj-context-2/received/snapshot.json").read_bytes())
        assert accepted["memory"][0]["accepted_by"] == "jp"
        assert accepted["memory"][0]["author"] == "cj"
        cli("consult", "--url", url, "--credential", cj, "--casita", binary,
            "--task", "brand-new-task", "--output", output / "cj-new-task")
        task_start = json.loads((output / "cj-new-task/task-start.json").read_bytes())
        assert task_start["checkpoint"] is None and len(task_start["accepted_memory"]) == 1
        controls["new_task_consults_workspace_memory"] = True
        for _ in range(2):
            cli("watch", "--url", url, "--credential", cj, "--seconds", 1,
                "--output", output / "cj-watch")
        assert len(list((output / "cj-watch").glob('[0-9]*.json'))) == 3
        controls["watch_resume_without_duplicate_events"] = True
        bad = output / "bad-credential.json"
        bad.write_bytes(canonical({"workspace": "synthetic-team", "token": "x" * 43}))
        rejected("invalid_token", bad, "context", {}, 401)
        other = json.loads(cj.read_bytes())
        other["workspace"] = "other-team"
        bad.write_bytes(canonical(other))
        rejected("wrong_workspace", bad, "context", {}, 403)
        cli("revoke", "--state", state, "--member", "cj")
        rejected("revoked_reads", cj, "context", {}, 401)
        rejected("revoked_idempotent_write", cj, "propose", propose, 401)
        assert client(url, jp, "context", {})["revision"] == 2
        controls["independent_member_survives_revocation"] = True
        assert all(controls.values())
        receipt = {"ok": True, "synthetic": True, "scripted_clients": True,
                   "client_processes": 2, "real_http": True, "casita_restore_verified": True,
                   "shared_revision": 2, "negative_controls": controls,
                   "actual_cj_participated": False, "eventools_source_shared": False,
                   "remote_devices_tested": False, "codex_auto_wakeup": False,
                   "received_source_executed": False}
        (output / "receipt.json").write_bytes(canonical(receipt))
        print("PASS: two authenticated client processes, Casita restore, reviewed memory, restart and catch-up")
        return receipt
    finally:
        for process in processes:
            if process.poll() is None:
                stop(process)
        for log in logs:
            log.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.casita, args.output)


if __name__ == "__main__":
    main()
