"""Verify detached pin signatures against receiver-provisioned trust."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile

NAMESPACES = {"input": "casita-context-demo.input-pins.v1",
              "result": "casita-context-demo.result-pins.v1"}


def read_regular(path, limit):
    # O_NONBLOCK avoids hanging on FIFOs; fstat and O_NOFOLLOW reject special
    # files and final-component links on the same descriptor that we read.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("signature inputs must be regular files without links")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("signature input exceeds limit")
    return raw


def keygen(*args, data=None):
    # This example never uses the user's SSH agent, keys or SSH configuration.
    env = dict(os.environ)
    env.pop("SSH_AUTH_SOCK", None)
    return subprocess.run(["ssh-keygen", *map(str, args)], input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, env=env)


def verify_pins(pins_path, signature_path, allowed_signers_path, signer, role):
    raw = read_regular(pins_path, 10_000)
    signature = read_regular(signature_path, 16_384)
    trusted = read_regular(allowed_signers_path, 16_384)
    # Snapshot all file inputs before invoking OpenSSH. Return the verified
    # bytes, never reopen pins.json to parse a possibly different message.
    with tempfile.TemporaryDirectory(prefix="casita-pin-verification-") as name:
        folder = Path(name)
        (folder / "signature").write_bytes(signature)
        (folder / "allowed_signers").write_bytes(trusted)
        result = keygen("-Y", "verify", "-f", folder / "allowed_signers",
            "-I", signer, "-n", NAMESPACES[role], "-s", folder / "signature", data=raw)
    if result.returncode != 0:
        raise ValueError("pin signature rejected for expected signer and namespace")
    return raw


def make_demo_key(path):
    """Create an unencrypted throwaway key only in the caller's private directory."""
    result = keygen("-q", "-t", "ed25519", "-N", "", "-C", "synthetic-demo", "-f", path)
    if result.returncode != 0:
        raise RuntimeError("could not generate throwaway signing key")


def sign_demo_pins(pins_path, key_path, signature_path, role):
    result = keygen("-Y", "sign", "-f", key_path, "-n", NAMESPACES[role],
        data=read_regular(pins_path, 10_000))
    if result.returncode != 0:
        raise RuntimeError("could not sign demo pins")
    signature_path.write_bytes(result.stdout)


def provision_demo_signer(key_path, allowed_path, identity, role):
    public = read_regular(Path(str(key_path) + ".pub"), 4096).decode("ascii").split()
    allowed_path.write_text(f'{identity} namespaces="{NAMESPACES[role]}" {public[0]} {public[1]}\n')
