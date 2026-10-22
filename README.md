# Jarri Codex Handoff

A small Linux utility for turning Codex CLI session history into a clean,
repo-scoped handoff transcript suitable for another model or for archival.

## Quick start

Jarri Codex Handoff automatically copies completed Codex CLI turns
to your clipboard, preserving the conversation, commands, and
results needed to continue working with another AI assistant.

It operates on Codex's persisted session records rather than
scraping terminal output.

### Fresh installation from source

Prerequisites: Linux, Python 3, Bash, Git, an installed upstream
Codex CLI, and a working clipboard backend.

Install Catcopy first. It owns clipboard capture and transport:

    git clone https://github.com/TorMatzAndren/catcopy.git
    cd catcopy
    sudo ./install.sh

Then install Jarri Codex Handoff:

    cd ..
    git clone https://github.com/TorMatzAndren/jarri-codex-handoff.git
    cd jarri-codex-handoff
    sudo ./install.sh

Verify command availability:

    command -v codex
    command -v catcopy
    command -v codex-shell
    command -v codex-handoff-copy

All four commands should resolve before continuing.

### Everyday workflow

Enter a Git repository and launch Codex:

    cd ~/projects/my-project
    codex-shell

Work normally in Codex.

When a turn completes, Jarri Codex Handoff copies the transcript
for the exact thread and completed turn to the clipboard.

Paste the result into ChatGPT, another model, a development
discussion, or an archive.

The Codex session stays open.

The launcher also starts a per-user recovery observer. Repeated
launches reuse the observer through a state-directory lock.

### Manual fallback

    codex-handoff-copy
    codex-handoff-copy 1
    codex-handoff-copy --list
    codex-handoff-copy --thread THREAD_ID --turn TURN_ID

The commands above work within a Git repository and select
sessions belonging to that repository.

### Upstream Codex selection

By default, codex-shell resolves the upstream codex executable
through PATH. If necessary, select it explicitly:

    CODEX_BIN=/path/to/codex codex-shell

Jarri Codex Handoff does not install or replace Codex.

### Privacy

Handoff transcripts can contain source code, terminal output,
paths, and other sensitive development information.

Review the clipboard contents before sharing them externally.

## What it does

Codex persists canonical session history as JSONL under:

~~~text
~/.codex/sessions/
~~~

Jarri Codex Handoff reads that persisted history directly instead of trying
to scrape terminal/TUI output.

It:

- finds Codex sessions belonging to the current Git repository
- selects the newest matching session by default
- renders user messages
- renders assistant commentary and final responses
- preserves tool calls and tool outputs
- excludes reasoning records
- supports automatic exact-turn handoff after each completed Codex turn
- keeps manual and historical session selection available through `codex-handoff-copy`

Repo-local copy and listing are intentionally strict. They must be run from inside a Git
repository. Non-Git directories such as `/`, `$HOME`, or `/tmp` are not
treated as implicit repository roots.

## Commands

### `codex-shell`

`codex-shell` is the supported launcher for the integration.

It launches the normal upstream Codex CLI and ensures these terminal
presentation settings:

~~~toml
[tui]
raw_output_mode = true
alternate_screen = "never"
~~~

It also configures Codex's turn-complete notification hook:

~~~text
codex-handoff-notify
~~~

`codex-shell` starts the independent recovery observer in the background. To
start it directly instead, run:

~~~bash
codex-handoff-watch
~~~

`codex-handoff-watch --once` performs a bounded pass for recent, previously
unrecorded completions, and
`codex-handoff-watch --status` shows registry counts. The normal watcher polls
rollouts once per second. Startup recovery considers only task completions from
the previous 10 minutes; registry rows older than 180 days are pruned.

The canonical rollout JSONL under `~/.codex/sessions/` is the completion truth.
The notification hook reacts to:

~~~text
agent-turn-complete
~~~

and is the fast path for copying that exact completed turn. The standalone
watcher is the recovery path for persisted completions that lack a successful
notify delivery. Both paths call the same delivery primitive and SQLite
registry under `$XDG_STATE_HOME/jarri-codex-handoff/delivery.sqlite3`. The
registry prevents duplicate attempts from reaching the clipboard after a
successful delivery and records bounded recovery retry state. Automatic
recovery makes at most 8 attempts, starting at 30 seconds and doubling to a
one-hour maximum. Missing Catcopy, missing commands, and invalid session or
repository configuration are marked permanent and require operator action.
A process crash in
the narrow interval after clipboard mutation and before the registry commit
can still cause another copy after restart; clipboard mutation is not
transactional with SQLite.

Codex itself remains the upstream Codex executable. Jarri Codex Handoff does
not replace or modify the Codex program.

By default, `codex-shell` resolves `codex` from `PATH`. To select a specific
upstream executable explicitly, set `CODEX_BIN`:

~~~bash
CODEX_BIN=/path/to/codex codex-shell
~~~

Jarri Codex Handoff still does not install, replace, or shadow the `codex`
command.

If no `notify` setting exists, `codex-shell` configures the Jarri handoff hook.

