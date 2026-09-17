# Jarri Codex Handoff

A small Linux utility for turning Codex CLI session history into a clean,
repo-scoped handoff transcript suitable for another model or for archival.

## Quick start

~~~bash
codex-shell
# do work in Codex, then exit

c thisrepo
~~~

Useful variants:

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
- copies the rendered transcript to the clipboard via `c thisrepo`

## Commands

### `codex-shell`

Launches the normal upstream Codex CLI and ensures:

~~~toml
[tui]
raw_output_mode = true
alternate_screen = "never"
~~~

Existing Codex configuration is preserved.

These settings affect terminal presentation only. The handoff renderer reads
Codex's persisted JSONL session history directly.

### `codex-handoff-render`

Renders a repo-scoped Codex session to stdout.

~~~bash
codex-handoff-render
codex-handoff-render 1
codex-handoff-render --list
~~~

### `catcopy` / `c`

`c` is a thin symlink to `catcopy`.

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

## Installation

### Debian package

~~~bash
sudo apt install ./jarri-codex-handoff_0.2.3_all.deb
~~~

### Portable tarball

~~~bash
tar -xzf jarri-codex-handoff-0.2.3.tar.gz
cd jarri-codex-handoff-0.2.3
sudo ./install.sh
~~~

User-local install:

~~~bash
PREFIX="$HOME/.local" ./install.sh
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
Codex CLI
    |
    v
~/.codex/sessions/.../rollout-*.jsonl
    |
    v
repo-scoped session selection
    |
    v
semantic transcript rendering
    |
    +--> stdout: codex-handoff-render
    |
    `--> clipboard: c thisrepo
~~~

## Clean-room verification

Version 0.2.3 was tested on a fresh Debian 13 VM with no pre-existing Jarri
tools or Codex configuration.

~~~text
fresh Debian
-> install .deb
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

## Compatibility

Initially developed and clean-room verified against:

~~~text
codex-cli 0.154.0
~~~

Codex's persisted JSONL format is upstream behavior and may evolve.

## License

MIT
