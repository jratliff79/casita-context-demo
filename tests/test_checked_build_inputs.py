"""Reject hidden Cargo inputs before a checked-build receipt can be issued."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('checked_builder',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class BuildInputs(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'native';self.source.mkdir()
        def git(*args):return subprocess.check_output(['git','-C',str(self.source),*args],stderr=subprocess.DEVNULL)
        self.git=git;git('init','-q')
        (self.source/'Cargo.toml').write_text('[workspace]\n')
        (self.source/'.gitignore').write_text('Cargo.lock\nignored/\n')
        git('add','.');git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','fixture')
        self.pin=git('rev-parse','HEAD').decode().strip()
        (self.source/'Cargo.lock').write_text('SYNTHETIC LOCK')
    def validator(self):
        self.assertTrue(hasattr(builder,'validate_source'), 'source input validator unavailable')
        return builder.validate_source
    def test_only_explicit_root_lock_is_allowed_outside_git(self):
        validate=self.validator()
        with patch.object(builder,'PIN',self.pin):validate(self.source,b'SYNTHETIC LOCK')
    def test_untracked_and_ignored_build_scripts_are_rejected(self):
        validate=self.validator()
        for name in ['build.rs','ignored/build.rs','crates/extra/Cargo.lock']:
            with self.subTest(name=name):
                path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('SYNTHETIC')
                with patch.object(builder,'PIN',self.pin),self.assertRaises(ValueError):validate(self.source,b'SYNTHETIC LOCK')
                path.unlink()
    def test_parent_and_cargo_home_config_are_rejected(self):
        self.assertTrue(hasattr(builder,'validate_cargo_configs'),'Cargo hierarchy validation unavailable')
        home=self.root/'cargo-home';home.mkdir()
        for path in [self.root/'.cargo/config.toml',home/'config']:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('[build]\nrustflags=["-Coverflow-checks=no"]\n')
            with self.subTest(path=path.name),self.assertRaises(ValueError):builder.validate_cargo_configs(self.source,home)
            path.unlink()
    def test_target_and_build_environment_flags_are_rejected(self):
        self.assertTrue(hasattr(builder,'validate_environment'),'Cargo environment validation unavailable')
        builder.validate_environment({'CARGO_BUILD_JOBS':'2','CARGO_TERM_COLOR':'never'})
        for key in ['RUSTFLAGS','CARGO_BUILD_RUSTFLAGS','CARGO_BUILD_RUSTC_WRAPPER',
                    'CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_RUSTFLAGS','CARGO_PROFILE_RELAY_CHECKED_OVERFLOW_CHECKS']:
            with self.subTest(key=key),self.assertRaises(ValueError):builder.validate_environment({key:'SYNTHETIC'})
    def test_profile_config_cannot_include_hidden_flags(self):
        self.assertTrue(hasattr(builder,'validate_profile_config'),'explicit profile allowlist unavailable')
        path=self.root/'profile.toml';path.write_text((ROOT/'ci/relay-checked-profile.toml').read_text())
        builder.validate_profile_config(path)
        path.write_text(path.read_text()+'\ninclude=["unrecorded.toml"]\n')
        with self.assertRaises(ValueError):builder.validate_profile_config(path)


if __name__=='__main__':unittest.main()
