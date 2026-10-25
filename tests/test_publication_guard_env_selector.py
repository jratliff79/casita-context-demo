"""Explicit Git selectors retain the same protected-gitfile semantics."""
import test_publication_guard_edges as fixtures
import test_publication_guard_install_ancestors as ancestors


class EnvSelector(fixtures.Fixture):
    chmod_for_test = ancestors.InstallAncestors.chmod_for_test
    assert_install_refused_without_mutation = (
        ancestors.InstallAncestors.assert_install_refused_without_mutation)

    def select_linked_gitfile(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        self.repo = linked
        self.env['GIT_DIR'] = str(linked / '.git')

    def test_protected_environment_selected_gitfile_remains_supported(self):
        self.select_linked_gitfile()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard().returncode, 0)

    def test_environment_gitfile_alias_namespace_is_checked(self):
        self.select_linked_gitfile()
        shared = self.root / 'shared'; shared.mkdir()
        alias = shared / 'selected.git'; alias.symlink_to(self.repo / '.git')
        self.env['GIT_DIR'] = str(alias)
        self.chmod_for_test(shared, 0o777)
        self.assert_install_refused_without_mutation()
