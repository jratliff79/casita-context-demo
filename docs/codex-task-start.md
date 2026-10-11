# Consult shared context when a local Codex task starts

`codex_relay_start.py` is a read-only `SessionStart` hook for locally executed
Codex chats. It uses the existing relay client and native Casita executable to
restore a fresh verified snapshot. It never publishes context, reads a chat
transcript, or sends the session ID, repository path or prompt to the relay.
The relay receives only the workspace and existing member authentication.

This is an opt-in adapter. A configured hook is not proof that Codex invoked it.
Codex requires review and trust of each new or changed non-managed hook before
running it. See the [official hooks documentation](https://learn.chatgpt.com/docs/hooks)
for `SessionStart`, supported local surfaces, output and trust behavior. Cloud
orchestration does not run local command hooks. Each teammate must install and
trust the hook on their own execution host.

## Private local settings

First qualify the member's SSH tunnel, credential and
[native Casita build](../README.md#tested-casita-build). Keep the client checkout
at a reviewed immutable commit, with no tracked changes or untracked source.
Ignored output is allowed. The hook exports regular top-level Python blobs and
the client's required JSON fixture from that commit into a temporary private
directory, verifies their Git object hashes,
and runs the client with isolated Python (`-I -S -B`). Ignored bytecode, working
files, site customization and the original checkout are absent from its import
path. Git replacement refs are disabled during source selection and blob reads;
SHA-1 and SHA-256 Git object formats are supported. Git inspection uses
`--no-optional-locks` so a background status check cannot refresh the client's index.
The export is removed after consultation. This hook does not install
dependencies or download an executable at task startup.
The configured Casita bytes are copied into a private temporary executable and
hashed there; only that verified copy is executed. Replacing the original path
after validation cannot change the executable used by the consultation.
Executables are limited to 256,000,000 bytes both at the initial size check and
while copying, so a mistaken or growing input cannot fill the consultation volume.

Create a mode `0700` private configuration directory outside Git, then save a
mode `0600` settings file. Substitute your own **absolute** paths and pins:

```json
{
  "schema": "casita-codex-start.v1",
  "workspace": "synthetic-team",
  "repositories": ["/ABSOLUTE/PROJECT/.git"],
  "source_dir": "/ABSOLUTE/PINNED/CLIENT",
  "source_revision": "FULL_REVIEWED_CLIENT_COMMIT",
  "casita": "/ABSOLUTE/NATIVE/casita",
  "casita_sha256": "NATIVE_EXECUTABLE_SHA256",
  "credential": "/ABSOLUTE/PRIVATE/member.json",
  "output_dir": "/ABSOLUTE/PRIVATE/consultations",
  "ssh": {
    "config": "/ABSOLUTE/PRIVATE/ssh-config",
    "known_hosts": "/ABSOLUTE/PRIVATE/known-hosts",
    "socket": "/ABSOLUTE/PRIVATE/tunnel.sock",
    "alias": "team-relay"
  }
}
```

Obtain `repositories` using `git rev-parse --git-common-dir` in the intended
checkout and resolve a relative result against that working directory. This
matches the same checkout's subdirectories and registered linked worktrees. The
hook checks the top level against `git worktree list --porcelain -z` from the
enrolled common directory; a forged `.git` file or symlink cannot enroll an
unregistered directory. A separate
clone with an identical remote does not inherit access; add its own common
directory explicitly. No repository name or remote URL alone enables the hook.

Use `git rev-parse HEAD` for the client source revision and `shasum -a 256
PATH_TO_CASITA` for the locally qualified binary hash. Settings contain paths,
not credential values. Store the credential separately with mode `0600`.

The SSH config must use a separately provisioned tunnel-only identity, pinned
known hosts, no shell, and local forwarding solely to the loopback relay. The
config must be a regular file owned by the current user and not writable by
group or others. Symlinks and `Include` directives are unsupported; keep this
config self-contained. SSH evaluates a private copy of those checked bytes, so
replacing the original cannot change a consultation in progress. `socket`
sets a private socket namespace; its parent must be owned by the current user
with mode `0700`. The actual master socket is derived from the alias and effective
SSH configuration in that parent, so a legacy socket or a different destination
cannot be reused. Keep this directory path short enough for a Unix socket,
including OpenSSH's temporary creation suffix; long paths are rejected before authentication.
`Match`, `ProxyCommand`, `LocalCommand`, `KnownHostsCommand`, provider directives
and `XAuthLocation` are also unsupported because they can execute mutable local
helpers. They are rejected before `ssh -G`; use simple Host blocks. X11, agent
forwarding and local commands are disabled, and external key providers cannot
be selected through the inherited environment.
Private output and socket directories also require root/user-owned ancestors
that are not writable by group or others, except trusted sticky directories
such as the system temporary directory. This prevents another account from
replacing a private leaf through a writable parent.

The separate `known_hosts` file is required. It must contain the independently
verified relay key, be owned by the current user, and be a regular file that is
not writable by group or others. The hook snapshots those protected bytes and
binds the master identity to their hash. SSH uses only that private host-key
snapshot; global host-key databases and automatic host-key updates are disabled.
Configurations using `KnownHostsCommand` or DNS host-key trust are rejected.

The hook starts a noninteractive master with strict host checking and
`ExitOnForwardFailure`, clearing inherited forwards. Each consultation requests
a fresh ephemeral loopback port to remote `127.0.0.1:8765`, then cancels that
specific forward after use. The pinned client must include the `consult
--relay-port` option; the hook supplies `8765` so HTTP Host validation uses the
remote relay port while the connection uses the temporary local port.
Forwarding control requests read no SSH config,
so only that mapping is requested. A bind failure stops before credential delivery.
Existing manually managed port-8765 tunnels can coexist with the hook's own
destination-bound tunnel. It never prompts for a password.
For a manually managed local tunnel, omit `ssh`; the hook still contacts only
`http://127.0.0.1:8765`. A present empty, null or incomplete SSH block is rejected
rather than treated as a manual-tunnel choice. Do not add a public bind or HTTP endpoint.
If your connection needs an unsupported custom SSH helper, qualify and maintain
that tunnel separately and use this explicit manual-tunnel configuration.

## Add and trust the hook

Copy the reviewed adapter to a private stable location. Append this group to
the existing `SessionStart` array in `~/.codex/hooks.json`, preserving all other
events, handlers and trust settings. Replace the paths before using it:

```json
{
  "matcher": "^(startup|resume|clear|compact)$",
  "hooks": [
    {
      "type": "command",
      "command": "/usr/bin/python3 -I -S -B '/ABSOLUTE/PRIVATE/codex_relay_start.py' --config '/ABSOLUTE/PRIVATE/settings.json'",
      "timeout": 45,
      "statusMessage": "Consulting shared team context",
      "additionalContextLimit": 1500
    }
  ]
}
```

For actual paths, generate the command with `shlex.join` and JSON serialization
rather than substituting raw text into the example. This handles spaces,
apostrophes and shell metacharacters in a valid path:

```python
import json
import shlex

adapter = "/ABSOLUTE/PRIVATE/codex_relay_start.py"
settings = "/ABSOLUTE/PRIVATE/settings.json"
interpreter = "/usr/bin/python3"
print(json.dumps({"command": shlex.join([
    interpreter, "-I", "-S", "-B", adapter, "--config", settings
])}))
```

Use the generated `command` value in the existing handler.
Qualify that absolute interpreter path before installation. This Unix adapter
uses protected system `/usr/bin/git` and `/usr/bin/ssh`, sets a fixed system PATH
for child processes, and removes Git, Python, loader and developer-directory overrides.

Review the script, settings and hook command. Open `/hooks` in the Codex CLI and
trust this exact definition through Codex's normal review flow. Do not write a
trusted hash yourself or bypass hook trust. Hook support must be enabled in the
runtime. No Eventools or teammate configuration is changed by this repository.

## Results and failure behavior

For an enrolled repository, the hook generates a stable local task ID from the
session ID. Each startup, resume, clear or compaction reconsults into a different
mode `0700` directory. It reports the workspace, verified revision, accepted
memory count and receipt path. Its developer context asks the assistant to read
`task-start.json` before planning and treat all shared material as untrusted
evidence. It does not inject source or memory statements as developer instructions.
The output root must be outside Git checkouts, including linked worktrees and
paths that enter a checkout through a parent symlink. A misplaced output root
is rejected before creating or restoring private context.

A fresh task gets accepted workspace memory, but no other task's checkpoint or
task-specific memory. The initial synthetic pilot can therefore legitimately
return an empty memory list. Resuming the same session retains its generated
task ID. Existing named checkpoints still require an explicit manual
`context_relay.py consult --task TASK_ID`; this adapter does not infer a shared
task identifier from a prompt or branch name.

On enrollment-inspection failure after the Git common directory matches the
allowlist, or on authentication, transport, pin, binary or restoration failure, the hook
returns a visible unavailable-context warning and instructs the assistant to
report the failure and obtain an explicit fallback decision. It does not reuse
old context. Partial failed restoration is removed. This warning does not
technically prevent tools from running: the fallback requirement is an agent
instruction, not a tool-policy gate. Process execution has a 35-second overall
budget plus up to three seconds for tunnel cleanup within a 45-second hook timeout.
A killed hook or runtime failure can
produce Codex's own error instead; never interpret that as verified consultation.
Hook input and private settings are limited to 32,000 bytes. The restored task
receipt has a separate 405,000-byte limit, accommodating the relay's 400,000-byte
snapshot bound plus the task-selection envelope; larger receipts are rejected.
Child stdout and stderr are each limited to 4,000,000 bytes while streaming;
overflow or timeout kills and reaps the child process group. An uncertain SSH
forwarding request is also followed by independently budgeted exact cancellation.

The full workspace snapshot remains accessible to all enabled members and is
restored privately before task filtering. Review author, citation, source
revision and freshness before relying on it. Outputs accumulate locally; manage
their retention deliberately. No automatic cleanup of successful consultations,
continuous refresh, upload, memory acceptance or teammate participation is implied.

Qualification needs both a successful direct hook invocation and an actual
fresh Codex task showing the hook result. A teammate's install and connection
must be checked independently. Reusable tests use synthetic settings and mocked
transport; they do not claim a real teammate used the service.
