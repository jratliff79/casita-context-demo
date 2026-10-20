"""Publication checks use real Git indexes, commit objects and local hook execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'publication_guard.py'
EMAIL = '123456+synthetic-owner@users.noreply.github.com'
PRIVATE = 'SYNTHETIC_PRIVATE_CUSTOMER_7341'


class PublicationGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='synthetic-owner', GIT_AUTHOR_EMAIL=EMAIL,
                        GIT_COMMITTER_NAME='synthetic-owner', GIT_COMMITTER_EMAIL=EMAIL)
        self.git('init', '-q'); self.git('config', 'user.name', 'synthetic-owner')
        self.git('config', 'user.email', EMAIL)
        self.write('safe.txt', 'PUBLIC SYNTHETIC FIXTURE\n'); self.git('add', '.')
        self.git('commit', '-qm', 'Synthetic baseline')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()
        self.policy = self.root / 'private-policy.json'
        self.policy.write_text(json.dumps(dict(schema='casita.publication-policy.v1',
            blocked_literals=[PRIVATE], blocked_tokens=['syntheticoperator'])))
        self.policy.chmod(0o600)
        self.git('config', '--local', 'publicationGuard.policy', str(self.policy))
        self.git('config', '--local', 'publicationGuard.base', self.base)

    def git(self, *args, check=True, env=None, input=None):
        return subprocess.run(['git', '-C', str(self.repo), *args], text=True,
            capture_output=True, check=check, env=self.env if env is None else env, input=input)

    def write(self, name, text):
        path = self.repo / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def guard(self, mode='staged', *args, env=None, input=None):
        # Missing implementation is an explicit behavior failure, not an import error.
        self.assertTrue(SCRIPT.is_file(), 'publication guard is not implemented')
        return subprocess.run([sys.executable, '-I', str(SCRIPT), mode,
            '--repo', str(self.repo), *args], text=True, capture_output=True,
            env=self.env if env is None else env, input=input)

    def blocked(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('BLOCKED', result.stderr)
        for secret in [PRIVATE, str(self.policy), str(self.repo), 'machine-private@host.local']:
            self.assertNotIn(secret, result.stdout + result.stderr)

    def push_input(self, oid=None):
        oid = oid or self.git('rev-parse', 'HEAD').stdout.strip()
        return 'refs/heads/synthetic '+oid+' refs/heads/synthetic '+'0'*len(oid)+'\n'

    def test_clean_staged_snapshot_and_effective_identities_pass(self):
        self.write('space name.txt', 'PUBLIC\n'); self.git('add', '.')
        self.assertEqual(self.guard().returncode, 0)

    def test_scans_index_not_worktree_and_rejects_sensitive_filenames(self):
        self.write('safe.txt', PRIVATE); self.git('add', '.')
        self.write('safe.txt', 'PUBLIC\n'); self.blocked(self.guard())
        self.git('add', '.'); self.write('safe.txt', PRIVATE)
        self.assertEqual(self.guard().returncode, 0)
        self.write(PRIVATE+'.txt', 'PUBLIC\n'); self.git('add', PRIVATE+'.txt')
        self.blocked(self.guard())

    def test_alternate_index_is_honored(self):
        alternate=self.root/'alternate-index'; env=dict(self.env,GIT_INDEX_FILE=str(alternate))
        self.git('read-tree', 'HEAD', env=env)
        self.write('safe.txt', PRIVATE); self.git('add', '.', env=env)
        self.blocked(self.guard(env=env))
        self.assertEqual(self.guard().returncode, 0)

    def test_missing_malformed_permissive_or_internal_policy_blocks(self):
        variants=['missing','malformed','permissions','inside','symlink','empty']
        for variant in variants:
            with self.subTest(variant=variant):
                path=self.root/(variant+'.json')
                if variant=='symlink': path.symlink_to(self.policy)
                elif variant!='missing':
                    path.write_bytes(self.policy.read_bytes());path.chmod(0o600)
                if variant=='malformed': path.write_text('{')
                if variant=='permissions':path.chmod(0o644)
                if variant=='inside':
                    path=self.repo/'private.json';path.write_bytes(self.policy.read_bytes());path.chmod(0o600)
                if variant=='empty':path.write_text(json.dumps(dict(schema='casita.publication-policy.v1',blocked_literals=[],blocked_tokens=[])))
                self.blocked(self.guard('staged','--policy',str(path)))

    def test_effective_author_and_committer_overrides_are_checked(self):
        for key in ['GIT_AUTHOR_EMAIL','GIT_COMMITTER_EMAIL']:
            with self.subTest(key=key):
                self.blocked(self.guard(env=dict(self.env,**{key:'machine-private@host.local'})))
        self.blocked(self.guard(env=dict(self.env,GIT_AUTHOR_NAME=PRIVATE)))

    def test_unicode_case_and_token_matching_without_substring_false_positive(self):
        self.write('safe.txt', PRIVATE.lower());self.git('add','.');self.blocked(self.guard())
        self.write('safe.txt', 'an_syntheticoperator_note');self.git('add','.');self.blocked(self.guard())
        self.write('safe.txt', 'syntheticoperatorname');self.git('add','.')
        self.assertEqual(self.guard().returncode,0)

    def test_unreadable_content_modes_and_generated_artifacts_block(self):
        for variant in ['binary','link','output','oversize']:
            with self.subTest(variant=variant):
                self.git('reset','--hard','-q','HEAD');self.git('clean','-fdq')
                if variant=='binary':(self.repo/'bad.bin').write_bytes(b'\xff\x00')
                elif variant=='link':(self.repo/'linked').symlink_to('safe.txt')
                elif variant=='output':self.write('output/state.json','{}')
                else:(self.repo/'large.txt').write_bytes(b'x'*(8*1024*1024+1))
                self.git('add','-f','.');self.blocked(self.guard())

    def test_pre_push_scans_intermediate_snapshots_and_raw_commit_identities(self):
        self.write('safe.txt', PRIVATE);self.git('add','.');self.git('commit','-qm','Synthetic intermediate')
        self.write('safe.txt','PUBLIC\n');self.git('add','.');self.git('commit','-qm','Synthetic cleanup')
        self.blocked(self.guard('pre-push',input=self.push_input()))
        self.git('reset','--hard','-q',self.base)
        self.write('next.txt','PUBLIC\n');self.git('add','.')
        env=dict(self.env,GIT_AUTHOR_EMAIL='machine-private@host.local')
        self.git('commit','-qm','Synthetic bad identity',env=env)
        self.write('.mailmap','synthetic-owner <'+EMAIL+'> <machine-private@host.local>\n')
        self.blocked(self.guard('pre-push',input=self.push_input()))

    def test_pre_push_rejects_commit_message_ref_and_unrelated_history(self):
        self.write('next.txt','PUBLIC\n');self.git('add','.');self.git('commit','-qm',PRIVATE)
        self.blocked(self.guard('pre-push',input=self.push_input()))
        self.git('reset','--hard','-q',self.base)
        self.blocked(self.guard('pre-push',input=self.push_input().replace('refs/heads/synthetic','refs/heads/'+PRIVATE)))
        self.git('checkout','--orphan','synthetic-unrelated','-q');self.git('add','.')
        self.git('commit','-qm','Synthetic unrelated root')
        self.blocked(self.guard('pre-push',input=self.push_input()))

    def test_pre_push_rejects_malformed_updates_and_tags_allows_deletions(self):
        for data in ['bad\n','HEAD nooid refs/heads/synthetic '+'0'*40+'\n',
                     self.push_input().replace('refs/heads/synthetic','refs/tags/synthetic')]:
            with self.subTest(data=data):self.blocked(self.guard('pre-push',input=data))
        data='(delete) '+'0'*40+' refs/heads/synthetic '+self.base+'\n'
        self.assertEqual(self.guard('pre-push',input=data).returncode,0)

    def test_clean_outgoing_commit_passes_and_worktree_does_not_affect_it(self):
        self.write('next.txt','PUBLIC\n');self.git('add','.');self.git('commit','-qm','Synthetic public change')
        self.write('next.txt',PRIVATE)
        self.assertEqual(self.guard('pre-push',input=self.push_input()).returncode,0)

    def install(self):
        return self.guard('install','--policy',str(self.policy),'--base',self.base,
                          '--runtime-dir',str(self.root/'private-runtime'))

    def test_installed_hooks_block_real_commit_and_real_push_without_leaking_policy(self):
        result=self.install();self.assertEqual(result.returncode,0,result.stderr)
        self.write('safe.txt',PRIVATE);self.git('add','.')
        result=self.git('commit','-qm','Synthetic blocked commit',check=False);self.blocked(result)
        self.git('reset','--hard','-q',self.base)
        self.write('safe.txt',PRIVATE);self.git('add','.')
        self.git('-c','core.hooksPath='+os.devnull,'commit','-qm','Synthetic bypass fixture')
        remote=self.root/'remote.git';subprocess.run(['git','init','--bare','-q',str(remote)],check=True,env=self.env)
        self.git('remote','add','synthetic',str(remote))
        result=self.git('push','synthetic','HEAD:refs/heads/synthetic',check=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('BLOCKED',result.stderr)
        self.assertEqual(self.git('ls-remote','synthetic').stdout,'')
        hook=(self.repo/'.git/hooks/pre-commit').read_text()
        self.assertNotIn(PRIVATE,hook);self.assertNotIn(PRIVATE,''.join(p.read_text() for p in (self.root/'private-runtime').rglob('*.py')))

    def test_install_preserves_existing_hooks_and_rejects_in_repo_runtime(self):
        hook=self.repo/'.git/hooks/pre-commit';hook.write_text('#!/bin/sh\nexit 0\n');hook.chmod(0o700)
        before=hook.read_bytes();self.blocked(self.install());self.assertEqual(hook.read_bytes(),before)
        hook.unlink()
        self.blocked(self.guard('install','--policy',str(self.policy),'--base',self.base,
                               '--runtime-dir',str(self.repo/'output/runtime')))


if __name__ == '__main__':unittest.main()