If the Jarri hook is already configured, the operation is idempotent.

If another tool already owns the Codex `notify` setting, that setting is
preserved and automatic Jarri handoff is not enabled. `codex-shell` reports
the conflict instead of silently replacing another integration.

Other Codex configuration is preserved except for the two documented `[tui]`
settings above, which `codex-shell` deliberately ensures.

### `codex-handoff-notify`

Internal turn-complete notification helper used by `codex-shell`.

It accepts the Codex notification JSON payload, resolves its exact thread ID
and completed turn ID in canonical persisted session history, validates the
payload working directory as a Git repository, and invokes
the shared delivery primitive for the exact `THREAD_ID` and `TURN_ID`. It
ignores other event types and malformed or unresolved identities.

### `codex-handoff-watch`

Observes persisted `task_complete` records and recovers newly completed turns
after a 3-second grace period. At startup it only considers recent completion
records that are absent from the registry; it never replays recorded historical
failures automatically. Retry count and next-attempt time are stored in the
SQLite registry and survive restart. The retry schedule is capped at 8 attempts
(30 seconds, doubling to a one-hour maximum); exhausted and permanent failures
stay visible for diagnosis and do not block newer turns. `--retry` is retained
as a compatibility option but does not override the durable schedule.

When a scan finds several completions together, it processes rollout paths in
sorted path order and turns in their JSONL event order. Clipboard writes are
serialized, so the last successful turn in that order leaves its cumulative
transcript on the clipboard.

Notify and recovery both invoke the maintained `codex-handoff-copy`, which uses
the installed external `catcopy` executable found through PATH. No bundled
Catcopy executable is used.

To deliberately retry a historical failed turn, inspect its thread and turn
IDs in the registry, then run from the repository:

~~~bash
python3 /usr/local/bin/handoff_delivery.py recover THREAD_ID TURN_ID /path/to/repo
~~~

Use the matching installed helper path if a different installation prefix was
used. This sends the exact selected historical transcript through the normal
renderer and external Catcopy path; it does not alter canonical JSONL records.

### `codex-handoff-render`

Renders a repo-scoped Codex session to stdout.

~~~bash
codex-handoff-render
codex-handoff-render 1
codex-handoff-render --list
codex-handoff-render --session-id THREAD_ID --through-turn TURN_ID
~~~

The renderer owns transcript rendering and exact thread/turn resolution.
The shared delivery primitive validates repository context before invoking it.

### `codex-handoff-copy`

This command renders and copies Codex transcripts:

~~~bash
codex-handoff-copy
codex-handoff-copy 1
codex-handoff-copy --list
codex-handoff-copy --thread THREAD_ID --turn TURN_ID
~~~

Clipboard delivery uses external `catcopy` on `PATH`. `catcopy` is a required
external dependency for copying; this repository does not install or manage it.
The standalone catcopy project owns the commands `c` and `catcopy`.

## Installation

### Current source installation

For the latest implementation, install directly from this
repository:

    git clone https://github.com/TorMatzAndren/jarri-codex-handoff.git
    cd jarri-codex-handoff
    sudo ./install.sh

The default installation prefix is /usr/local.

For a user-local installation:

    PREFIX="$HOME/.local" ./install.sh

Ensure the corresponding bin directory is on PATH.

This installer manages Jarri Codex Handoff commands only.
It does not install upstream Codex or Catcopy.

### Historical release artifacts

The following commands apply specifically to release 0.2.5,
not necessarily the current main branch.

Debian package:

    sudo apt install ./jarri-codex-handoff_0.2.5_all.deb

Portable tarball:

    tar -xzf jarri-codex-handoff-0.2.5.tar.gz
    cd jarri-codex-handoff-0.2.5
    sudo ./install.sh

The current source installation includes changes made after
version 0.2.5. Do not assume that older release artifacts
contain those changes.

## Debian package dependencies

Jarri Codex Handoff uses Catcopy for clipboard delivery.

The Debian package from version 0.3.1 declares:

    Depends: bash, python3, git, catcopy (>= 0.2.0)

Catcopy is distributed separately:

https://github.com/TorMatzAndren/catcopy/releases/tag/v0.2.0

Download both Debian packages into the same directory
and install them together:

    sudo apt install ./catcopy_0.2.0_all.deb ./jarri-codex-handoff_0.3.1_all.deb

APT can then satisfy the Catcopy dependency using the
provided local package.

Installing Jarri Codex Handoff alone will not automatically
download Catcopy from GitHub.

Upstream Codex CLI must also be installed separately.

## Dependencies

Required:

- Python 3
- Bash
- Git

Clipboard backend:

- external standalone `catcopy` command (which selects its clipboard backend)

Codex itself is not bundled.

## Troubleshooting

### Automatic clipboard delivery does not happen

Check that both Codex and Catcopy are available:

    command -v codex
    command -v catcopy
    command -v codex-shell

Make sure a supported clipboard backend is installed and usable
in the active graphical session.

On X11, Catcopy can use xclip or xsel.
On Wayland, it can use wl-copy.

