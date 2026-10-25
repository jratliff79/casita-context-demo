"""Git pointer whitespace is part of the selected filesystem namespace."""
from pathlib import Path

import test_publication_guard_edges as fixtures
import test_publication_guard_install_ancestors as ancestors


class PointerPaths(fixtures.Fixture):
    chmod_for_test = ancestors.InstallAncestors.chmod_for_test
    relocate_metadata = ancestors.InstallAncestors.relocate_metadata
    assert_install_refused_without_mutation = (
        ancestors.InstallAncestors.assert_install_refused_without_mutation)

    def test_leading_space_gitfile_namespace_is_protected(self):
        metadata = self.root / 'private-metadata' / 'repository.git'
        self.relocate_metadata(metadata)
        shared = self.repo / ' shared'; shared.mkdir()
        (shared / 'metadata').symlink_to(metadata, target_is_directory=True)
        (self.repo / '.git').write_text('gitdir:  shared/metadata\n')
        self.chmod_for_test(shared, 0o777)
        self.assert_install_refused_without_mutation()

    def test_leading_space_commondir_namespace_is_protected(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        common = self.repo / '.git'; self.repo = linked
        metadata = Path(self.git('rev-parse', '--git-dir').stdout.strip())
        shared = metadata / ' shared'; shared.mkdir()
        (shared / 'common').symlink_to(common, target_is_directory=True)
        (metadata / 'commondir').write_text(' shared/common\n')
        self.chmod_for_test(shared, 0o777)
        self.assert_install_refused_without_mutation()

    def test_protected_space_bearing_gitfile_target_remains_supported(self):
        metadata = self.root / 'private-metadata' / ' repository.git '
        self.relocate_metadata(metadata)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard().returncode, 0)
