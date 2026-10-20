"""Additional failure cases for the publication boundary; synthetic policy only."""
import json

import test_publication_guard as fixtures

# Reuse Git-fixture helpers without collecting the parent test methods twice.
class Fixture(fixtures.PublicationGuard):
    pass
for _name in list(dir(Fixture)):
    if _name.startswith('test_'):
        setattr(Fixture, _name, None)


class GuardEdges(Fixture):
    def test_policy_json_is_not_publishable_even_when_unicode_escaped(self):
        self.policy.write_text(json.dumps(dict(schema='casita.publication-policy.v1',
            blocked_literals=['\u00c5-private-\u4e00'],blocked_tokens=[]),ensure_ascii=True))
        self.write('copied.json',self.policy.read_text());self.git('add','.')
        self.blocked(self.guard())

    def test_identical_but_nonexecutable_hook_is_not_reported_as_installed(self):
        self.assertEqual(self.install().returncode,0)
        hook=self.repo/'.git/hooks/pre-commit';hook.chmod(0o600)
        self.blocked(self.install())

    def test_custom_hooks_directory_is_preserved(self):
        other=self.root/'other-hooks';other.mkdir()
        self.git('config','core.hooksPath',str(other))
        self.blocked(self.install());self.assertEqual(list(other.iterdir()),[])

    def test_installed_copy_does_not_follow_worktree_code_or_pythonpath(self):
        self.assertEqual(self.install().returncode,0)
        self.write('publication_guard.py','raise SystemExit(0)\n')
        self.write('safe.txt','SYNTHETIC_PRIVATE_CUSTOMER_7341');self.git('add','.')
        env=dict(self.env,PYTHONPATH=str(self.repo))
        result=self.git('commit','-qm','Synthetic attempted weakening',check=False,env=env)
        self.blocked(result)

    def test_delete_then_readd_bad_history_remains_checked(self):
        self.write('next.txt','PUBLIC\n');self.git('add','.')
        env=dict(self.env,GIT_COMMITTER_EMAIL='machine-private@host.local')
        self.git('commit','-qm','Synthetic bad committer',env=env)
        self.blocked(self.guard('pre-push',input=self.push_input()))

    def test_shallow_and_unmerged_indexes_fail_closed(self):
        self.git('update-index','--index-info',input='100644 '+self.git('rev-parse','HEAD:safe.txt').stdout.strip()+' 1\tconflict.txt\n')
        self.blocked(self.guard())
        self.git('reset','--hard','-q','HEAD')
        (self.repo/'.git/shallow').write_text(self.base+'\n')
        self.blocked(self.guard('pre-push',input=self.push_input()))