Start Codex with codex-shell, rather than launching the upstream
codex command directly.

### Another notification integration is configured

codex-shell preserves an existing foreign Codex notify setting.

It reports the conflict rather than overwriting another tool's
configuration. Automatic notification-based handoff is not
enabled in that situation.

Resolve the ownership conflict deliberately. Do not discard
other Codex configuration merely to enable this integration.

### A completed turn was missed

Inspect the recovery registry:

    codex-handoff-watch --status

Run a bounded recovery pass for recent completions not yet in the registry:

    codex-handoff-watch --once

Manual transcript retrieval is also available:

    codex-handoff-copy --list
    codex-handoff-copy

The automatic startup recovery horizon is limited to recent
completions. Manual historical selection remains available.

Previously recorded failures are not replayed on startup. Review their
`last_error`, `attempts`, and status in the SQLite registry, fix any backend or
configuration issue, then use the explicit `handoff_delivery.py recover`
command for one selected thread and turn.

### Clipboard updates unexpectedly repeat

Check the recovery registry and diagnostic log.

The delivery registry is stored under:

    ${XDG_STATE_HOME:-$HOME/.local/state}/jarri-codex-handoff/

The primary log is handoff.log; delivery.sqlite3 is the
SQLite registry.

Normal concurrent notification and recovery attempts coordinate
through the registry. Clipboard mutation itself is not atomic
with SQLite. A crash after copying but before recording success
can therefore result in another copy after restart.

### Test the installed implementation

From the repository:

    python3 -m unittest discover -s tests -p 'test_*.py' -v

To compare an installed helper with its source:

    cmp bin/handoff_delivery.py /usr/local/bin/handoff_delivery.py

Adjust that installed path if a different PREFIX was used.

## Architecture

~~~text
codex-shell
    |
    +--> ensures Codex TUI settings
    |
    +--> ensures Jarri notify hook
    |
    `--> upstream Codex CLI
             |
             +--> ~/.codex/sessions/.../rollout-*.jsonl
             |
             +--> persisted task_complete (authoritative completion truth)
             |          |
             |          v
             |   codex-handoff-watch (recovery path)
             |
             `--> agent-turn-complete (fast path)
                        |
                        v
                codex-handoff-notify
                        |
                        v
             shared durable delivery operation
                        |
                        v
                SQLite delivery registry
                        |
                        v
             codex-handoff-copy --thread THREAD_ID --turn TURN_ID
                        |
                        v
            exact session and turn resolution
                        |
                        v
              semantic transcript rendering
                        |
                        v
                     clipboard
~~~

Manual rendering remains available independently:

~~~text
codex-handoff-render
    |
    `--> stdout

codex-handoff-copy
    |
    `--> clipboard
~~~

## Verification

The original package was clean-room tested on a fresh Debian 13 VM with no
pre-existing Jarri tools or Codex configuration.

That test covered:

~~~text
fresh Debian
-> install package
-> install upstream Codex
-> codex-shell
-> real Codex task
-> canonical JSONL session
-> repo-scoped render
~~~

The rendered transcript preserved:

- user request
- assistant commentary
- tool invocation
- tool output
- final answer

Version 0.2.5 additionally adds automatic per-turn clipboard handoff.

Version 0.2.5 was clean-room verified on Debian 13 using the built release
artifacts.

Verification covered:

- upgrade from the packaged 0.2.3 release to 0.2.5
- real `agent-turn-complete` notification delivery
- automatic clipboard update while Codex remains open
- complete current-turn data being present in persisted JSONL when notify fires
- strict failure of `codex-handoff-copy` outside a Git repository
- clean Codex configuration
- idempotent repeated `codex-shell` configuration
- preservation of an existing foreign `notify` setting
- installed `/usr/bin/codex-shell` launching upstream Codex 0.154.0
- installed `/usr/bin/codex-handoff-notify` handling the completed turn
- installed repo-scoped renderer recovering the real persisted Codex turn
- clipboard backend invocation verified with a controlled `xclip` sink

The real desktop X11 clipboard path was also verified separately.

### Current source verification (October 10, 2026)

Source commit fc67815 was verified on the development host
after installing the updated scripts.

Verification included:

- 32 passing automated tests
- 30 consecutive passing full-suite runs (960 test executions)
- concurrent notification and recovery delivery tests
- installed command comparisons against repository sources
- installed shared delivery helper matching repository source
- clean Git history ancestry before publication

A reproducible SQLite initialization race was identified in
concurrent first-time database connections.

The failure occurred while enabling WAL mode and produced
sqlite3.OperationalError: database is locked.

The source implementation now uses a bounded retry for SQLite
locking errors during WAL initialization.

The regression stress runs completed without recurrence.

This verification does not establish a formal exactly-once
guarantee across process crashes, nor does it constitute a
fresh clean-room installation test of a new release package.

## Compatibility

Initially developed and tested against:

~~~text
codex-cli 0.154.0
~~~

Codex's persisted JSONL and notification behavior are upstream interfaces and
may evolve.

## License

MIT
