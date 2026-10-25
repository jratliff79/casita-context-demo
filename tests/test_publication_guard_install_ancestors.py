"""Installation must protect every namespace selecting the managed hooks."""
from pathlib import Path

import test_publication_guard_edges as fixtures


class InstallAncestors(fixtures.Fixture):
    def chmod_for_test(self, path, mode):
        previous = path.stat().st_mode & 0o7777
        self.addCleanup(path.chmod, previous)
        path.chmod(mode)

    def relocate_metadata(self, metadata):
        metadata.parent.mkdir(parents=True, exist_ok=True)
        (self.repo / '.git').rename(metadata)
        (self.repo / '.git').write_text('gitdir: ' + str(metadata) + '\n')

    def assert_install_refused_without_mutation(self):
        config = self.git('config', '--local', '--list').stdout
        hooks = Path(self.git('rev-parse', '--git-path', 'hooks').stdout.strip())
        if not hooks.is_absolute():
            hooks = self.repo / hooks
        before = {p.name: p.read_bytes() for p in hooks.iterdir() if p.is_file()}
        result = self.install()
        self.blocked(result)
        self.assertEqual(self.git('config', '--local', '--list').stdout, config)
        self.assertFalse((self.root / 'private-runtime').exists())
        self.assertEqual({p.name: p.read_bytes() for p in hooks.iterdir()
                          if p.is_file()}, before)

    def test_group_writable_git_directory_is_refused_before_install(self):
        self.chmod_for_test(self.repo / '.git', 0o770)
        self.assert_install_refused_without_mutation()

    def test_world_writable_hooks_directory_is_refused_before_install(self):
        self.chmod_for_test(self.repo / '.git/hooks', 0o777)
        self.assert_install_refused_without_mutation()

    def test_separate_metadata_under_writable_ancestor_is_refused(self):
        shared = self.root / 'shared-metadata'; shared.mkdir()
        self.relocate_metadata(shared / 'private' / 'repository.git')
        self.chmod_for_test(shared, 0o770)
        self.assert_install_refused_without_mutation()

    def test_separate_checkout_under_writable_ancestor_is_refused(self):
        self.relocate_metadata(self.root / 'private-metadata' / 'repository.git')
        shared = self.root / 'shared-checkout'; shared.mkdir()
        moved = shared / 'repo'; self.repo.rename(moved); self.repo = moved
        self.chmod_for_test(shared, 0o777)
        self.assert_install_refused_without_mutation()

    def test_linked_worktree_private_git_directory_is_checked(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        self.repo = linked
        metadata = Path(self.git('rev-parse', '--git-dir').stdout.strip())
        self.chmod_for_test(metadata, 0o770)
        self.assert_install_refused_without_mutation()

    def test_linked_worktree_common_git_directory_is_checked(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        self.chmod_for_test(self.repo / '.git', 0o770)
        self.repo = linked
        self.assert_install_refused_without_mutation()

    def test_protected_metadata_target_does_not_hide_writable_alias_namespace(self):
        metadata = self.root / 'private-metadata' / 'repository.git'
        self.relocate_metadata(metadata)
        shared = self.root / 'shared-alias'; shared.mkdir()
        alias = shared / 'metadata'; alias.symlink_to(metadata.parent, target_is_directory=True)
        (self.repo / '.git').write_text('gitdir: ' + str(alias / metadata.name) + '\n')
        self.chmod_for_test(shared, 0o777)
        self.assert_install_refused_without_mutation()

    def test_protected_gitfile_alias_and_missing_hooks_remain_supported(self):
        metadata = self.root / 'private-metadata' / 'repository.git'
        self.relocate_metadata(metadata)
        alias = self.root / 'metadata-alias'; alias.symlink_to(metadata.parent,
                                                               target_is_directory=True)
        (self.repo / '.git').write_text('gitdir: ' + str(alias / metadata.name) + '\n')
        hooks = metadata / 'hooks'
        for p in hooks.iterdir():
            p.unlink()
        hooks.rmdir()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((hooks / 'pre-commit').is_file())
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(self.guard('pre-push', input=self.push_input()).returncode, 0)

    def test_protected_linked_worktree_remains_supported(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        self.repo = linked
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(self.guard('pre-push', input=self.push_input()).returncode, 0)

    def test_sticky_metadata_ancestor_with_private_child_remains_supported(self):
        shared = self.root / 'sticky'; shared.mkdir(); shared.chmod(0o1777)
        self.relocate_metadata(shared / 'private' / 'repository.git')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard().returncode, 0)
