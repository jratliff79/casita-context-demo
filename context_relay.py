#!/usr/bin/env python3
"""Opt-in, one-workspace context relay. Bind only to loopback; use SSH remotely."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from authentication import read_regular
from demo import Casita, canonical, digest, file_map
from git_review import git_environment, git_lines

LIMIT = 400_000
WIRE_LIMIT = 2_000_000
SCHEMA = "casita-context-relay.v1"


class Rejected(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def require(ok, message, status=400):
    if not ok:
        raise Rejected(message, status)


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value)


def text(value, limit=2000):
    return isinstance(value, str) and 0 < len(value) <= limit and "\x00" not in value


def safe_path(value):
    if not text(value, 250) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and str(path) == value
            and all(p not in (".", "..") and not p.startswith(".") for p in path.parts)
            and path.suffix.lower() not in (".pem", ".key", ".p12", ".pfx")
            and path.name.lower() not in ("credentials", "id_rsa", "id_ed25519"))


def checkpoint(value):
    require(isinstance(value, dict) and set(value) == {
        "task_id", "summary", "completed", "pending", "blockers", "source_revision", "files"},
        "invalid checkpoint fields")
    require(identifier(value["task_id"]) and text(value["summary"]), "invalid task or summary")
    require(isinstance(value["source_revision"], str)
            and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value["source_revision"]),
            "source revision must be a full Git commit")
    for field in ("completed", "pending", "blockers"):
        require(isinstance(value[field], list) and len(value[field]) <= 20
                and all(text(v, 500) for v in value[field]), "invalid task progress")
    files = value["files"]
    require(isinstance(files, list) and 1 <= len(files) <= 20, "select one to twenty source files")
    seen = set()
    for source in files:
        require(isinstance(source, dict) and set(source) == {"path", "text", "sha256"},
                "invalid selected source")
        require(safe_path(source["path"]) and source["path"] not in seen,
                "invalid or duplicate selected path")
        require(text(source["text"], 100_000)
                and digest(source["text"].encode()) == source["sha256"], "source hash mismatch")
        seen.add(source["path"])
    require(len(canonical(value)) <= LIMIT, "checkpoint exceeds byte limit")
    return value


def citation(value, context):
    require(isinstance(value, dict) and set(value) == {
        "path", "sha256", "start_line", "end_line", "excerpt"}, "invalid citation")
    source = next((s for s in context["files"] if s["path"] == value["path"]), None)
    start, end = value["start_line"], value["end_line"]
    require(source is not None and type(start) is int and type(end) is int,
            "citation source or lines missing")
    lines = git_lines(source["text"].encode("utf-8"))
    require(1 <= start <= end <= len(lines) and end - start < 40
            and value["sha256"] == source["sha256"]
            and value["excerpt"] == lines[start - 1:end], "citation differs from selected source")


@contextmanager
def connect(state):
    db = sqlite3.connect(str(state / "relay.sqlite"), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA synchronous = FULL")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(state, workspace, owner, members):
    require(identifier(workspace) and identifier(owner) and owner not in members
            and 1 <= len(members) <= 10 and len(set(members)) == len(members)
            and all(identifier(m) for m in members), "invalid workspace members")
    state.mkdir(parents=True, exist_ok=False, mode=0o700)
    (state / "credentials").mkdir(mode=0o700)
    (state / "snapshots").mkdir(mode=0o700)
    with connect(state) as db:
        db.executescript("""
            CREATE TABLE settings(workspace TEXT, owner TEXT);
            CREATE TABLE members(name TEXT PRIMARY KEY, token_hash TEXT UNIQUE, enabled INTEGER);
            CREATE TABLE revisions(revision INTEGER PRIMARY KEY, snapshot TEXT, pin TEXT);
            CREATE TABLE proposals(id TEXT PRIMARY KEY, payload TEXT, accepted INTEGER DEFAULT 0);
            CREATE TABLE events(cursor INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT);
            CREATE TABLE requests(actor TEXT, id TEXT, hash TEXT, result TEXT, PRIMARY KEY(actor,id));
        """)
        db.execute("INSERT INTO settings VALUES (?,?)", (workspace, owner))
        for member in [owner] + members:
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO members VALUES (?,?,1)", (member, digest(token.encode())))
            (state / "credentials" / (member + ".json")).write_bytes(canonical({
                "workspace": workspace, "token": token}))


class DiscardDiagnostics:
    def append(self, command):
        pass  # Long-running relay: do not retain argv or stdout/stderr histories.


def revoke_member(state, member):
    with connect(state) as db:
        owner = db.execute("SELECT owner FROM settings").fetchone()["owner"]
        require(member != owner, "cannot revoke the sole workspace owner")
        require(db.execute("UPDATE members SET enabled=0 WHERE name=?", (member,)).rowcount == 1,
                "unknown member")


class Relay:
    """One local process serializes Casita operations; SQLite commits head + event together."""
    def __init__(self, state, binary):
        self.state, self.binary = Path(state), str(Path(binary).resolve())
        require(self.state.is_dir() and not self.state.is_symlink(), "invalid relay state")
        with connect(self.state) as db:
            settings = db.execute("SELECT * FROM settings").fetchone()
            self.workspace, self.owner = settings["workspace"], settings["owner"]
        self.store = Casita(self.binary, self.state / "store", DiscardDiagnostics())
        if not (self.state / "store").exists():
            self.store.run("init")

    def authenticate(self, db, token):
        require(isinstance(token, str) and 30 <= len(token) <= 100, "unauthorized", 401)
        member = db.execute("SELECT name FROM members WHERE token_hash=? AND enabled=1",
                            (digest(token.encode()),)).fetchone()
        require(member is not None, "unauthorized", 401)
        return member["name"]

    @staticmethod
    def current(db):
        row = db.execute("SELECT * FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
        return json.loads(row["snapshot"]) if row else None

    def save_snapshot(self, db, snapshot):
        raw = canonical(snapshot)
        require(len(raw) <= LIMIT, "shared state exceeds byte limit")
        # Unique roots retain every published revision. Orphans from a failed commit
        # remain private and unreachable through the API; no background GC in this pilot.
        root = "relay/" + secrets.token_hex(16)
        with tempfile.TemporaryDirectory(dir=self.state) as temp:
            folder = Path(temp) / "bundle"
            folder.mkdir()
            (folder / "snapshot.json").write_bytes(raw)
            self.store.run("import", folder, "--root", root)
            key = self.store.roots()[root]
            archive = Path(temp) / "snapshot.casitar"
            self.store.run("archive", "create", "--root", root, "--output", archive, "--json")
            self.store.run("fsck", "--dry-run")
            self.store.run("checkout", key, Path(temp) / "verified", "--no-root")
            require(file_map(Path(temp) / "verified") == {"snapshot.json": digest(raw)},
                    "Casita snapshot verification failed")
            encoded = read_regular(archive, WIRE_LIMIT // 2)
            archive_id = digest(encoded)
            pin = {"revision": snapshot["revision"], "directory_key": key,
                   "snapshot_sha256": digest(raw), "archive_sha256": archive_id}
            destination = self.state / "snapshots" / (archive_id + ".casitar")
            # Write archive durably before exposing its index in the SQLite commit.
            with destination.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            fd = os.open(str(destination.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        db.execute("INSERT INTO revisions VALUES (?,?,?)",
                   (snapshot["revision"], raw.decode(), canonical(pin).decode()))
        return pin

    def handle(self, token, operation, body):
        require(isinstance(body, dict), "body must be a JSON object")
        with connect(self.state) as db:
            # Reload membership every request, including reads and idempotent retries.
            actor = self.authenticate(db, token)
            require(body.get("workspace") == self.workspace, "workspace unavailable", 403)
            if operation == "context":
                require(set(body) == {"workspace"}, "invalid context request")
                row = db.execute("SELECT pin FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
                if not row:
                    return {"workspace": self.workspace, "revision": 0, "pin": None, "archive": None}
                pin = json.loads(row["pin"])
                raw = read_regular(self.state / "snapshots" / (pin["archive_sha256"] + ".casitar"),
                                   WIRE_LIMIT // 2)
                require(digest(raw) == pin["archive_sha256"], "stored archive damaged", 503)
                return {"workspace": self.workspace, "revision": pin["revision"], "pin": pin,
                        "archive": base64.b64encode(raw).decode("ascii")}
            if operation == "updates":
                require(set(body) == {"workspace", "after"} and type(body["after"]) is int
                        and body["after"] >= 0, "invalid update cursor")
                rows = db.execute("SELECT * FROM events WHERE cursor>? ORDER BY cursor LIMIT 100",
                                  (body["after"],)).fetchall()
                events = []
                for row in rows:
                    event = {"cursor": row["cursor"], **json.loads(row["payload"])}
                    if len(canonical(events + [event])) > WIRE_LIMIT // 2:
                        break
                    events.append(event)
                return {"events": events,
                        "next_cursor": events[-1]["cursor"] if events else body["after"]}
            require(operation in ("publish", "propose", "accept"), "unknown operation", 404)
            require(identifier(body.get("request_id")), "invalid idempotency key")
            db.execute("BEGIN IMMEDIATE")
            request_hash = digest(canonical({"operation": operation, "body": body}))
            previous = db.execute("SELECT * FROM requests WHERE actor=? AND id=?",
                                  (actor, body["request_id"])).fetchone()
            if previous:
                require(previous["hash"] == request_hash, "idempotency key reused", 409)
                return json.loads(previous["result"])
            current = self.current(db)
            revision = current["revision"] if current else 0
            require(type(body.get("expected_revision")) is int
                    and body["expected_revision"] == revision, "stale revision; refresh first", 409)
            now = datetime.now(timezone.utc).isoformat()
            envelope = {"schema": SCHEMA, "workspace": self.workspace, "revision": revision + 1,
                        "parent_revision": revision, "author": actor, "created_at": now}
            common = {"workspace", "request_id", "expected_revision"}
            if operation == "publish":
                require(set(body) == common | {"checkpoint"}, "invalid publish request")
                selected = checkpoint(body["checkpoint"])
                tasks = dict(current["tasks"]) if current else {}
                tasks[selected["task_id"]] = selected
                require(len(tasks) <= 20, "workspace task limit reached")
                snapshot = {**envelope, "tasks": tasks,
                            "memory": current["memory"] if current else [], "decision": None}
                pin = self.save_snapshot(db, snapshot)
                result = {"pin": pin}
            elif operation == "propose":
                require(current is not None, "publish context first", 409)
                require(set(body) == common | {"task_id", "scope", "entry_id", "statement", "citation"}
                        and identifier(body["task_id"]) and body["scope"] in ("task", "workspace")
                        and identifier(body["entry_id"]) and text(body["statement"], 1000),
                        "invalid memory proposal")
                require(body["task_id"] in current["tasks"], "task unavailable", 404)
                context = current["tasks"][body["task_id"]]
                citation(body["citation"], context)
                proposal = {"author": actor, "base_revision": revision, "created_at": now,
                            "task_id": body["task_id"], "scope": body["scope"],
                            "source_revision": context["source_revision"],
                            "entry_id": body["entry_id"], "statement": body["statement"],
                            "citation": body["citation"]}
                proposal_id = digest(canonical(proposal))
                db.execute("INSERT INTO proposals(id,payload) VALUES (?,?)",
                           (proposal_id, canonical(proposal).decode()))
                result = {"proposal_id": proposal_id, "proposal": proposal}
            else:
                require(actor == self.owner, "only the workspace owner may accept memory", 403)
                require(set(body) == common | {"proposal_id"}, "invalid acceptance request")
                row = db.execute("SELECT * FROM proposals WHERE id=?", (body["proposal_id"],)).fetchone()
                require(row is not None, "proposal unavailable", 404)
                proposal = json.loads(row["payload"])
                require(not row["accepted"] and proposal["base_revision"] == revision,
                        "proposal is stale or already accepted", 409)
                memory = [m for m in current["memory"] if not (
                    m["entry_id"] == proposal["entry_id"] and m["scope"] == proposal["scope"]
                    and (m["scope"] == "workspace" or m["task_id"] == proposal["task_id"]))]
                require(len(memory) < 50, "shared memory entry limit reached")
                memory.append({**proposal, "accepted_by": actor, "accepted_at": now,
                               "proposal_id": body["proposal_id"]})
                snapshot = {**envelope, "tasks": current["tasks"], "memory": memory,
                            "decision": {"proposal_id": body["proposal_id"], "accepted_by": actor}}
                pin = self.save_snapshot(db, snapshot)
                db.execute("UPDATE proposals SET accepted=1 WHERE id=?", (body["proposal_id"],))
                result = {"pin": pin}
            event = {"kind": operation, "author": actor, "created_at": now,
                     "revision": revision if operation == "propose" else revision + 1, **result}
            db.execute("INSERT INTO events(payload) VALUES (?)", (canonical(event).decode(),))
            result = {**result, "cursor": db.execute("SELECT last_insert_rowid()").fetchone()[0]}
            db.execute("INSERT INTO requests VALUES (?,?,?,?)",
                       (actor, body["request_id"], request_hash, canonical(result).decode()))
            db.commit()
            return result


def serve(relay, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass  # No credentials, request content or query strings in logs.

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                require(self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",
                        f"localhost:{self.server.server_port}"), "invalid Host", 403)
                require(not self.headers.get("Origin"), "browser origins are disabled", 403)
                require(not self.headers.get("Transfer-Encoding")
                        and len(self.headers.get_all("Content-Length", [])) == 1,
                        "one content length required")
                length = int(self.headers["Content-Length"])
                require(0 < length <= LIMIT + 5000, "request exceeds byte limit", 413)
                require(self.path in ("/context", "/updates", "/publish", "/propose", "/accept"),
                        "unknown endpoint", 404)
                auth = self.headers.get("Authorization", "")
                require(auth.startswith("Bearer "), "unauthorized", 401)
                raw = self.rfile.read(length)
                require(len(raw) == length, "incomplete body")
                result = relay.handle(auth[7:], self.path[1:], json.loads(raw))
                status = 200
            except Rejected as error:
                status, result = error.status, {"error": str(error)}
            except (ValueError, KeyError, TypeError):
                status, result = 400, {"error": "invalid request"}
            except Exception:
                status, result = 503, {"error": "relay operation failed; no update acknowledged"}
            encoded = canonical(result)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return HTTPServer(("127.0.0.1", port), Handler)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Rejected("redirect rejected")


def client(url, credential, operation, body):
    target = urlsplit(url)
    require(target.scheme == "http" and target.hostname in ("127.0.0.1", "localhost")
            and target.port is not None and target.path in ("", "/") and not target.query
            and not target.fragment and not target.username and not target.password,
            "use a loopback URL, with SSH forwarding for another machine")
    config = json.loads(read_regular(credential, 4096))
    request = Request(url.rstrip("/") + "/" + operation,
                      data=canonical({"workspace": config["workspace"], **body}),
                      headers={"Authorization": "Bearer " + config["token"],
                               "Content-Type": "application/json"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(WIRE_LIMIT + 1)
    require(len(raw) <= WIRE_LIMIT, "response exceeds limit")
    result = json.loads(raw)
    if operation == "context":
        require(result.get("workspace") == config["workspace"], "response workspace mismatch")
    return result


def receive(response, binary, output):
    """Restore a verified snapshot in a fresh client store; never execute source."""
    pin = response["pin"]
    require(isinstance(pin, dict) and set(pin) == {
        "revision", "directory_key", "snapshot_sha256", "archive_sha256"}
        and type(pin["revision"]) is int and pin["revision"] > 0
        and pin["revision"] == response["revision"], "invalid snapshot pin")
    raw = base64.b64decode(response["archive"], validate=True)
    require(len(raw) <= WIRE_LIMIT // 2 and digest(raw) == pin["archive_sha256"],
            "archive hash mismatch")
    output.mkdir(parents=True, exist_ok=False)
    try:
        archive = output / "snapshot.casitar"
        archive.write_bytes(raw)
        store = Casita(str(Path(binary).resolve()), output / "store", [])
        store.run("init")
        store.run("archive", "verify", archive, "--json")
        imported = json.loads(store.run("archive", "import", archive,
                                        "--root-prefix", "received", "--json"))
        require(len(imported["mappings"]) == 1
                and imported["mappings"][0]["root"] == pin["directory_key"], "archive root mismatch")
        store.run("checkout", pin["directory_key"], output / "received", "--no-root")
        store.run("fsck", "--dry-run")
        require(file_map(output / "received") == {"snapshot.json": pin["snapshot_sha256"]},
                "snapshot content mismatch")
        snapshot = json.loads(read_regular(output / "received/snapshot.json", LIMIT))
        require(snapshot["schema"] == SCHEMA and snapshot["revision"] == pin["revision"]
                and snapshot["workspace"] == response["workspace"],
                "snapshot metadata mismatch")
        require(isinstance(snapshot["tasks"], dict) and 1 <= len(snapshot["tasks"]) <= 20,
                "invalid workspace task index")
        for task_id, context in snapshot["tasks"].items():
            checkpoint(context)
            require(context["task_id"] == task_id, "task index mismatch")
        (output / "verified-pin.json").write_bytes(canonical(pin))
        return snapshot
    except BaseException:
        # No usable context remains after a failed validation.
        import shutil
        shutil.rmtree(output)
        raise


def prepare(repo, revision, selected, task, summary, completed, pending, blockers):
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is not None,
            "choose a full immutable commit")
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, timeout=30, env=git_environment()).stdout
    require(git("rev-parse", revision + "^{commit}").decode().strip() == revision,
            "revision must name the exact commit")
    require(1 <= len(selected) <= 20 and len(set(selected)) == len(selected), "invalid selection")
    files = []
    for path in selected:
        require(safe_path(path), "invalid selected path")
        entries = [entry for entry in git("ls-tree", "--full-tree", "-z", revision, "--", path).split(b"\x00")
                   if entry]
        require(len(entries) == 1, "select regular Git files only")
        metadata, recorded_path = entries[0].split(b"\t", 1)
        require(recorded_path.decode("utf-8") == path and metadata.split()[0] in (b"100644", b"100755"),
                "select regular Git files only")
        size = int(git("cat-file", "-s", revision + ":" + path))
        require(0 < size <= 100_000, "selected source exceeds limit")
        raw = git("show", revision + ":" + path)
        files.append({"path": path, "text": raw.decode("utf-8"), "sha256": digest(raw)})
    return checkpoint({"task_id": task, "summary": summary, "completed": completed,
                       "pending": pending, "blockers": blockers, "source_revision": revision,
                       "files": files})


@contextmanager
def exclusive(path):
    with path.open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("another process already owns this state")
        yield


def watch(url, credential, output, seconds, interval=2):
    """Persist events before advancing the cursor. Resume without replaying files."""
    config = json.loads(read_regular(credential, 4096))
    require(not output.is_symlink(), "watch output must not be a link")
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output / "watch.lock"):
        cursor_file = output / "cursor.json"
        saved = json.loads(read_regular(cursor_file, 4096)) if cursor_file.exists() else {
            "workspace": config["workspace"], "after": 0}
        require(saved["workspace"] == config["workspace"] and type(saved["after"]) is int
                and saved["after"] >= 0, "watch workspace or cursor mismatch")
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            updates = client(url, credential, "updates", {"after": saved["after"]})
            for event in updates["events"]:
                require(type(event["cursor"]) is int and event["cursor"] > saved["after"],
                        "invalid event sequence")
                path = output / (str(event["cursor"]) + ".json")
                raw = canonical(event)
                if path.exists():
                    require(read_regular(path, WIRE_LIMIT) == raw, "saved event differs from relay")
                else:
                    with path.open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                saved["after"] = event["cursor"]
                print("Update " + str(event["cursor"]) + ": " + event["kind"]
                      + ", revision " + str(event["revision"]), flush=True)
            require(updates["next_cursor"] == saved["after"], "invalid next cursor")
            temp = output / "cursor.tmp"
            with temp.open("wb") as stream:
                stream.write(canonical(saved))
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(cursor_file)
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        return saved


def task_start(snapshot, task, pin):
    require(identifier(task), "invalid task identifier")
    return {"schema": "casita-task-start.v1", "workspace": snapshot["workspace"],
            "pin": pin, "task_id": task, "checkpoint": snapshot["tasks"].get(task),
            "accepted_memory": [entry for entry in snapshot["memory"]
                                if entry["scope"] == "workspace" or entry["task_id"] == task],
            "instructions_are_data": True, "execution_authorized": False}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--state", type=Path, required=True)
    init.add_argument("--workspace", required=True)
    init.add_argument("--owner", required=True)
    init.add_argument("--member", action="append", required=True)
    start = sub.add_parser("serve")
    start.add_argument("--state", type=Path, required=True)
    start.add_argument("--casita", required=True)
    start.add_argument("--port", type=int, default=8765)
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--state", type=Path, required=True)
    revoke.add_argument("--member", required=True)
    staging = sub.add_parser("prepare")
    staging.add_argument("--repo", type=Path, required=True)
    staging.add_argument("--revision", required=True)
    staging.add_argument("--file", action="append", required=True)
    staging.add_argument("--task", required=True)
    staging.add_argument("--summary", required=True)
    for name in ("completed", "pending", "blocker"):
        staging.add_argument("--" + name, action="append", default=[])
    staging.add_argument("--output", type=Path, required=True)
    staging.add_argument("--expected-revision", type=int, required=True)
    watching = sub.add_parser("watch")
    watching.add_argument("--url", default="http://127.0.0.1:8765")
    watching.add_argument("--credential", type=Path, required=True)
    watching.add_argument("--output", type=Path, required=True)
    watching.add_argument("--seconds", type=int, default=300)
    for name in ("context", "consult", "updates", "publish", "propose", "accept"):
        p = sub.add_parser(name)
        p.add_argument("--url", default="http://127.0.0.1:8765")
        p.add_argument("--credential", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
        if name in ("context", "consult"):
            p.add_argument("--casita", required=True)
            if name == "consult":
                p.add_argument("--task", required=True)
        elif name == "updates":
            p.add_argument("--after", type=int, required=True)
        else:
            p.add_argument("--input", type=Path, required=True)
            p.add_argument("--approve-sha256", required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.state, args.workspace, args.owner, args.member)
        print("Created owner-only state and per-member credential files. Keep credentials private.")
    elif args.command == "serve":
        with exclusive(args.state / "server.lock"):
            server = serve(Relay(args.state, args.casita), args.port)
            print("Listening on 127.0.0.1:" + str(server.server_port), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
    elif args.command == "revoke":
        revoke_member(args.state, args.member)
        print("Revoked future requests. Already delivered copies remain with their recipients.")
    elif args.command == "prepare":
        data = prepare(args.repo, args.revision, args.file, args.task, args.summary,
                       args.completed, args.pending, args.blocker)
        require(args.expected_revision >= 0, "invalid expected revision")
        raw = canonical({"request_id": "checkpoint-" + secrets.token_hex(16),
                         "expected_revision": args.expected_revision, "checkpoint": data})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(raw)
        print("Review all selected bytes before publishing. SHA-256: " + digest(raw))
    elif args.command == "watch":
        require(1 <= args.seconds <= 86400, "watch for one second to one day; rerun to resume")
        watch(args.url, args.credential, args.output, args.seconds)
    else:
        require(not args.output.exists(), "use a fresh output path")
        if args.command in ("publish", "propose", "accept"):
            raw = read_regular(args.input, LIMIT + 5000)
            require(digest(raw) == args.approve_sha256, "reviewed input hash mismatch")
            body = json.loads(raw)
        else:
            body = {"after": args.after} if args.command == "updates" else {}
        operation = "context" if args.command == "consult" else args.command
        result = client(args.url, args.credential, operation, body)
        if args.command in ("context", "consult"):
            require(result["pin"] is not None, "no checkpoint published")
            snapshot = receive(result, args.casita, args.output)
            if args.command == "consult":
                selected = task_start(snapshot, args.task, result["pin"])
                (args.output / "task-start.json").write_bytes(canonical(selected))
            print("Verified shared context revision " + str(snapshot["revision"]))
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as stream:
                stream.write(canonical(result))
            print("Saved relay response.")


if __name__ == "__main__":
    main()
