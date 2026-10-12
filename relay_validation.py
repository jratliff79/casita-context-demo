"""Shared data-only relay validation; no filesystem, process or network access."""
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def git_lines(blob):
    parts = blob.decode("utf-8").split("\n")
    lines = [line[:-1] if line.endswith("\r") else line for line in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


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


def timestamp(value):
    require(text(value, 64), "invalid relay timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise Rejected("invalid relay timestamp") from None
    require(parsed.tzinfo is not None, "relay timestamp needs a timezone")


def accepted_memory(entry, revision, tasks):
    proposal_fields = {"author", "base_revision", "created_at", "task_id", "scope",
                       "source_revision", "entry_id", "statement", "citation"}
    require(isinstance(entry, dict) and set(entry) == proposal_fields | {
        "accepted_by", "accepted_at", "proposal_id"}, "invalid accepted memory fields")
    require(all(identifier(entry[key]) for key in ("author", "task_id", "entry_id", "accepted_by"))
            and entry["task_id"] in tasks and entry["scope"] in ("task", "workspace")
            and type(entry["base_revision"]) is int and 1 <= entry["base_revision"] < revision
            and text(entry["statement"], 1000)
            and isinstance(entry["source_revision"], str)
            and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", entry["source_revision"]),
            "invalid accepted memory metadata")
    timestamp(entry["created_at"]); timestamp(entry["accepted_at"])
    cite = entry["citation"]
    require(isinstance(cite, dict) and set(cite) == {
        "path", "sha256", "start_line", "end_line", "excerpt"}, "invalid memory citation")
    require(safe_path(cite["path"]) and isinstance(cite["sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", cite["sha256"])
            and type(cite["start_line"]) is int and type(cite["end_line"]) is int
            and 1 <= cite["start_line"] <= cite["end_line"] and cite["end_line"] - cite["start_line"] < 40
            and isinstance(cite["excerpt"], list)
            and len(cite["excerpt"]) == cite["end_line"] - cite["start_line"] + 1
            and all(isinstance(line, str) and "\x00" not in line and len(line) <= 100_000
                    for line in cite["excerpt"]), "invalid memory citation metadata")
    require(entry["proposal_id"] == digest(canonical({key: entry[key] for key in proposal_fields})),
            "accepted memory proposal identity mismatch")
    # An accepted citation can outlive later changes to a task checkpoint. It is
    # identified by its original source commit/hash, not rebound to current files.


def proposal(value):
    fields = {"author", "base_revision", "created_at", "task_id", "scope",
              "source_revision", "entry_id", "statement", "citation"}
    require(isinstance(value, dict) and set(value) == fields
            and type(value["base_revision"]) is int and value["base_revision"] >= 1
            and identifier(value["author"]) and identifier(value["task_id"]),
            "invalid proposal fields")
    # Reuse exactly the accepted-memory proposal rules without inventing a
    # second shape for pending proposals. Synthetic acceptance is data only.
    entry = {**value, "accepted_by": value["author"], "accepted_at": value["created_at"],
             "proposal_id": digest(canonical(value))}
    accepted_memory(entry, value["base_revision"] + 1, {value["task_id"]: None})
    return value


def snapshot(value, workspace, revision):
    require(isinstance(value, dict) and set(value) == {
        "schema", "workspace", "revision", "parent_revision", "author", "created_at",
        "tasks", "memory", "decision"}, "invalid relay snapshot fields")
    require(value["schema"] == SCHEMA and identifier(workspace) and value["workspace"] == workspace
            and type(value["revision"]) is int and value["revision"] == revision and revision > 0
            and type(value["parent_revision"]) is int and value["parent_revision"] == revision - 1
            and identifier(value["author"]), "invalid relay snapshot metadata")
    timestamp(value["created_at"])
    tasks = value["tasks"]
    require(isinstance(tasks, dict) and 1 <= len(tasks) <= 20, "invalid workspace task index")
    for task_id, context in tasks.items():
        checkpoint(context)
        require(context["task_id"] == task_id, "task index mismatch")
    memory = value["memory"]
    require(isinstance(memory, list) and len(memory) <= 50, "invalid accepted memory index")
    for entry in memory:
        accepted_memory(entry, revision, tasks)
    decision = value["decision"]
    require(decision is None or (isinstance(decision, dict) and set(decision) == {
        "proposal_id", "accepted_by"} and identifier(decision["accepted_by"])
        and any(entry["proposal_id"] == decision["proposal_id"]
                and entry["accepted_by"] == decision["accepted_by"] for entry in memory)),
        "invalid memory decision")
    require(len(canonical(value)) <= LIMIT, "snapshot exceeds byte limit")
    return value
