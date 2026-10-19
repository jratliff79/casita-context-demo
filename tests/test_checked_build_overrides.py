"""Private archive attributes and incremental overrides are not pinned inputs."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_overrides',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class ArchiveOverrides(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'native';self.source.mkdir()
        def git(*args):return subprocess.check_output(['git','-C',str(self.source),*args],stderr=subprocess.DEVNULL)
        self.git=git;git('init','-q')
        (self.source/'.gitignore').write_text('Cargo.lock\n')
        (self.source/'Cargo.toml').write_text('[workspace]\n')
        git('add','.');git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','fixture')
        self.pin=git('rev-parse','HEAD').decode().strip()
        (self.source/'Cargo.lock').write_bytes(b'SYNTHETIC LOCK')

    def test_private_attributes_cannot_silently_remove_pinned_cargo_inputs(self):
        attributes=self.source/'.git/info/attributes';attributes.write_text('Cargo.toml export-ignore\n')
        with patch.object(builder,'PIN',self.pin):
            with self.assertRaises(ValueError):builder.validate_source(self.source,b'SYNTHETIC LOCK')
            with self.assertRaises(ValueError):builder.source_archive(self.source)

    def test_local_attributes_file_config_does_not_change_archive(self):
        attributes=self.root/'attributes';attributes.write_text('Cargo.toml export-ignore\n')
        self.git('config','core.attributesFile',str(attributes))
        with patch.object(builder,'PIN',self.pin):archive=builder.source_archive(self.source)
        self.assertIn(b'Cargo.toml',archive,'unrecorded attributes config omitted a tracked build input')

    def test_incremental_environment_override_is_rejected(self):
        for value in ['0','1']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                builder.validate_environment({'CARGO_INCREMENTAL':value})


if __name__=='__main__':unittest.main()
