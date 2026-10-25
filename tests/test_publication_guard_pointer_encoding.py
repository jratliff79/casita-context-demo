"""Git's record terminator must not be confused with a path's final LF."""
from pathlib import Path

import test_publication_guard_edges as fixtures
import test_publication_guard_install_ancestors as ancestors


class PointerEncoding(fixtures.Fixture):
    chmod_for_test = ancestors.InstallAncestors.chmod_for_test
    assert_install_refused_without_mutation = (
        ancestors.InstallAncestors.assert_install_refused_without_mutation)

    def linked_metadata_with_final_lf(self):
        linked = self.root / 'linked'
        self.git('worktree', 'add', '--detach', str(linked), 'HEAD')
        self.repo = linked
        metadata = Path(self.git('rev-parse', '--git-dir').stdout[:-1])
        moved = metadata.with_name(metadata.name + '\n'); metadata.rename(moved)
        # A slash keeps Git's gitfile parser from trimming the path's final LF.
        (linked / '.git').write_text('gitdir: ' + str(moved) + '/\n')
        return moved

    def test_final_lf_metadata_does_not_hide_writable_commondir_pointer(self):
        metadata = self.linked_metadata_with_final_lf()
        self.chmod_for_test(metadata / 'commondir', 0o666)
        self.assert_install_refused_without_mutation()

    def test_protected_final_lf_metadata_remains_supported(self):
        self.linked_metadata_with_final_lf()
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.guard().returncode, 0)
