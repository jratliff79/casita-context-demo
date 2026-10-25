#!/usr/bin/env python3
"""Check staged snapshots and outgoing Git commits using an external private policy."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import unicodedata

GIT = '/usr/bin/git'
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 4096
MAX_COMMITS = 128
MAX_RECORD = 65536
MAX_LISTING = 2 * 1024 * 1024
GIT_TIMEOUT = 30
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
        require(path.is_absolute() and not path.is_symlink(), 'private-policy-file')
        path = outside(path, root)
        protected_directory(path.parent, 'policy')
        raw = private_file(path)
        self.path = path
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
        status, _ = self._capture('config', '--get', 'extensions.partialclone', allowed=(0, 1))
        require(status == 1, 'partial-clone-not-supported')
        _, promisors = self._capture('config', '--null', '--type=bool', '--get-regexp',
            r'^remote\..*\.promisor$', allowed=(0, 1), limit=MAX_LISTING)
        require(not any(
            row.rsplit(b'\n', 1)[-1] == b'true' for row in promisors.split(b'\0') if row),
            'partial-clone-not-supported')

    def _capture(self, *args, limit=MAX_RECORD, allowed=(0,), limit_code='git-output-limit'):
        deadline = time.monotonic() + GIT_TIMEOUT
        process = subprocess.Popen([GIT, '--no-pager', '--no-replace-objects',
            '-c', 'core.fsmonitor=false', '-C', str(self.root), *args],
            env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True)
        chunks = []; size = 0
        selector = None
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'git-timeout')
                if not selector.select(remaining):
                    raise Blocked('git-timeout')
                data = os.read(process.stdout.fileno(), min(65536, limit + 1 - size))
                if not data:
                    break
                size += len(data)
                require(size <= limit, limit_code)
                chunks.append(data)
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'git-timeout')
            process.wait(timeout=remaining)
            require(process.returncode in allowed, 'git-read-or-config')
            return process.returncode, b''.join(chunks)
        except BaseException:
            # A leader may have exited while a descendant still owns stdout.
            # Kill the session even then; never drain rejected output.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                process.stdout.close()
                process.wait()
            raise
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()

    def run(self, *args, limit=MAX_RECORD, limit_code='git-output-limit'):
        return self._capture(*args, limit=limit, limit_code=limit_code)[1]

    def path(self, *args):
        output = self.run(*args)
        require(output.endswith(b'\n') and len(output) > 1, 'git-path-format')
        return Path(os.fsdecode(output[:-1]))

    def config(self, key):
        status, output = self._capture('config', '--local', '--get', key, allowed=(0, 1))
        return output.decode().strip() if status == 0 else None

    def base(self, value=None):
        value = self.config('publicationGuard.base') if value is None else value
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
        data = self.git.run('cat-file', 'blob', oid, limit=size, limit_code='object-size')
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
        require(len(data) <= MAX_LISTING, 'file-list-limit')
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
        self.entries(self.git.run('ls-files', '--stage', '-z', limit=MAX_LISTING,
                                  limit_code='file-list-limit'), index=True)
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
        self.entries(self.git.run('ls-tree', '-r', '-z', oid, limit=MAX_LISTING,
                                  limit_code='file-list-limit'))

    def pre_push(self, data):
        require(len(data) <= MAX_RECORD, 'update-limit')
        require(self.git.run('rev-parse', '--is-shallow-repository').strip() == b'false', 'full-history-required')
        base = self.git.base()
        commits = set()
        for line in data.decode('utf-8').splitlines():
            fields = line.rsplit(None, 3)
            require(len(fields) == 4, 'update-format')
            local_ref, oid, remote_ref, old = fields
            require(OID.fullmatch(oid) and OID.fullmatch(old) and len(oid) == len(old)
                    and remote_ref.startswith(('refs/heads/', 'refs/tags/')), 'update-format')
            if set(oid) == {'0'}:
                continue  # Deletion publishes no content, including deletion of old tags.
            require(remote_ref.startswith('refs/heads/'), 'branch-updates-only')
            self.policy.check(line.encode(), 'private-ref')
            require(self.git.run('cat-file', '-t', oid).strip() == b'commit', 'commit-required')
            self.git.run('merge-base', '--is-ancestor', base, oid)
            pending = self.git.run('rev-list', '--max-count=' + str(MAX_COMMITS + 1), oid, '^' + base).decode().splitlines()
            commits.update(pending)
            require(len(commits) <= MAX_COMMITS, 'commit-count-limit')
            # Even a push of the reviewed base must have a clean tip snapshot.
            self.entries(self.git.run('ls-tree', '-r', '-z', oid, limit=MAX_LISTING,
                                      limit_code='file-list-limit'))
        for oid in sorted(commits):
            self.commit(oid)
        return len(commits)


def protected_directory(path, context):
    # A private leaf can still be renamed through a writable parent. Check the
    # complete canonical chain; root/user-owned sticky temporary dirs are safe.
    for parent in reversed([path, *path.parents]):
        if not parent.exists():
            continue
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, os.getuid()),
                context + '-ancestor-owner')
        require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX,
                context + '-ancestor-writable')


def protected_git_path(path, context, *, file=False):
    """Check canonical targets and the namespaces used to resolve symlinks."""
    require(path.is_absolute(), context + '-absolute-path')
    pending = [(path, file)]; visited = set(); links = 0
    while pending:
        current, leaf_file = pending.pop()
        if (current, leaf_file) in visited:
            continue
        visited.add((current, leaf_file))
        for parent in reversed([current, *current.parents]):
            try:
                info = parent.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                require(info.st_uid in (0, os.getuid()), context + '-ancestor-owner')
                links += 1
                require(links <= 40, context + '-symlink-limit')
                target = Path(os.readlink(parent))
                if not target.is_absolute():
                    target = parent.parent / target
                parent.resolve(strict=True)
                pending.append((target, leaf_file and parent == current))
            elif leaf_file and parent == current:
                require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, os.getuid()),
                        context + '-pointer-owner')
                require(not info.st_mode & 0o022, context + '-pointer-writable')
            else:
                protected_directory(parent.resolve(), context)
    return path.resolve()


def git_pointer(path, context, prefix=b''):
    canonical = protected_git_path(path, context, file=True)
    with os.fdopen(os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        data = stream.read(MAX_RECORD + 1)
    require(len(data) <= MAX_RECORD and data.startswith(prefix), context + '-pointer-format')
    # Git trims line endings, not path whitespace, in gitfiles and commondir.
    raw_target = data[len(prefix):].rstrip(b'\r\n')
    require(bool(raw_target) and b'\0' not in raw_target, context + '-pointer-format')
    target = Path(os.fsdecode(raw_target))
    return target if target.is_absolute() else path.parent / target


def protected_installation(git, hooks):
    def absolute(path):
        return path if path.is_absolute() else git.root / path
    protected_git_path(git.root, 'repository')
    metadata = absolute(git.path('rev-parse', '--git-dir'))
    common = absolute(git.path('rev-parse', '--git-common-dir'))
    paths = [metadata, common]
    selector = absolute(Path(git.env['GIT_DIR'])) if git.env.get('GIT_DIR') else git.root / '.git'
    paths.append(selector if selector.is_dir() else
                 git_pointer(selector, 'git-metadata', b'gitdir: '))
    if git.env.get('GIT_COMMON_DIR'):
        paths.append(absolute(Path(git.env['GIT_COMMON_DIR'])))
    else:
        pointer = metadata / 'commondir'
        if pointer.exists() or pointer.is_symlink():
            paths.append(git_pointer(pointer, 'git-common'))
    for path in paths:
        protected_git_path(path, 'git-metadata')
    return protected_git_path(hooks, 'hooks')


def install(git, policy_path, base, runtime):
    base = git.base(base)
    # Refuse any configured hook directory; do not disable or replace another guard.
    status, _ = git._capture('config', '--get', 'core.hooksPath', allowed=(0, 1))
    require(status == 1, 'existing-hooks-path')
    require(runtime.is_absolute() and not runtime.is_symlink(), 'private-runtime-required')
    runtime = outside(runtime, git.root)
    protected_directory(runtime, 'runtime')
    hooks = git.path('rev-parse', '--git-path', 'hooks')
    if not hooks.is_absolute(): hooks = git.root / hooks
    require(not hooks.is_symlink(), 'existing-hooks-path')
    hooks = protected_installation(git, hooks)
    source = Path(__file__).read_bytes()
    snapshot = runtime / ('guard-' + hashlib.sha256(source).hexdigest() + '.py')
    wrappers = {name: ('#!/bin/sh\n# casita-publication-guard v1\nexec 3>&2 2>/dev/null\n'
        + shlex.quote(sys.executable) + ' -I ' + shlex.quote(str(snapshot))
        + ' ' + mode + ' --repo . 3>&-\n'
        + 'publication_status=$?\n'
        + 'if [ "$publication_status" -ne 0 ]; then\n'
        + "  printf '%s\\n' 'BLOCKED: publication guard (hook-failure)' >&3\n"
        + 'fi\nexit "$publication_status"\n').encode()
        for name, mode in [('pre-commit', 'staged'), ('pre-push', 'pre-push')]}
    for name, data in wrappers.items():
        target = hooks / name
        require(not target.is_symlink() and (not target.exists() or
                (target.is_file() and private_file(target) == data
                 and target.stat().st_mode & stat.S_IXUSR)), 'existing-hook')
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    protected_directory(runtime, 'runtime')
    info = runtime.stat()
    require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'private-runtime-permissions')
    if snapshot.exists():
        require(private_file(snapshot) == source, 'installed-copy-mismatch')
    else:
        with os.fdopen(os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(source)
        snapshot.chmod(0o400)
    hooks.mkdir(parents=True, exist_ok=True)
    hooks = protected_installation(git, hooks)
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
                git._capture('config', '--local', '--unset-all', key, allowed=(0, 5))
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
        policy_path = policy.path
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
