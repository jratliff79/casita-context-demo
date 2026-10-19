#!/usr/bin/env python3
"""Build the pinned native CLI with optimization and both dev runtime guards."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from relay_fsck_benchmark import checked_artifact
PIN='9d7a2f42b14d86189662e21241f72a55ccb42cb6'
PROFILE_LINES = ('[profile.relay-checked]', 'inherits = "dev"', 'opt-level = 3',
    'debug = 0', 'debug-assertions = true', 'overflow-checks = true', 'incremental = false')


def validate_source(source, lock):
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD']).decode().strip()!=PIN:
        raise ValueError('native source pin mismatch')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=no']):
        raise ValueError('native tracked source changed')
    for extra_args in ([], ['--ignored']):
        extra=subprocess.check_output(['git','-C',str(source),'ls-files','--others',
                                       '--exclude-standard','-z',*extra_args]).split(b'\0')
        if set(extra)-{b'',b'Cargo.lock'}:
            raise ValueError('untracked or ignored native build input')
    path=source/'Cargo.lock'
    if path.is_symlink() or not path.is_file() or path.read_bytes()!=lock:
        raise ValueError('native dependency lock differs')


def validate_environment(environment):
    if 'CARGO_HOME' in environment and not Path(environment['CARGO_HOME']).is_absolute():
        raise ValueError('Cargo home must be absolute when provided')
    forbidden={'RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER','RUSTC'}
    for key,value in environment.items():
        if value and (key in forbidden or key.startswith(('CARGO_PROFILE_','CARGO_TARGET_'))
                or key.startswith('CARGO_BUILD_') and key!='CARGO_BUILD_JOBS'):
            raise ValueError('compiler/profile environment overrides are unsupported')


def validate_cargo_configs(cwd, cargo_home):
    paths={cargo_home/'config',cargo_home/'config.toml'}
    for parent in [cwd,*cwd.parents]:
        paths.update((parent/'.cargo/config',parent/'.cargo/config.toml'))
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError('automatic Cargo configuration is unsupported')


def validate_profile_config(path):
    raw=path.read_bytes()
    lines=tuple(line.strip() for line in raw.decode().splitlines()
                if line.strip() and not line.strip().startswith('#'))
    if lines!=PROFILE_LINES:raise ValueError('checked profile must contain only the reviewed settings')
    return hashlib.sha256(raw).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--offline',action='store_true')
    args=parser.parse_args();source,target=args.source.resolve(),args.target.resolve()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False,mode=0o700)
    lock=(HERE/'ci/Cargo.lock').read_bytes()
    validate_source(source,lock);validate_environment(os.environ)
    profile=HERE/'ci/relay-checked-profile.toml';profile_pin=validate_profile_config(profile)
    cargo_home=Path(os.environ.get('CARGO_HOME',Path.home()/'.cargo')).resolve()
    if target.is_relative_to(source) or output.is_relative_to(source):
        raise ValueError('build output must stay outside the source input')
    # Cargo must see only a private Git snapshot plus the explicitly pinned lock.
    # Running from the source snapshot also removes the invocation directory
    # from Cargo's automatic configuration search.
    archive=subprocess.check_output(['git','-C',str(source),'archive','--format=tar',PIN])
    argv=['cargo','+1.94.1','build','--locked','--package','casita','--bin','casita',
          '--target-dir',str(target),'--profile','relay-checked','--config',str(profile),
          '--message-format=json-render-diagnostics']
    if args.offline:argv.append('--offline')
    artifact=None
    with tempfile.TemporaryDirectory(prefix='native-source-',dir=output) as temporary, \
            (output/'cargo-messages.jsonl').open('x') as messages:
        snapshot=Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(snapshot,filter='data')
        (snapshot/'Cargo.lock').write_bytes(lock)
        validate_cargo_configs(snapshot,cargo_home)
        process=subprocess.Popen(argv+['--manifest-path',str(snapshot/'Cargo.toml')],
                                 cwd=snapshot,stdout=subprocess.PIPE,text=True)
        try:
            for line in process.stdout:
                if len(line)>1024*1024:raise ValueError('compiler message exceeds bound')
                messages.write(line)
                value=json.loads(line)
                if value.get('reason')=='compiler-artifact' and value.get('executable'):
                    artifact=value
            if process.wait()!=0:raise RuntimeError('checked native build failed')
        finally:
            process.stdout.close()
            if process.poll() is None:process.terminate();process.wait(timeout=10)
    binary=Path(checked_artifact(artifact or {}))
    validate_source(source,lock)
    if validate_profile_config(profile)!=profile_pin:raise ValueError('profile changed during build')
    receipt=dict(source_commit=PIN,rust_toolchain='1.94.1',
        lock_sha256=hashlib.sha256(lock).hexdigest(),profile=artifact['profile'],
        native_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        profile_config_sha256=profile_pin,source_archive_sha256=hashlib.sha256(archive).hexdigest(),
        source_snapshot_from_git=True,untracked_input_allowlist=['Cargo.lock'],automatic_cargo_configs_rejected=True,
        full_fsck_per_publication_unchanged=True,runtime_source_changed=False)
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print('Checked optimized native CLI built; runtime assertions and overflow checks enabled.')


if __name__=='__main__':main()
