"""Real subprocess probes for bounded Git output and process cleanup."""
import contextlib
import io
import os
import signal
import subprocess
import sys
import time
from unittest import mock

import publication_guard as guard
from test_publication_guard import PRIVATE
import test_publication_guard_edges as fixtures


class Streaming(fixtures.Fixture):
    def producer(self, commands, descendant=False):
        completed = self.root / 'producer-completed'
        helper = self.root / 'synthetic-git'
        helper.write_text('#!' + sys.executable + '\n' +
            'import os, sys\n' +
            'from pathlib import Path\n' +
            'if any(value in sys.argv for value in ' + repr(commands) + '):\n' +
            ('    import signal\n'
             '    child = os.fork()\n'
             '    if child:\n'
             '        Path(' + repr(str(self.root / 'descendant-pid')) + ').write_text(str(child))\n'
             '        os._exit(0)\n'
             '    signal.pause()\n' if descendant else '') +
            '    prefix = (b"100644 " + b"1" * 40 + b" 0\\t" if "ls-files" in sys.argv else b"100644 blob " + b"1" * 40 + b"\\t")\n' +
            '    sys.stdout.buffer.write(prefix)\n' +
            '    for _ in range(128):\n' +
            '        sys.stdout.buffer.write(b"a" * 65536)\n' +
            '    sys.stdout.buffer.write(b"\\0"); sys.stdout.buffer.flush()\n' +
            '    Path(' + repr(str(completed)) + ').write_text("complete")\n' +
            'else:\n' +
            '    os.execv("/usr/bin/git", ["/usr/bin/git", *sys.argv[1:]])\n')
        helper.chmod(0o700)
        return helper, completed

    def assert_listing_stopped(self, mode, fault=None):
        command = 'ls-files' if mode == 'staged' else 'ls-tree'
        helper, completed = self.producer([command], descendant=fault == 'descendant')
        children = []
        real_popen = subprocess.Popen
        real_clock = time.monotonic; real_read = os.read
        active = False; elapsed = 0

        def observe(*args, **kwargs):
            nonlocal active
            child = real_popen(*args, **kwargs)
            if command in args[0]:
                children.append(child)
                def cleanup():
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                self.addCleanup(cleanup)
                if fault == 'descendant':
                    pidfile = self.root / 'descendant-pid'
                    ready_deadline = real_clock() + 5
                    while not pidfile.exists() and real_clock() < ready_deadline:
                        time.sleep(0.01)
                    self.assertTrue(pidfile.exists(), 'descendant fixture was not prepared')
                    child.wait(timeout=5)
                active = True
            return child

        def clock():
            nonlocal elapsed
            if active and fault in ('timeout', 'descendant'):
                elapsed += 31
            return real_clock() + elapsed

        def read(*args):
            if active and fault == 'interrupt':
                raise KeyboardInterrupt
            return real_read(*args)

        out = io.StringIO(); err = io.StringIO()
        args = ['publication_guard.py', 'pre-push' if mode == 'commit' else mode,
                '--repo', str(self.repo), '--policy', str(self.policy)]
        stdin = mock.Mock(buffer=io.BytesIO(self.push_input().encode()))
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(guard, 'GIT', str(helper)), \
                mock.patch.object(subprocess, 'Popen', side_effect=observe), \
                mock.patch.object(sys, 'argv', args), \
                mock.patch.object(sys, 'stdin', stdin), \
                mock.patch.object(time, 'monotonic', side_effect=clock), \
                mock.patch.object(subprocess, '_time', side_effect=clock), \
                mock.patch.object(os, 'read', side_effect=read), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if mode == 'commit':
                scanner = guard.Scan(guard.Git(self.repo), guard.Policy(self.policy, self.repo))
                with self.assertRaises(guard.Blocked):
                    scanner.commit(self.base)
            else:
                self.assertEqual(guard.main(), 1)
                self.assertIn('BLOCKED', err.getvalue())
        self.assertFalse(completed.exists(), 'Git stdout was buffered through producer completion')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode, 'producer was not reaped')
        if fault != 'descendant':
            self.assertNotEqual(children[0].returncode, 0, 'producer was allowed to finish')
        with self.assertRaises(ChildProcessError):
            os.waitpid(children[0].pid, os.WNOHANG)
        for sensitive in (str(self.root), PRIVATE):
            self.assertNotIn(sensitive, out.getvalue() + err.getvalue())
        if fault == 'descendant':
            # The direct child may exit while its descendant still owns stdout.
            # Observe termination; do not infer it merely from the leader's exit.
            pidfile = self.root / 'descendant-pid'
            if pidfile.exists():
                pid = int(pidfile.read_text())
                def state():
                    return subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(pid)],
                        capture_output=True, text=True, check=False).stdout.strip()
                try:
                    deadline = real_clock() + 2
                    status = state()
                    while status and not status.startswith('Z') and real_clock() < deadline:
                        time.sleep(0.01); status = state()
                    self.assertTrue(not status or status.startswith('Z'),
                                    'descendant survived the read deadline')
                finally:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_staged_listing_stops_producer_and_reaps_it(self):
        self.assert_listing_stopped('staged')

    def test_outgoing_tip_listing_stops_producer_and_reaps_it(self):
        self.assert_listing_stopped('pre-push')

    def test_raw_commit_listing_stops_producer_and_reaps_it(self):
        self.assert_listing_stopped('commit')

    def test_valid_blob_larger_than_listing_limit_remains_supported(self):
        self.write('large-public.txt', 'a' * (3 * 1024 * 1024))
        self.git('add', '.')
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git('commit', '-qm', 'Synthetic large public content')
        result = self.guard('pre-push', input=self.push_input())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_deadline_reaps_the_git_child(self):
        self.assert_listing_stopped('staged', fault='timeout')

    def test_interrupt_reaps_the_git_child_without_diagnostics_leak(self):
        self.assert_listing_stopped('staged', fault='interrupt')

    def test_deadline_stops_descendant_after_the_git_leader_exits(self):
        self.assert_listing_stopped('staged', fault='descendant')
