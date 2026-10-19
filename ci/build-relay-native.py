#!/usr/bin/env python3
"""Build the pinned native CLI with optimization and both dev runtime guards."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from relay_fsck_benchmark import checked_artifact
PIN='9d7a2f42b14d86189662e21241f72a55ccb42cb6'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--offline',action='store_true')
    args=parser.parse_args();source,target=args.source.resolve(),args.target.resolve()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False,mode=0o700)
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD']).decode().strip()!=PIN:
        raise ValueError('native source pin mismatch')
    if subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=no']):
        raise ValueError('native tracked source changed')
    lock=(HERE/'ci/Cargo.lock').read_bytes()
    if (source/'Cargo.lock').read_bytes()!=lock:raise ValueError('native dependency lock differs')
    forbidden=['RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS','RUSTC_WRAPPER','RUSTC_WORKSPACE_WRAPPER','RUSTC']
    if any(os.environ.get(key) for key in forbidden) or any(k.startswith('CARGO_PROFILE_') for k in os.environ):
        raise ValueError('compiler/profile environment overrides are unsupported')
    argv=['cargo','+1.94.1','build','--locked','--package','casita','--bin','casita',
          '--manifest-path',str(source/'Cargo.toml'),'--target-dir',str(target),
          '--profile','relay-checked','--config',str(HERE/'ci/relay-checked-profile.toml'),
          '--message-format=json-render-diagnostics']
    if args.offline:argv.append('--offline')
    artifact=None
    with (output/'cargo-messages.jsonl').open('x') as messages:
        process=subprocess.Popen(argv,stdout=subprocess.PIPE,text=True)
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
    receipt=dict(source_commit=PIN,rust_toolchain='1.94.1',
        lock_sha256=hashlib.sha256(lock).hexdigest(),profile=artifact['profile'],
        native_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        profile_config_sha256=hashlib.sha256((HERE/'ci/relay-checked-profile.toml').read_bytes()).hexdigest(),
        full_fsck_per_publication_unchanged=True,runtime_source_changed=False)
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    print('Checked optimized native CLI built; runtime assertions and overflow checks enabled.')


if __name__=='__main__':main()
