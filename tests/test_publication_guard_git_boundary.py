"""Outgoing history is bounded by an explicit reviewed base, never remote claims."""
import json
import shlex

import test_publication_guard_edges as fixtures


class GitBoundary(fixtures.Fixture):
    def test_partial_clone_configuration_blocks_before_object_scanning(self):
        self.git('config','remote.synthetic.promisor','true')
        self.blocked(self.guard())

    def test_invalid_unicode_policy_is_rejected(self):
        self.policy.write_text(json.dumps(dict(schema='casita.publication-policy.v1',
            blocked_literals=['\ud800x'],blocked_tokens=[])))
        self.blocked(self.guard())

    def test_remote_tip_does_not_suppress_unreviewed_earlier_commit(self):
        self.write('new.txt','PUBLIC\n');self.git('add','.')
        self.git('commit','-qm','SYNTHETIC_PRIVATE_CUSTOMER_7341')
        remote=self.git('rev-parse','HEAD').stdout.strip()
        self.write('new.txt','PUBLIC LATER\n');self.git('add','.')
        self.git('commit','-qm','Synthetic later commit')
        data=self.push_input().split();data[3]=remote
        self.blocked(self.guard('pre-push',input=' '.join(data)+'\n'))

    def test_commit_limit_blocks_instead_of_truncating_history(self):
        tree=self.git('rev-parse','HEAD^{tree}').stdout.strip();tip=self.base
        for index in range(129):
            tip=self.git('commit-tree',tree,'-p',tip,input='Synthetic commit '+str(index)+'\n').stdout.strip()
        self.blocked(self.guard('pre-push',input=self.push_input(tip)))

    def test_git_executable_is_not_selected_from_worktree_path(self):
        fake=self.repo/'git';marker=self.root/'unexpected-git'
        fake.write_text('#!/bin/sh\n: > '+shlex.quote(str(marker))+'\nexit 2\n');fake.chmod(0o700)
        env=dict(self.env,PATH=str(self.repo)+':'+self.env.get('PATH',''))
        self.assertEqual(self.guard(env=env).returncode,0)
        self.assertFalse(marker.exists())
