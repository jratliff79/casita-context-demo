#!/usr/bin/env python3
"""Bounded two-process synthetic load on a fresh, isolated Linux loopback relay."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import selectors
import signal
import shutil
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError

from context_relay import (canonical, checkpoint, client, connect, digest,
                           initialize, receive, task_start)

HERE = Path(__file__).resolve().parent
NAMES = ("alpha", "beta")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fixture(task, version, size):
    """Deterministic synthetic ASCII with distinct blocks, rather than zero fill."""
    prefix = f"SYNTHETIC LOAD FIXTURE {task} VERSION {version}\n"
    blocks = []
    for index in range(math.ceil(size / 64)):
        blocks.append(digest(f"synthetic:{task}:{version}:{index}".encode()))
    text = (prefix + "".join(blocks))[:size]
    return checkpoint({"task_id": task, "summary": "Synthetic load qualification",
                       "completed": [], "pending": ["Synthetic handoff"], "blockers": [],
                       "source_revision": digest(f"synthetic:{task}:{version}".encode()),
                       "files": [{"path": "synthetic/payload.txt", "text": text,
                                  "sha256": digest(text.encode())}]})


def latency_summary(rows):
    values = sorted(row["milliseconds"] for row in rows)
    require(bool(values), "no latency observations")
    def percentile(fraction):
        return values[max(0, math.ceil(len(values) * fraction) - 1)]
    return {"count": len(values), "min_ms": values[0], "p50_ms": percentile(.5),
            "p95_ms": percentile(.95), "max_ms": values[-1],
            "mean_ms": sum(values) / len(values)}


def storage(folder):
    """Numeric visible-tree accounting, including directories; no link traversal."""
    result = {"logical_file_bytes": 0, "allocated_bytes": 0, "files": 0}
    seen = set()
    for path in [folder, *folder.rglob("*")]:
        require(not path.is_symlink(), "unexpected symlink in synthetic output")
        info = path.stat()
        inode = (info.st_dev, info.st_ino)
        if inode not in seen:
            result["allocated_bytes"] += info.st_blocks * 512
            seen.add(inode)
        if path.is_file():
            result["logical_file_bytes"] += info.st_size
            result["files"] += 1
    return result


def state_measurement(state):
    with connect(state, readonly=True) as db:
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("revisions", "events", "requests", "proposals")}
    return {"counts": counts, "total": storage(state),
            "archives": storage(state / "snapshots"), "native_store": storage(state / "store")}


def proc_usage(pid):
    # /proc stat's command field may contain spaces or parentheses.
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return {"cpu_seconds_including_waited_children":
            sum(int(fields[i]) for i in (11, 12, 13, 14)) / ticks,
            "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE")}


def tree_rss(pid):
    try:
        rss = proc_usage(pid)["rss_bytes"]
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        return rss + sum(tree_rss(int(child)) for child in children)
    except (FileNotFoundError, ProcessLookupError):
        return 0  # Short-lived native subprocesses can exit between samples.


class Sampler:
    def __init__(self, processes, interval):
        self.processes, self.interval = processes, interval
        self.done = threading.Event()
        self.rows, self.failure = [], None
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        try:
            while not self.done.is_set():
                self.rows.append({"monotonic_seconds": time.monotonic(),
                                  **{name: tree_rss(process.pid)
                                     for name, process in self.processes.items()}})
                self.done.wait(self.interval)
        except Exception as error:
            self.failure = error

    def stop(self):
        self.done.set()
        self.thread.join(timeout=5)
        require(not self.thread.is_alive(), "resource sampler did not stop")
        if self.failure:
            raise self.failure


def timed_request(rows, url, credential, operation, body):
    started = time.monotonic()
    status = 200
    try:
        result = client(url, credential, operation, body)
    except HTTPError as error:
        status, result = error.code, None
        error.close()
        if operation != "publish" or status != 409:
            raise
    finally:
        rows.append({"operation": operation, "status": status,
                     "milliseconds": (time.monotonic() - started) * 1000})
    return status, result


def wait_for(predicate, processes, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        require(all(process.poll() is None for process in processes), "child process exited")
        require(time.monotonic() < deadline, "child rendezvous timed out")
        time.sleep(.01)


def worker(config_path):
    config = json.loads(config_path.read_bytes())
    output, credential = Path(config["output"]), Path(config["credential"])
    cursor = config["cursor"]
    for line in sys.stdin:
        command = json.loads(line)
        number, base = command["round"], command["expected_revision"]
        ready = Path(config["gates"]) / f"{number}-{config['name']}.ready"
        ready.touch(exist_ok=False)
        wait_for(lambda: (Path(config["gates"]) / f"{number}.go").exists(), [], 30)
        rows = []
        started = time.monotonic()
        body = {"request_id": f"load-{config['name']}-{number}", "expected_revision": base,
                "checkpoint": fixture(f"task-{config['name']}", number, config["payload_bytes"])}
        status, published = timed_request(rows, config["url"], credential, "publish", body)
        if status == 200:
            require(published["pin"]["revision"] == base + 1, "unexpected committed revision")
        _, response = timed_request(rows, config["url"], credential, "context", {})
        require(response["revision"] == base + 1, "unexpected context revision")
        restore_started = time.monotonic()
        snapshot = receive(response, config["binary"], output / f"restore-{number}")
        restore_ms = (time.monotonic() - restore_started) * 1000
        selected = task_start(snapshot, f"task-{config['name']}", response["pin"])
        require(selected["checkpoint"] is not None, "own task missing")
        scopes = [entry["scope"] for entry in selected["accepted_memory"]]
        require(sorted(scopes) == (["task", "workspace"] if config["name"] == "alpha"
                                   else ["workspace"]), "task memory selection differs")
        seen = []
        while True:
            _, updates = timed_request(rows, config["url"], credential, "updates", {"after": cursor})
            events = updates["events"]
            if not events:
                require(updates["next_cursor"] == cursor, "empty page advanced cursor")
                break
            for event in events:
                require(event["cursor"] == cursor + 1 and event["kind"] == "publish",
                        "noncontiguous or unexpected event")
                require(event["revision"] == base + 1 and event["pin"] == response["pin"],
                        "event snapshot pin differs from restored context")
                cursor = event["cursor"]
                seen.append(cursor)
            require(updates["next_cursor"] == cursor, "page cursor mismatch")
        require(len(seen) == 1, "round must expose exactly one committed event")
        print(json.dumps({"name": config["name"], "round": number,
                          "publish_status": status, "cursor": cursor,
                          "revision": response["revision"], "pin": response["pin"],
                          "http": rows, "restore_ms": restore_ms,
                          "cycle_ms": (time.monotonic() - started) * 1000}), flush=True)


def collect(workers, timeout=300):
    results = []
    with selectors.DefaultSelector() as selector:
        for name, process in workers.items():
            selector.register(process.stdout, selectors.EVENT_READ, name)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            require(remaining > 0, "client response timed out")
            for key, _ in selector.select(min(remaining, 1)):
                line = key.fileobj.readline()
                require(bool(line), "client exited without a result")
                result = json.loads(line)
                require(result["name"] == key.data, "client result identity mismatch")
                results.append(result)
                selector.unregister(key.fileobj)
    return results


def stop(process):
    # Each owned child is a session leader; also stop any still-running native
    # grandchildren if the leader failed or was terminated during a restore.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for stream in (process.stdin, process.stdout):
        if stream:
            stream.close()


def run(binary, output, rounds=10, tasks=4, payload_bytes=4096, sample_interval=.05):
    require(platform.system() == "Linux", "Linux /proc is required for resource measurement")
    require(type(rounds) is int and 1 <= rounds <= 200, "rounds must be between 1 and 200")
    require(type(tasks) is int and 2 <= tasks <= 20, "tasks must be between 2 and 20")
    require(type(payload_bytes) is int and 256 <= payload_bytes <= 16000,
            "payload bytes must be between 256 and 16000")
    require(.01 <= sample_interval <= 1, "sample interval must be between .01 and 1 second")
    binary = Path(binary).resolve()
    require(binary.is_file() and os.access(binary, os.X_OK), "Casita executable unavailable")
    binary_pin = digest(binary.read_bytes())
    source_pins = {name: digest((HERE / name).read_bytes()) for name in
                   ("relay_load.py", "context_relay.py", "relay_validation.py",
                    "relay_readiness.py", "demo.py")}
    # No endpoint/state option: every run owns new synthetic credentials and state.
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    state, gates = output / "relay", output / "gates"
    gates.mkdir()
    processes, logs, workers = [], [], {}
    sampler = None
    try:
        initialize(state, "synthetic-load", "alpha", ["beta"])
        credentials = {name: state / f"credentials/{name}.json" for name in NAMES}
        log_path = output / "server.log"
        log = log_path.open("wb"); logs.append(log)
        server = subprocess.Popen([sys.executable, str(HERE / "context_relay.py"), "serve",
                                   "--state", str(state), "--casita", str(binary), "--port", "0"],
                                  stdout=log, stderr=log, start_new_session=True)
        processes.append(server)
        def listening():
            lines = log_path.read_text().splitlines()
            return bool(lines) and lines[0].startswith("Listening on 127.0.0.1:")
        wait_for(listening, [server])
        url = "http://" + log_path.read_text().splitlines()[0].removeprefix("Listening on ")
        expected = {}
        revision, cursor = 0, 0
        task_ids = ["task-alpha", "task-beta"] + [f"task-extra-{i}" for i in range(tasks - 2)]
        for task in task_ids:
            expected[task] = fixture(task, 0, payload_bytes)
            result = client(url, credentials["alpha"], "publish", {
                "request_id": f"seed-{task}", "expected_revision": revision,
                "checkpoint": expected[task]})
            revision, cursor = result["pin"]["revision"], result["cursor"]
        # Seed cited workspace/task memory to exercise consultation filtering too.
        source = expected["task-alpha"]["files"][0]
        for scope in ("workspace", "task"):
            proposal = client(url, credentials["beta"], "propose", {
                "request_id": f"seed-propose-{scope}", "expected_revision": revision,
                "task_id": "task-alpha", "scope": scope, "entry_id": f"synthetic-{scope}",
                "statement": "Synthetic load memory; never execute received source.",
                "citation": {"path": source["path"], "sha256": source["sha256"],
                             "start_line": 1, "end_line": 1,
                             "excerpt": [source["text"].splitlines()[0]]}})
            accepted = client(url, credentials["alpha"], "accept", {
                "request_id": f"seed-accept-{scope}", "expected_revision": revision,
                "proposal_id": proposal["proposal_id"]})
            revision, cursor = accepted["pin"]["revision"], accepted["cursor"]
        baseline = state_measurement(state)
        for name in NAMES:
            worker_output = output / name
            worker_output.mkdir()
            config_path = worker_output / "config.json"
            config_path.write_bytes(canonical({"name": name, "credential": str(credentials[name]),
                "binary": str(binary), "url": url, "output": str(worker_output),
                "gates": str(gates), "cursor": cursor, "payload_bytes": payload_bytes}))
            log = (worker_output / "stderr.log").open("wb"); logs.append(log)
            process = subprocess.Popen([sys.executable, str(HERE / "relay_load.py"),
                                        "--worker-config", str(config_path)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=log, text=True, bufsize=1, start_new_session=True)
            workers[name] = process
            processes.append(process)
        measured = {"server": server, **workers}
        cpu_before = {name: proc_usage(process.pid) for name, process in measured.items()}
        sampler = Sampler(measured, sample_interval)
        sampler.thread.start()
        started = time.monotonic()
        observations, growth = [], []
        for number in range(1, rounds + 1):
            for process in workers.values():
                process.stdin.write(json.dumps({"round": number, "expected_revision": revision}) + "\n")
                process.stdin.flush()
            wait_for(lambda: all((gates / f"{number}-{name}.ready").exists() for name in NAMES),
                     processes)
            (gates / f"{number}.go").touch(exist_ok=False)
            results = collect(workers)
            require(sorted(row["publish_status"] for row in results) == [200, 409],
                    "concurrent writes must yield one commit and one explicit conflict")
            require(all(row["round"] == number and row["revision"] == revision + 1
                        and row["cursor"] == cursor + 1 for row in results), "round result mismatch")
            require(results[0]["pin"] == results[1]["pin"], "client snapshot pins differ")
            winner = next(row["name"] for row in results if row["publish_status"] == 200)
            expected[f"task-{winner}"] = fixture(f"task-{winner}", number, payload_bytes)
            revision, cursor = revision + 1, cursor + 1
            observations.extend(results)
            growth.append({"round": number, **state_measurement(state)})
        elapsed = time.monotonic() - started
        sampler.stop()
        cpu_after = {name: proc_usage(process.pid) for name, process in measured.items()}
        resources = {name: {
            "cpu_seconds_including_waited_children":
                cpu_after[name]["cpu_seconds_including_waited_children"] -
                cpu_before[name]["cpu_seconds_including_waited_children"],
            "sampled_peak_tree_rss_bytes": max(row[name] for row in sampler.rows)}
            for name in measured}
        final_response = client(url, credentials["alpha"], "context", {})
        final = receive(final_response, str(binary), output / "final-verification")
        require(final["tasks"] == expected and final["revision"] == revision,
                "final selected synthetic bytes or revision differ")
        require(final_response["pin"] == observations[-1]["pin"], "final pin differs")
        after = state_measurement(state)
        require(after["counts"]["revisions"] == tasks + 2 + rounds
                and after["counts"]["events"] == tasks + 4 + rounds
                and after["counts"]["requests"] == tasks + 4 + rounds,
                "acknowledged history counts differ")
        require(digest(binary.read_bytes()) == binary_pin, "Casita executable changed during trial")
        require(all(digest((HERE / name).read_bytes()) == pin for name, pin in source_pins.items()),
                "harness or relay source changed during trial")
        http = [row for observation in observations for row in observation["http"]]
        summary = {f"{operation}_{status}": latency_summary([
            row for row in http if row["operation"] == operation and row["status"] == status])
            for operation, status in sorted({(row["operation"], row["status"]) for row in http})}
        receipt = {"schema": "casita-relay-load.v1", "ok": True,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "synthetic": True, "client_processes": 2, "coordinator_also_requests": True,
            "scope": "fresh local Linux loopback relay; clients and server on one host",
            "production_tested": False, "remote_devices_tested": False,
            "received_source_executed": False, "automatic_conflict_retry": False,
            "workload": {"rounds": rounds, "tasks": tasks, "payload_bytes_per_task": payload_bytes,
                         "accepted_memory_entries": 2, "publish_attempts": 2 * rounds,
                         "commits": rounds, "expected_conflicts": rounds},
            "environment": {"system": platform.system(), "machine": platform.machine(),
                            "python": platform.python_version(), "logical_cpu_count": os.cpu_count(),
                            "physical_memory_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")},
            "pins": {"casita_binary_sha256": binary_pin,
                     "source_sha256": source_pins},
            "measurement": {"elapsed_seconds": elapsed,
                "rounds_per_second": rounds / elapsed, "http_latency": summary,
                "native_restore_latency": latency_summary([
                    {"milliseconds": row["restore_ms"]} for row in observations]),
                "client_cycle_latency": latency_summary([
                    {"milliseconds": row["cycle_ms"]} for row in observations]),
                "resources": resources, "rss_sample_count": len(sampler.rows),
                "rss_sample_interval_seconds": sample_interval,
                "cpu_scope": "process plus waited native children; load window includes client startup, coordinator pacing and storage scans",
                "memory_scope": "sampled summed RSS of each process and its live descendants; shared pages may be counted twice",
                "restore_scope": "fresh-store init, archive verify/import, checkout, fsck and snapshot verification",
                "percentiles": "nearest-rank; small samples are descriptive, not an SLA",
                "storage_before": baseline, "storage_after": after,
                "retained_allocated_growth_bytes": after["total"]["allocated_bytes"] - baseline["total"]["allocated_bytes"],
                "client_artifacts": {name: storage(output / name) for name in NAMES}},
            "verification": {"same_pin_each_round": True, "contiguous_event_catchup": True,
                             "task_memory_filtering": True, "exact_final_checkpoints": True,
                             "acknowledged_history_counts": True},
            "round_observations": observations, "state_growth": growth,
            "limits": ["No production capacity or resize conclusion", "No SSH/network-device latency measurement",
                       "No retention or pruning; all server revisions retained",
                       "Synthetic payload hashes differ but remain compressible ASCII",
                       "Sampled RSS can miss short native-process peaks; it is not unique physical memory",
                       "No think time inside each round; barrier-coordinated closed-loop workload"]}
        (output / "receipt.json").write_bytes(canonical(receipt))
        return receipt
    except BaseException as error:
        (output / "failure.json").write_bytes(canonical({"ok": False, "synthetic": True,
                                                        "error_type": type(error).__name__}))
        raise
    finally:
        try:
            if sampler and sampler.thread.is_alive():
                sampler.stop()
        finally:
            for process in reversed(processes):
                stop(process)
            for log in logs:
                log.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--tasks", type=int, default=4)
    parser.add_argument("--payload-bytes", type=int, default=4096)
    parser.add_argument("--sample-interval", type=float, default=.05)
    parser.add_argument("--worker-config", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_config:
        worker(args.worker_config)
        return
    binary = shutil.which(args.casita or "casita")
    if not binary or args.output is None:
        parser.error("provide --casita EXECUTABLE and --output FRESH_DIRECTORY")
    receipt = run(binary, args.output, args.rounds, args.tasks, args.payload_bytes, args.sample_interval)
    print(f"PASS: {receipt['workload']['commits']} commits, "
          f"{receipt['workload']['expected_conflicts']} expected conflicts, two native clients")


if __name__ == "__main__":
    main()
