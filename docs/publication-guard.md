# Check a publication before committing or pushing

`publication_guard.py` is a local guard for this public example. It reads the
complete Git index snapshot, filenames and effective author/committer identities
before a commit. Its pre-push check reads raw outgoing commit headers, messages,
ref names and every intermediate snapshot after an explicitly reviewed base.
A later cleanup commit cannot hide an earlier publication in the same branch.

The private identifier policy stays in an owner-only file outside **any Git
working repository**. It is neither a tracked fixture nor a CI secret. The public
unit tests use synthetic policies in temporary repositories; they do not contain
or prove coverage of an operator's real private identifier list.

## Configure privately

Use Python 3.9+, `/usr/bin/git` and a POSIX host. Create an absolute policy path outside any
checkout, with an owner-only directory and mode `0600` file. This is the schema;
replace these synthetic values privately:

```json
{
  "schema": "casita.publication-policy.v1",
  "blocked_literals": ["SYNTHETIC_PRIVATE_PROJECT"],
  "blocked_tokens": ["syntheticoperator"]
}
```

Literal matches are substrings. Token matches require ASCII-letter/digit
boundaries, so underscore-separated labels are checked too. Both checks normalize
Unicode with NFKC and case folding. Empty lists together, unknown fields,
duplicate JSON keys, linked files, shared file permissions, and missing policies
block the check. Copies of policy JSON are rejected as staged/outgoing files,
including JSON with escaped identifiers.

Set a public Git identity in repository-local configuration if it is not already
configured:

```sh
git config --local user.name YOUR_PUBLIC_HANDLE
git config --local user.email YOUR_GITHUB_NOREPLY_ADDRESS
```

Preview the actual staged snapshot, without changing files or committing:

```sh
python3 publication_guard.py staged \
  --policy "$HOME/.config/casita-publication/policy.json"
```

The index is inspected directly, including `GIT_INDEX_FILE` when Git provides an
alternate index. Working-file edits and ignored output are not substitutes for
the indexed bytes. The guard never runs received files, text conversion, or
Git clean/smudge filters. Both effective identities must use GitHub noreply
addresses; raw outgoing commit identities are checked again without mailmap
substitution. This is a formatting/privacy check, not identity authentication.

## Install the reviewed copy

Review the guard and its tests first. Choose a full commit ID already reviewed
and published as the history boundary. The guard does not infer approval from
an arbitrary branch name or remote advertisement. From the repository root:

```sh
python3 publication_guard.py install \
  --policy "$HOME/.config/casita-publication/policy.json" \
  --base REVIEWED_FULL_COMMIT \
  --runtime-dir "$HOME/.local/share/casita-publication"
```

Installation copies the reviewed scanner read-only to a private runtime directory
under a content-hashed filename, then creates `pre-commit` and `pre-push` hooks. They use
the qualified installing Python interpreter with `-I`, `/usr/bin/git`, and the installed copy
rather than a mutable working-tree script. The private policy path and reviewed base are saved
only in repository-local Git configuration. Policy values never go in hooks,
tracked files, arguments or generated public artifacts. Each clone needs its own
installation. Linked worktrees use their actual index and the common hooks.

Installation refuses custom `core.hooksPath` settings and existing hooks with
other content. It preserves those hooks instead of silently replacing or chaining
them. An identical installed hook must still be owner-only and executable.
Inspect a conflict locally before choosing how to integrate it. Runtime ancestors
must belong to root or the installing user and must not be group/world writable
unless protected by the sticky bit. This is checked before and after creation.

Re-running the same reviewed version can advance the base after reviewing a
newly merged main commit. Updating the scanner code requires reviewing the new
version and replacing only the two previously managed hook files before
reinstallation. Preserve unrelated hooks. Keep the private policy and previous
reviewed copy available when updating. To disable the guard deliberately, remove
only its managed hooks and unset the local `publicationGuard.policy` and
`publicationGuard.base` settings.

## Push coverage and limits

The pre-push hook accepts Git's update records on standard input. Positive branch
updates must descend from the configured reviewed base. Git object-expression
sources such as `HEAD~1` or a full commit ID are supported; eligibility uses the
resolved commit and destination branch. Every commit reachable
from the candidate and outside that base's history is checked, regardless of the
advertised remote tip. Old history at/before the reviewed base is preserved;
its metadata is not rewritten. The candidate tip's files are always checked.
Branch/tag deletions publish no content and are allowed. Positive tag updates
require separate review and are blocked by this version.

Partial-clone configuration, shallow outgoing histories, missing objects, unmerged indexes, symlinks/submodules,
non-UTF-8/NUL-containing content, generated root `output/` paths, common credential
and native-state file extensions, and exceeded limits fail the check. Limits are
128 outgoing commits, 4,096 files per snapshot, 8 MiB per file, 64 MiB of unique
blob bytes per invocation, and 64 KiB per commit/policy/update input. The guard
performs no fetch; partial clones must be replaced by a full local clone first.

Success prints `PASS: publication guard` with the mode. Failure prints only a
static `BLOCKED` reason. Hooks suppress interpreter/bootstrap diagnostics and
emit a generic `hook-failure` while preserving the nonzero exit status; direct
CLI checks provide more specific static reasons. It never prints a matched identifier, source excerpt,
filename, policy path, ref or identity. Inspect the relevant staged/committed
bytes privately to resolve a failure; do not paste them into a public PR.

Literal/token matching does not decode arbitrary encoded payloads or determine
whether business content is appropriate to share. Review selected content,
commit messages, PR prose and media separately. Local hooks remain under the
operator's control and Git permits explicit bypasses; they do not establish
server-side enforcement or erase already published history.

Git documents the [hook invocation and pre-push records](https://git-scm.com/docs/githooks),
[index records](https://git-scm.com/docs/git-ls-files),
[raw object reads](https://git-scm.com/docs/git-cat-file), and
[effective identities](https://git-scm.com/docs/git-var).
