#!/usr/bin/env python3
"""Sync and verify a synthetic offline event kit and a last-minute rundown update."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from artifacts import new_output, rejected
from demo import Casita, canonical, digest, file_map

EVENT = "synthetic-demo"
CURRENT = "kit/current"
ASSETS = {"assets/title.svg", "assets/operator-notes.txt"}
FILES = ASSETS | {"rundown.json"}


def snapshot_name(version):
    if version not in ("v1", "v2"):
        raise ValueError("unknown synthetic kit version")
    return f"kit/{EVENT}/{version}"


def payloads_sent(report):
    matches = re.findall(r"^payloads-sent ([0-9]+)$", report, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("expected one payloads-sent counter from pinned Casita sync")
    return int(matches[0])


def create_kit(folder, version):
    snapshot_name(version)
    (folder / "assets").mkdir(parents=True)
    (folder / "assets/title.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100">'
        b'<text x="20" y="60">SYNTHETIC EVENT</text></svg>\n')
    (folder / "assets/operator-notes.txt").write_bytes(
        b"Synthetic operator card: confirm the selected rundown version before use.\n")
    rundown = {"schema": "casita-event-rundown.v1", "synthetic": True, "event_id": EVENT,
               "cues": [
                   {"id": "doors", "time": "18:00", "label": "Synthetic doors open",
                    "asset": "assets/operator-notes.txt"},
                   {"id": "welcome", "time": "18:10" if version == "v1" else "18:15",
                    "label": "Synthetic welcome", "asset": "assets/title.svg"}]}
    (folder / "rundown.json").write_bytes(canonical(rundown))
    manifest = {"schema": "casita-event-kit.v1", "synthetic": True,
                "event_id": EVENT, "version": version, "files": file_map(folder)}
    data = canonical(manifest)
    (folder / "manifest.json").write_bytes(data)
    return {"event_id": EVENT, "version": version, "manifest_id": digest(data)}


def verify_kit(folder, pin):
    if not isinstance(pin.get("manifest_id"), str) or not re.fullmatch(r"[0-9a-f]{64}", pin["manifest_id"]):
        raise ValueError("expected kit manifest ID must be a SHA-256 digest")
    actual = file_map(folder)
    if set(actual) != FILES | {"manifest.json"}:
        raise ValueError("kit file set mismatch")
    if actual.pop("manifest.json") != pin["manifest_id"]:
        raise ValueError("kit manifest pin mismatch")
    manifest = json.loads((folder / "manifest.json").read_bytes())
    if (not isinstance(manifest, dict)
            or set(manifest) != {"schema", "synthetic", "event_id", "version", "files"}
            or manifest["schema"] != "casita-event-kit.v1" or manifest["synthetic"] is not True):
        raise ValueError("unexpected kit manifest schema")
    if manifest["event_id"] != pin.get("event_id") or manifest["version"] != pin.get("version"):
        raise ValueError("kit event or version binding mismatch")
    if actual != manifest["files"]:
        raise ValueError("kit file hashes mismatch")
    rundown = json.loads((folder / "rundown.json").read_bytes())
    if (not isinstance(rundown, dict)
            or set(rundown) != {"schema", "synthetic", "event_id", "cues"}
            or rundown["schema"] != "casita-event-rundown.v1" or rundown["synthetic"] is not True
            or rundown["event_id"] != pin["event_id"] or not isinstance(rundown["cues"], list)
            or len(rundown["cues"]) != 2):
        raise ValueError("unexpected synthetic rundown schema")
    ids = []
    for cue in rundown["cues"]:
        if (not isinstance(cue, dict) or set(cue) != {"id", "time", "label", "asset"}
                or not all(isinstance(value, str) for value in cue.values())
                or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", cue["time"])
                or cue["asset"] not in ASSETS):
            raise ValueError("invalid cue or asset reference")
        ids.append(cue["id"])
    if sorted(ids) != ["doors", "welcome"]:
        raise ValueError("unexpected or duplicate synthetic cue IDs")
    return rundown


def select_kit(receiver, snapshot, pin, folder):
    # Transfer alone is not approval. Reject a stale/mismatched snapshot before
    # checkout, then verify its actual restored files before moving CURRENT.
    key = pin.get("directory_key")
    if not isinstance(key, str) or not key or receiver.roots().get(snapshot) != key:
        raise ValueError("snapshot directory key does not match the expected kit")
    receiver.run("checkout", pin["directory_key"], folder, "--no-root")
    verify_kit(folder, pin)
    receiver.run("root", "set", CURRENT, pin["directory_key"])
    if receiver.roots().get(CURRENT) != pin["directory_key"]:
        raise ValueError("selected kit root does not match the verified snapshot")


def run_demo(binary, destination):
    output = new_output(destination)
    commands = []
    receipt = {"schema": "casita-event-kit.receipt.v1", "commands": commands,
               "synthetic": True, "eventools_integration": False,
               "network_isolation_enforced": False, "received_code_executed": False,
               "media_played": False, "execution_attested": False, "scripted_selection": True}
    try:
        sender = Casita(binary, output / "sender-store", commands)
        venue = Casita(binary, output / "venue-store", commands)
        receipt["casita_version"] = sender.run("--version").strip()
        receipt["casita_binary_sha256"] = digest(Path(binary).read_bytes())
        sender.run("init")
        venue.run("init")
        pins, originals, sync_reports = {}, {}, {}
        for version in ("v1", "v2"):
            folder = output / "kits" / version
            pins[version] = create_kit(folder, version)
            verify_kit(folder, pins[version])
            originals[version] = file_map(folder)
            sender.run("import", folder, "--root", snapshot_name(version))
            pins[version]["directory_key"] = sender.roots()[snapshot_name(version)]
            args = ["sync", "--from", sender.repository, "--to", venue.repository,
                    "--root", snapshot_name(version)]
            if version == "v2":
                args.append("--incremental")
            sync_reports[version] = venue.run(*args)
            if venue.roots().get(snapshot_name(version)) != pins[version]["directory_key"]:
                raise ValueError("synced kit root differs from the sender pin")
            if version == "v1":
                select_kit(venue, snapshot_name("v1"), pins["v1"], output / "selected-v1")
            elif venue.roots().get(CURRENT) != pins["v1"]["directory_key"]:
                raise ValueError("sync changed the active kit before verification and selection")
        (output / "pins.json").write_bytes(canonical(pins))
        expected = json.loads((output / "pins.json").read_bytes())
        sent = {version: payloads_sent(report) for version, report in sync_reports.items()}
        if not 0 < sent["v2"] < sent["v1"]:
            raise ValueError("expected fewer payloads sent for this incremental kit update")
        receipt["sync_payloads_sent"] = sent
        first, second = [venue.entries(expected[v]["directory_key"]) for v in ("v1", "v2")]
        if first["assets"] != second["assets"] or first["rundown.json"] == second["rundown.json"]:
            raise ValueError("expected shared assets and a changed rundown")
        receipt["shared_assets_directory_key"] = first["assets"]
        controls = {"stale_snapshot_before_selection": rejected(lambda: select_kit(
            venue, snapshot_name("v1"), expected["v2"], output / "rejected-stale"))}
        if (output / "rejected-stale").exists() or venue.roots().get(CURRENT) != expected["v1"]["directory_key"]:
            raise ValueError("stale candidate changed the active selection or created a checkout")
        receipt["old_selection_preserved_until_verified_update"] = True
        select_kit(venue, snapshot_name("v2"), expected["v2"], output / "selected-v2")

        # Model loss of access to the sender, without changing any host network
        # settings. Rename only the synthetic sender store created in this run.
        shutil.rmtree(output / "kits")
        sender.repository.rename(output / "sender-store-unavailable")
        if sender.repository.exists() or (output / "kits").exists():
            raise ValueError("sender path or original kit files remain available")
        receipt["sender_path_unavailable"] = True
        receipt["venue_only_command_start"] = len(commands)
        offline = output / "venue-only-v2"
        venue.run("checkout", venue.roots()[CURRENT], offline, "--no-root")
        rundown = verify_kit(offline, expected["v2"])
        if file_map(offline) != originals["v2"]:
            raise ValueError("venue-only files differ from the original kit")
        if rundown["cues"][1]["time"] != "18:15":
            raise ValueError("venue-only rundown did not preserve the update")

        mixed = output / "mixed-kit"
        shutil.copytree(offline, mixed)
        shutil.copyfile(output / "selected-v1/rundown.json", mixed / "rundown.json")
        controls["mixed_rundown"] = rejected(lambda: verify_kit(mixed, expected["v2"]))
        altered = output / "altered-asset"
        shutil.copytree(offline, altered)
        (altered / "assets/title.svg").write_bytes(b"synthetic replaced graphic\n")
        controls["altered_asset"] = rejected(lambda: verify_kit(altered, expected["v2"]))
        missing = output / "missing-asset"
        shutil.copytree(offline, missing)
        (missing / "assets/title.svg").unlink()
        controls["missing_asset"] = rejected(lambda: verify_kit(missing, expected["v2"]))
        rebound = output / "wrong-event"
        shutil.copytree(offline, rebound)
        path = rebound / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["event_id"] = "synthetic-other-event"
        data = canonical(manifest)
        path.write_bytes(data)
        controls["rebound_event_metadata"] = rejected(lambda: verify_kit(
            rebound, dict(expected["v2"], manifest_id=digest(data))))
        venue.run("fsck", "--dry-run")
        receipt.update(ok=True, pins=expected, original_file_hashes=originals,
                       sync_reports=sync_reports, negative_controls=controls,
                       venue_only_restoration_verified=True, venue_integrity_audit_passed=True,
                       selected_directory_key=venue.roots()[CURRENT], welcome_time="18:15")
    except Exception as error:
        receipt.update(ok=False, error=str(error))
        raise
    finally:
        (output / "receipt.json").write_bytes(canonical(receipt))
    print("PASS: synthetic v1 kit synced and selected with exact rundown and assets")
    print("PASS: v2 incremental sync sends fewer payloads; unchanged assets retain identity")
    print("PASS: transfer and stale candidate leave v1 active until verified v2 selection")
    print("PASS: venue store restores exact v2 with original files and sender path unavailable")
    print("PASS: welcome cue remains 18:15; no received code or media executed")
    print("PASS: mixed rundown, altered or missing asset and rebound event metadata rejected")
    print("PASS: venue store passes integrity audit")
    print(f"Venue-only kit: {offline}")
    print(f"Receipt: {output / 'receipt.json'}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--casita", default="casita", help="tested Casita executable or PATH name")
    parser.add_argument("--output", required=True, type=Path, help="fresh output/<directory>")
    args = parser.parse_args()
    binary = shutil.which(args.casita)
    if not binary:
        parser.error("Casita executable not found; see README.md for the tested build")
    try:
        run_demo(str(Path(binary).resolve()), args.output)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"Event-kit demo failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
