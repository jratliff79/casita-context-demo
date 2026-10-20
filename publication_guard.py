#!/usr/bin/env python3
"""Check staged snapshots and outgoing Git commits using an external private policy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import unicodedata

GIT = '/usr/bin/git'
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 4096
MAX_COMMITS = 128
MAX_RECORD = 65536
OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
EMAIL = re.compile(r'(?:[0-9]+\+)?[A-Za-z0-9][A-Za-z0-9-]*@users\.noreply\.github\.com\Z')


class Blocked(Exception):
    """Codes are static: diagnostics must never contain matched material."""


def require(condition, code):
    if not condition:
        raise Blocked(code)


def text(data):
    value = data.decode('utf-8', errors='strict')
    require(not any(ord(c) < 32 and c not in '\r\n\t' for c in value), 'non-text-content')
    return unicodedata.normalize('NFKC', value).casefold()


def outside(path, root):
    path = path.resolve()
    require(path != root and root not in path.parents, 'private-location-required')
    # Do not move the policy into a different working repository by accident.
    require(not any((parent / '.git').exists() for parent in path.parents), 'private-location-required')
    return path


def private_file(path):
    require(path.is_absolute() and not path.is_symlink(), 'private-policy-file')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o077 and info.st_nlink == 1, 'private-policy-permissions')
    require(info.st_size <= MAX_RECORD, 'policy-limit')
    # O_NOFOLLOW also closes the leaf-symlink window between inspection and open.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        require(os.fstat(stream.fileno()) == info, 'policy-changed')
        data = stream.read(MAX_RECORD + 1)
    require(len(data) <= MAX_RECORD, 'policy-limit')
    return data


class Policy:
    def __init__(self, path, root):
        outside(path, root)
        raw = private_file(path)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'policy-schema')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        require(isinstance(value, dict) and set(value) == {'schema', 'blocked_literals', 'blocked_tokens'}
                and value['schema'] == 'casita.publication-policy.v1', 'policy-schema')
        groups = []
        for key in ['blocked_literals', 'blocked_tokens']:
            items = value[key]
            require(isinstance(items, list) and len(items) <= 128, 'policy-schema')
            require(all(isinstance(item, str) and 2 <= len(item) <= 1024 and item == item.strip()
                        and all(ord(c) >= 32 for c in item) for item in items), 'policy-schema')
            for item in items:
                item.encode('utf-8', errors='strict')
            groups.append([unicodedata.normalize('NFKC', item).casefold() for item in items])
        self.literals, tokens = groups
        require(self.literals or tokens, 'empty-policy')
        self.tokens = [re.compile(r'(?<![a-z0-9])' + re.escape(item) + r'(?![a-z0-9])') for item in tokens]

    def check(self, data, code):
        value = text(data)
        require(not any(item in value for item in self.literals)
                and not any(item.search(value) for item in self.tokens), code)


class Git:
    def __init__(self, repo):
        self.root = Path(repo).resolve()
        self.env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1', GIT_GRAFT_FILE=os.devnull,
                        GIT_NO_LAZY_FETCH='1', GIT_TERMINAL_PROMPT='0')
        actual = self.run('rev-parse', '--show-toplevel').decode().strip()
        require(Path(actual).resolve() == self.root, 'repository-context')
        partial = subprocess.run([GIT, '-C', str(self.root), 'config', '--get-regexp',
            r'^(extensions\.partialclone|remote\..*\.promisor)$'], env=self.env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        require(partial.returncode == 1, 'partial-clone-not-supported')

    def run(self, *args):
        result = subprocess.run([GIT, '--no-pager', '--no-replace-objects',
            '-c', 'core.fsmonitor=false', '-C', str(self.root), *args],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        require(result.returncode == 0, 'git-read-or-config')
        return result.stdout

    def config(self, key):
        result = subprocess.run([GIT, '-C', str(self.root), 'config', '--local', '--get', key],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        require(result.returncode in (0, 1), 'git-config')
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def base(self, value=None):
        value = value or self.config('publicationGuard.base')
        require(isinstance(value, str) and OID.fullmatch(value), 'reviewed-base-required')
        require(self.run('cat-file', '-t', value).strip() == b'commit', 'reviewed-base-required')
        return value


class Scan:
    def __init__(self, git, policy):
        self.git, self.policy = git, policy
        self.blobs = set()
        self.total = 0

    def identity(self, raw):
        self.policy.check(raw, 'private-identity')
        value = raw.decode('utf-8')
        match = re.fullmatch(r'([^<>\r\n\x00]+) <([^<>\s]+)> [0-9]+ [+-][0-9]{4}', value)
        require(match and match[1].strip(), 'identity-format')
        require(EMAIL.fullmatch(match[2]) or match[2] == 'noreply@github.com', 'noreply-identity-required')

    def blob(self, oid):
        if oid in self.blobs:
            return
        size = int(self.git.run('cat-file', '-s', oid))
        require(size <= MAX_FILE and self.total + size <= MAX_TOTAL, 'content-limit')
        data = self.git.run('cat-file', 'blob', oid)
        require(len(data) == size, 'object-size')
        self.policy.check(data, 'private-content')
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            value = None
        require(not (isinstance(value, dict) and value.get('schema') ==
                     'casita.publication-policy.v1'), 'private-policy-copy')
        self.blobs.add(oid); self.total += size

    def entries(self, data, index=False):
        require(len(data) <= 2 * 1024 * 1024, 'file-list-limit')
        rows = data.split(b'\0')
        require(rows[-1] == b'' and len(rows) - 1 <= MAX_FILES, 'file-list-limit')
        for row in rows[:-1]:
            header, path = row.split(b'\t', 1)
            mode, kind, oid = header.decode('ascii').split()
            if index:
                oid, stage = kind, oid
                require(stage == '0', 'unmerged-index')
            else:
                require(kind == 'blob', 'regular-files-required')
            require(mode in ('100644', '100755') and OID.fullmatch(oid), 'regular-files-required')
            self.policy.check(path, 'private-filename')
            name = path.decode('utf-8')
            require(not any(ord(c) < 32 for c in name), 'filename-control')
            parts = Path(name).parts
            require(parts and parts[0] != 'output' and '.git' not in parts
                    and not any(p == '.env' or p.startswith('.env.') for p in parts)
                    and Path(name).suffix.lower() not in ('.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.casitar'),
                    'generated-or-credential-path')
            self.blob(oid)

    def staged(self):
        # Respect GIT_INDEX_FILE: Git uses alternate indexes for some commit forms.
        self.entries(self.git.run('ls-files', '--stage', '-z'), index=True)
        for key in ('GIT_AUTHOR_IDENT', 'GIT_COMMITTER_IDENT'):
            self.identity(self.git.run('var', key).rstrip(b'\n'))

    def commit(self, oid):
        require(self.git.run('cat-file', '-t', oid).strip() == b'commit', 'commit-required')
        size = int(self.git.run('cat-file', '-s', oid))
        require(size <= MAX_RECORD, 'commit-limit')
        raw = self.git.run('cat-file', 'commit', oid)
        require(len(raw) == size, 'object-size')
        self.policy.check(raw, 'private-commit-metadata')
        headers = raw.split(b'\n\n', 1)[0].split(b'\n')
        for prefix in (b'author ', b'committer '):
            values = [row[len(prefix):] for row in headers if row.startswith(prefix)]
            require(len(values) == 1, 'identity-format')
            self.identity(values[0])  # Raw headers, never mailmap-transformed output.
        self.entries(self.git.run('ls-tree', '-r', '-z', oid))

    def pre_push(self, data):
        require(len(data) <= MAX_RECORD, 'update-limit')
        require(self.git.run('rev-parse', '--is-shallow-repository').strip() == b'false', 'full-history-required')
        base = self.git.base()
        commits = set()
        for line in data.decode('ascii').splitlines():
            fields = line.split()
            require(len(fields) == 4, 'update-format')
            local_ref, oid, remote_ref, old = fields
            require(OID.fullmatch(oid) and OID.fullmatch(old) and len(oid) == len(old)
                    and remote_ref.startswith(('refs/heads/', 'refs/tags/')), 'update-format')
            if set(oid) == {'0'}:
                continue  # Deletion publishes no content, including deletion of old tags.
            require(remote_ref.startswith('refs/heads/')
                    and (local_ref == 'HEAD' or local_ref.startswith('refs/heads/')), 'branch-updates-only')
            self.policy.check(line.encode(), 'private-ref')
            require(self.git.run('cat-file', '-t', oid).strip() == b'commit', 'commit-required')
            self.git.run('merge-base', '--is-ancestor', base, oid)
            pending = self.git.run('rev-list', '--max-count=' + str(MAX_COMMITS + 1), oid, '^' + base).decode().splitlines()
            commits.update(pending)
            require(len(commits) <= MAX_COMMITS, 'commit-count-limit')
            # Even a push of the reviewed base must have a clean tip snapshot.
            self.entries(self.git.run('ls-tree', '-r', '-z', oid))
        for oid in sorted(commits):
            self.commit(oid)
        return len(commits)


def install(git, policy_path, base, runtime):
    git.base(base)
    # Refuse any configured hook directory; do not disable or replace another guard.
    result = subprocess.run([GIT, '-C', str(git.root), 'config', '--get', 'core.hooksPath'],
        env=git.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    require(result.returncode == 1, 'existing-hooks-path')
    require(runtime.is_absolute() and not runtime.is_symlink(), 'private-runtime-required')
    runtime = outside(runtime, git.root)
    hooks = Path(os.fsdecode(git.run('rev-parse', '--git-path', 'hooks').rstrip(b'\n')))
    if not hooks.is_absolute(): hooks = git.root / hooks
    require(not hooks.is_symlink(), 'existing-hooks-path')
    source = Path(__file__).read_bytes()
    snapshot = runtime / ('guard-' + hashlib.sha256(source).hexdigest() + '.py')
    wrappers = {name: ('#!/bin/sh\n# casita-publication-guard v1\nexec ' + shlex.quote(sys.executable)
        + ' -I ' + shlex.quote(str(snapshot)) + ' ' + mode + ' --repo .\n').encode()
        for name, mode in [('pre-commit', 'staged'), ('pre-push', 'pre-push')]}
    for name, data in wrappers.items():
        target = hooks / name
        require(not target.is_symlink() and (not target.exists() or
                (target.is_file() and private_file(target) == data
                 and target.stat().st_mode & stat.S_IXUSR)), 'existing-hook')
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = runtime.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'private-runtime-permissions')
    if snapshot.exists():
        require(private_file(snapshot) == source, 'installed-copy-mismatch')
    else:
        with os.fdopen(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(source)
        snapshot.chmod(0o400)
    hooks.mkdir(parents=True, exist_ok=True)
    created = []
    previous = {key: git.config(key) for key in ('publicationGuard.policy', 'publicationGuard.base')}
    try:
        for name, data in wrappers.items():
            target = hooks / name
            if not target.exists():
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
                created.append(target)
                with os.fdopen(fd, 'wb') as stream: stream.write(data)
        git.run('config', '--local', 'publicationGuard.policy', str(policy_path))
        git.run('config', '--local', 'publicationGuard.base', base)
    except Exception:
        for path in created: path.unlink()
        for key, value in previous.items():
            if value is not None: git.run('config', '--local', key, value)
            else:
                subprocess.run([GIT, '-C', str(git.root), 'config', '--local', '--unset-all', key],
                    env=git.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Blocked('arguments')


def main():
    try:
        os.umask(0o077)
        parser = Parser(description=__doc__)
        parser.add_argument('mode', choices=['staged', 'pre-push', 'install'])
        parser.add_argument('--repo', type=Path, default=Path('.'))
        parser.add_argument('--policy', type=Path)
        parser.add_argument('--base')
        parser.add_argument('--runtime-dir', type=Path)
        args = parser.parse_args()
        git = Git(args.repo)
        configured = args.policy or git.config('publicationGuard.policy')
        require(bool(configured), 'private-policy-required')
        policy_path = Path(configured)
        policy = Policy(policy_path, git.root)
        if args.mode == 'install':
            require(args.policy is not None and args.runtime_dir is not None and args.base is not None, 'install-arguments')
            install(git, policy_path, args.base, args.runtime_dir)
        else:
            scan = Scan(git, policy)
            if args.mode == 'staged': scan.staged()
            else: scan.pre_push(sys.stdin.buffer.read(MAX_RECORD + 1))
        print('PASS: publication guard ' + args.mode)
        return 0
    except Blocked as error:
        print('BLOCKED: publication guard (' + str(error) + ')', file=sys.stderr)
    except (Exception, KeyboardInterrupt):
        # Git/JSON/OS exceptions can contain paths or sensitive text. Never echo them.
        print('BLOCKED: publication guard (input-or-git-error)', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
