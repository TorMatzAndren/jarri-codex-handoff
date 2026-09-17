# Jarri Codex Handoff

A small Linux utility for turning Codex CLI session history into a clean,
repo-scoped handoff transcript suitable for another model or for archival.

## Quick start

~~~bash
codex-shell
~~~

Work normally in Codex.

After each completed Codex turn, the current repo-scoped handoff transcript is
automatically copied to the clipboard.

You can then paste it directly into another model or application without
exiting Codex.

Manual and historical handoff controls remain available:

~~~bash
c thisrepo        # newest Codex session for this repository
c thisrepo 1      # previous session
c thisrepo list   # list matching sessions
~~~

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
- copies the rendered transcript to the clipboard automatically after each
  completed Codex turn
- keeps manual and historical session selection available through `c thisrepo`

`thisrepo` is intentionally strict. It must be run from inside a Git
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

The notification hook reacts to:

~~~text
agent-turn-complete
~~~

and copies the current repo-scoped Codex transcript to the clipboard.

Codex itself remains the upstream Codex executable. Jarri Codex Handoff does
not replace or modify the Codex program.

If no `notify` setting exists, `codex-shell` configures the Jarri handoff hook.

If the Jarri hook is already configured, the operation is idempotent.

If another tool already owns the Codex `notify` setting, that setting is
preserved and automatic Jarri handoff is not enabled. `codex-shell` reports
the conflict instead of silently replacing another integration.

Other Codex configuration is preserved except for the two documented `[tui]`
settings above, which `codex-shell` deliberately ensures.

### `codex-handoff-notify`

Internal turn-complete notification helper used by `codex-shell`.

It:

- accepts the Codex notification JSON payload
- ignores events other than `agent-turn-complete`
- ignores missing or unusable working directories
- ignores working directories outside Git repositories
- invokes the package's `catcopy thisrepo` path

The automatic path calls `catcopy` directly rather than relying on the short
`c` command, so shell aliases or functions named `c` cannot interfere with
turn-complete handoff.

### `codex-handoff-render`

Renders a repo-scoped Codex session to stdout.

~~~bash
codex-handoff-render
codex-handoff-render 1
codex-handoff-render --list
~~~

The renderer is the owner of Git-repository validation and Codex session
selection.

### `catcopy` / `c`

`c` is a thin symlink to `catcopy`.

Normal clipboard use:

~~~bash
c file.txt
cat file.txt | c
~~~

Codex handoff:

~~~bash
c thisrepo
c thisrepo 1
c thisrepo list
~~~

`catcopy` prefers the `codex-handoff-render` executable installed beside it,
then falls back to normal `PATH` resolution.

## Installation

### Debian package

~~~bash
sudo apt install ./jarri-codex-handoff_0.2.5_all.deb
~~~

### Portable tarball

~~~bash
tar -xzf jarri-codex-handoff-0.2.5.tar.gz
cd jarri-codex-handoff-0.2.5
sudo ./install.sh
~~~

User-local install:

~~~bash
PREFIX="$HOME/.local" ./install.sh
~~~

Then start Codex through:

~~~bash
codex-shell
~~~

## Dependencies

Required:

- Python 3
- Bash
- Git

Clipboard backend:

- `xclip`
- `xsel`
- `wl-copy`

Codex itself is not bundled.

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
             `--> agent-turn-complete
                        |
                        v
                codex-handoff-notify
                        |
                        v
                Git repository validation
                        |
                        v
                   catcopy thisrepo
                        |
                        v
                codex-handoff-render
                        |
                        v
              repo-scoped session selection
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

c thisrepo
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
- strict failure of `c thisrepo` outside a Git repository
- clean Codex configuration
- idempotent repeated `codex-shell` configuration
- preservation of an existing foreign `notify` setting
- installed `/usr/bin/codex-shell` launching upstream Codex 0.154.0
- installed `/usr/bin/codex-handoff-notify` handling the completed turn
- installed repo-scoped renderer recovering the real persisted Codex turn
- clipboard backend invocation verified with a controlled `xclip` sink

The real desktop X11 clipboard path was also verified separately.

## Compatibility

Initially developed and tested against:

~~~text
codex-cli 0.154.0
~~~

Codex's persisted JSONL and notification behavior are upstream interfaces and
may evolve.

## License

MIT
