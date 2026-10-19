"""Checked receipts must not consume mutable compiler/dependency caches."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_isolation',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuildIsolation(unittest.TestCase):
    def test_build_environment_does_not_forward_native_compiler_or_package_overrides(self):
        self.assertTrue(hasattr(builder,'build_environment'),'isolated compiler environment unavailable')
        source={'PATH':'/synthetic/bin','HOME':'/synthetic/home','RUSTUP_HOME':'/synthetic/rustup',
                'CARGO_HOME':'/synthetic/edited-cache','CARGO_BUILD_JOBS':'2'}
        names=['CC','CFLAGS','CXX','CXXFLAGS','AR','ARFLAGS','RANLIB','LD','CPATH','LIBRARY_PATH',
               'HOST_CC','TARGET_CFLAGS','CC_aarch64_unknown_linux_gnu','CFLAGS_aarch64-unknown-linux-gnu',
               'ZSTD_SYS_USE_PKG_CONFIG','BLAKE3_PURE','PKG_CONFIG_PATH','CMAKE_TOOLCHAIN_FILE']
        source.update({name:'SYNTHETIC UNRECORDED OVERRIDE' for name in names})
        result=builder.build_environment(source,Path('/synthetic/private-new-cargo'))
        self.assertFalse(set(names).intersection(result),'native toolchain override reached Cargo')
        self.assertEqual(result['CARGO_HOME'],'/synthetic/private-new-cargo')
        self.assertEqual(result['CARGO_BUILD_JOBS'],'2')
        self.assertEqual(result['PATH'],source['PATH'])

    def test_private_cargo_sources_start_empty_and_are_removed_on_failure(self):
        self.assertTrue(hasattr(builder,'private_build_workspace'),'private dependency cache unavailable')
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                with builder.private_build_workspace(output) as (snapshot,home):
                    self.assertTrue(snapshot.is_dir());self.assertTrue(home.is_dir())
                    self.assertNotEqual(snapshot,home)
                    self.assertEqual(list(home.iterdir()),[],'unpacked dependency cache was reused')
                    (home/'SYNTHETIC_DEPENDENCY').write_text('private source cache')
                    raise RuntimeError('synthetic failure')
            self.assertFalse(home.exists());self.assertFalse(snapshot.exists())

    def test_existing_target_cannot_supply_a_substituted_executable(self):
        self.assertTrue(hasattr(builder,'prepare_target'),'fresh-target validation unavailable')
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'target';builder.prepare_target(target)
            (target/'relay-checked').mkdir();(target/'relay-checked/casita').write_bytes(b'SYNTHETIC SUBSTITUTION')
            with self.assertRaises(ValueError):builder.prepare_target(target)

    def test_cached_final_artifact_cannot_receive_a_build_receipt(self):
        self.assertTrue(hasattr(builder,'built_artifact'),'built-this-invocation artifact gate unavailable')
        artifact=dict(reason='compiler-artifact',target=dict(name='casita',kind=['bin']),executable='/synthetic/casita',
                      profile=dict(opt_level='3',debug_assertions=True,overflow_checks=True,test=False))
        for value in [True,None]:
            with self.subTest(fresh=value),self.assertRaises(ValueError):
                builder.built_artifact(dict(artifact,fresh=value))
        self.assertEqual(builder.built_artifact(dict(artifact,fresh=False)),'/synthetic/casita')


if __name__=='__main__':unittest.main()
