"""Exercise the real helper with an explicitly synthetic compiler protocol."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_integration',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuildProtocol(unittest.TestCase):
    def test_real_entrypoint_isolates_inputs_and_removes_private_dependency_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'native';source.mkdir();tools=root/'bin';tools.mkdir()
            def git(*args):return subprocess.check_output(['git','-C',str(source),*args],stderr=subprocess.DEVNULL)
            git('init','-q');(source/'.gitignore').write_text('Cargo.lock\n')
            (source/'Cargo.toml').write_text('[workspace]\n');git('add','.')
            git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','synthetic source')
            pin=git('rev-parse','HEAD').decode().strip();(source/'Cargo.lock').write_bytes((ROOT/'ci/Cargo.lock').read_bytes())
            cache=root/'old-cargo';(cache/'registry/src').mkdir(parents=True)
            (cache/'registry/src/EDITED_DEPENDENCY').write_text('SYNTHETIC MUTABLE CACHE')
            target=root/'target';output=root/'evidence'
            compiler=tools/'cargo'
            compiler.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
from pathlib import Path
home=Path(os.environ['CARGO_HOME']);target=Path(sys.argv[sys.argv.index('--target-dir')+1])
assert not {'CC','CFLAGS','CXX','AR','SYNTHETIC_PACKAGE_OVERRIDE'}.intersection(os.environ)
assert home.is_dir() and not list(home.iterdir())
assert target.is_dir() and not list(target.iterdir())
assert Path.cwd().joinpath('Cargo.toml').is_file()
(home/'SYNTHETIC_DOWNLOADED_SOURCE').write_text('synthetic source')
binary=target/'relay-checked/casita';binary.parent.mkdir();binary.write_bytes(b'SYNTHETIC COMPILER OUTPUT');binary.chmod(0o700)
print(json.dumps(dict(reason='compiler-artifact',target=dict(name='casita',kind=['bin']),
    executable=str(binary),fresh=False,profile=dict(opt_level='3',debug_assertions=True,overflow_checks=True,test=False))))
''');compiler.chmod(0o700)
            argv=['build-relay-native.py','--source',str(source),'--target',str(target),'--output',str(output)]
            environment={'PATH':str(tools)+os.pathsep+os.environ['PATH'],'CARGO_HOME':str(cache),
                         'CC':'SYNTHETIC WRAPPER','CFLAGS':'SYNTHETIC FLAGS','CXX':'SYNTHETIC WRAPPER','AR':'SYNTHETIC WRAPPER',
                         'SYNTHETIC_PACKAGE_OVERRIDE':'SYNTHETIC OVERRIDE'}
            completed=True;failure=None
            with patch.object(builder,'PIN',pin),patch.object(builder,'HERE',ROOT), \
                 patch.object(sys,'argv',argv),patch.dict(os.environ,environment):
                try:builder.main()
                except Exception as error:completed=False;failure=type(error).__name__
            self.assertTrue(completed,'real helper did not isolate compiler/cache/target inputs: '+str(failure))
            receipt=json.loads((output/'build-receipt.json').read_text())
            self.assertTrue(receipt['fresh_cargo_home']);self.assertTrue(receipt['fresh_target'])
            self.assertTrue(receipt['final_artifact_built_this_invocation'])
            self.assertEqual(sorted(p.name for p in output.iterdir()),['build-receipt.json','cargo-messages.jsonl'])
            self.assertEqual((cache/'registry/src/EDITED_DEPENDENCY').read_text(),'SYNTHETIC MUTABLE CACHE')


if __name__=='__main__':unittest.main()
