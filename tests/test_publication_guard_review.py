"""Review regressions: installed runtime trust, Git refspecs and bootstrap privacy."""
import shlex
import subprocess
import sys

import test_publication_guard_edges as fixtures


class ReviewContracts(fixtures.Fixture):
    def test_runtime_ancestor_owner_and_permissions_are_required(self):
        for permissions in [0o777,0o770]:
            with self.subTest(permissions=permissions):
                for name in ['pre-commit','pre-push']:
                    (self.repo/'.git/hooks'/name).unlink(missing_ok=True)
                parent=self.root/('shared-'+str(permissions));parent.mkdir();parent.chmod(permissions)
                result=self.guard('install','--policy',str(self.policy),'--base',self.base,
                    '--runtime-dir',str(parent/'runtime'))
                self.blocked(result)
                self.assertFalse((parent/'runtime').exists())

    def test_real_expression_and_oid_branch_pushes_pass(self):
        self.assertEqual(self.install().returncode,0)
        self.write('one.txt','PUBLIC\n');self.git('add','.');self.git('commit','-qm','Synthetic one')
        candidate=self.git('rev-parse','HEAD').stdout.strip()
        self.write('two.txt','PUBLIC\n');self.git('add','.');self.git('commit','-qm','Synthetic two')
        remote=self.root/'remote.git';subprocess.run(['/usr/bin/git','init','--bare','-q',str(remote)],check=True,env=self.env)
        self.git('remote','add','synthetic',str(remote))
        for index,source in enumerate(['HEAD~1',candidate]):
            with self.subTest(source=source):
                result=self.git('push','synthetic',source+':refs/heads/release-'+str(index),check=False)
                self.assertEqual(result.returncode,0,result.stderr)
                ref=self.git('ls-remote','synthetic','refs/heads/release-'+str(index)).stdout
                self.assertTrue(ref.startswith(candidate+'\t'))

    def test_hook_bootstrap_errors_remain_static_and_nonzero(self):
        self.assertEqual(self.install().returncode,0)
        hook=self.repo/'.git/hooks/pre-commit';original_hook=hook.read_bytes()
        snapshot=next((self.root/'private-runtime').glob('guard-*.py'));source=snapshot.read_bytes()
        for variant in ['missing-scanner','scanner-is-directory','missing-interpreter']:
            with self.subTest(variant=variant):
                if snapshot.is_dir():snapshot.rmdir()
                if not snapshot.exists():snapshot.write_bytes(source);snapshot.chmod(0o400)
                hook.write_bytes(original_hook)
                if variant=='missing-scanner':snapshot.unlink()
                elif variant=='scanner-is-directory':snapshot.unlink();snapshot.mkdir()
                else:
                    hook.write_text(hook.read_text().replace(shlex.quote(sys.executable),
                        shlex.quote(str(self.root/'missing-python'))))
                result=subprocess.run([str(hook)],cwd=self.repo,env=self.env,text=True,capture_output=True)
                self.blocked(result)
                self.assertNotIn(str(self.root),result.stdout+result.stderr)

    def test_sticky_temporary_ancestors_remain_supported(self):
        # The fixture itself is under a protected per-user TemporaryDirectory.
        # Standard /tmp-style sticky ancestors must not reject this safe layout.
        self.assertEqual(self.install().returncode,0)
        self.assertEqual(self.guard().returncode,0)

    def test_shell_signal_diagnostics_do_not_reveal_private_paths(self):
        self.assertEqual(self.install().returncode,0)
        fake=self.root/'terminated-interpreter'
        fake.write_text('#!/bin/sh\nkill -TERM $$\n');fake.chmod(0o700)
        hook=self.repo/'.git/hooks/pre-commit'
        hook.write_text(hook.read_text().replace(shlex.quote(sys.executable),shlex.quote(str(fake))))
        result=subprocess.run([str(hook)],cwd=self.repo,env=self.env,text=True,capture_output=True)
        self.blocked(result)
        self.assertNotIn(str(self.root),result.stdout+result.stderr)
