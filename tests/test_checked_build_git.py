"""A source pin names the real commit, even with local Git redirection."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_git',ROOT/'ci/build-relay-native.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class GitSourcePin(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.source=Path(temp.name)/'native';self.source.mkdir()
        self.env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')}
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.source),*args],env=self.env,stderr=subprocess.DEVNULL)
        self.git=git;git('init','-q')
        (self.source/'.gitignore').write_text('Cargo.lock\n')
        (self.source/'Cargo.toml').write_text('[workspace]\n# ORIGINAL\n')
        git('add','.');git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','original')
        self.pin=git('rev-parse','HEAD').decode().strip()
        (self.source/'Cargo.lock').write_bytes(b'SYNTHETIC LOCK')

    def replace(self):
        (self.source/'Cargo.toml').write_text('[workspace]\n# REPLACEMENT\n')
        self.git('add','.');self.git('-c','user.name=Synthetic','-c','user.email=synthetic@example.invalid','commit','-qm','replacement')
        replacement=self.git('rev-parse','HEAD').decode().strip()
        self.git('checkout','-q',self.pin)
        self.git('replace',self.pin,replacement)
        self.git('reset','--hard',self.pin)
        self.assertIn(b'REPLACEMENT',self.git('archive','--format=tar',self.pin))

    def test_replacement_tree_cannot_pass_original_source_validation(self):
        self.replace()
        with patch.object(builder,'PIN',self.pin),self.assertRaises(ValueError):
            builder.validate_source(self.source,b'SYNTHETIC LOCK')

    def test_archive_ignores_replace_refs_and_git_environment_redirection(self):
        self.replace()
        self.assertTrue(hasattr(builder,'source_archive'),'isolated archive reader unavailable')
        with patch.object(builder,'PIN',self.pin),patch.dict(os.environ,{'GIT_DIR':'/synthetic/missing','GIT_CONFIG_COUNT':'1',
                'GIT_CONFIG_KEY_0':'core.worktree','GIT_CONFIG_VALUE_0':'/synthetic/missing'}):
            archive=builder.source_archive(self.source)
        self.assertIn(b'ORIGINAL',archive)
        self.assertNotIn(b'REPLACEMENT',archive)


if __name__=='__main__':unittest.main()
