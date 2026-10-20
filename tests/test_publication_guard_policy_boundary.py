"""Review regressions for policy storage and explicit Git configuration values."""
import test_publication_guard_edges as fixtures


class PolicyBoundary(fixtures.Fixture):
    def test_shared_policy_ancestors_block_all_modes(self):
        original = self.policy
        for permissions in [0o777, 0o770]:
            parent = self.root / ('shared-policy-' + str(permissions))
            parent.mkdir(); parent.chmod(permissions)
            private = parent / 'private'; private.mkdir(mode=0o700)
            self.policy = private / 'policy.json'
            self.policy.write_bytes(original.read_bytes()); self.policy.chmod(0o600)
            self.git('config', 'publicationGuard.policy', str(self.policy))
            for mode in ['staged', 'pre-push', 'install']:
                with self.subTest(permissions=permissions, mode=mode):
                    result = (self.install() if mode == 'install' else
                              self.guard(mode, input=self.push_input() if mode == 'pre-push' else None))
                    self.blocked(result)
            parent.chmod(0o700)
            for name in ['pre-commit', 'pre-push']:
                (self.repo / '.git/hooks' / name).unlink(missing_ok=True)
        self.policy = original

    def test_private_policy_under_protected_ancestors_remains_supported(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(self.guard('pre-push', input=self.push_input()).returncode, 0)

    def test_empty_explicit_base_preserves_installed_configuration_and_hooks(self):
        self.assertEqual(self.install().returncode, 0)
        hooks = {name: (self.repo / '.git/hooks' / name).read_bytes()
                 for name in ['pre-commit', 'pre-push']}
        previous = self.git('config', '--local', '--list').stdout
        result = self.guard('install', '--policy', str(self.policy), '--base', '',
                            '--runtime-dir', str(self.root / 'private-runtime'))
        self.blocked(result)
        self.assertEqual(self.git('config', '--local', '--list').stdout, previous)
        for name, source in hooks.items():
            self.assertEqual((self.repo / '.git/hooks' / name).read_bytes(), source)
        self.assertEqual(self.guard('pre-push', input=self.push_input()).returncode, 0)

    def test_disabled_promisor_boolean_values_allow_complete_repositories(self):
        for value in ['false', 'no', 'off', '0', '']:
            self.git('config', 'remote.synthetic.promisor', value)
            for mode in ['staged', 'pre-push', 'install']:
                with self.subTest(value=value, mode=mode):
                    result = (self.install() if mode == 'install' else
                              self.guard(mode, input=self.push_input() if mode == 'pre-push' else None))
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_enabled_or_invalid_promisor_and_partial_clone_extension_block(self):
        self.git('config', 'remote.disabled.promisor', 'false')
        for value in ['true', 'yes', 'on', '1', 'invalid-boolean']:
            with self.subTest(value=value):
                self.git('config', 'remote.synthetic.promisor', value)
                self.blocked(self.guard())
        self.git('config', '--unset', 'remote.synthetic.promisor')
        with (self.repo / '.git/config').open('a') as stream:
            stream.write('[remote \"bare\"]\n\tpromisor\n')
        self.blocked(self.guard())
        self.git('config', '--unset', 'remote.bare.promisor')
        self.git('config', 'extensions.partialclone', 'synthetic')
        self.blocked(self.guard())
