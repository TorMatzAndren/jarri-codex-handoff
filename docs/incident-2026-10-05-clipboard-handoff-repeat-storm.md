Title: Codex Clipboard Handoff Repeat Storm
ID: incident-2026-10-05-clipboard-handoff-repeat-storm
Date: 2026-10-05
Author: Matz
Type: incident
Subsystem: codex-handoff
Updated: 2026-10-22
Revision: 1

---

@role:incident

@subsystem:codex-handoff
@subsystem:clipboard
@subsystem:workspace

@scope:automatic-handoff
@scope:clipboard-delivery
@scope:recovery-watcher
@scope:retry-semantics
@scope:x11-selection-ownership

@entity:/home/dretski/projects/jarri-codex-handoff/bin/codex-handoff-copy
@entity:/home/dretski/projects/jarri-codex-handoff/bin/handoff_delivery.py
@entity:/var/jarri/clipboard-register/clipboard_register.sqlite3
@entity:commit:3735253

@truth:verified
@evidence:runtime
@evidence:sqlite
@evidence:test-suite

# Summary

On 2026-10-05 the Jarri Codex Handoff recovery watcher repeatedly
delivered one completed Codex turn to the clipboard.

The affected delivery reached 1,245 attempts.

Clipboard Register correctly recorded each clipboard mutation, producing
a large sequence of identical CODEX observations. Clipboard History was
therefore displaying evidence of a real repeated side effect rather than
duplicating rows at presentation time.

# Affected Turn

Thread:

    01a109f5-646c-74f0-afa1-d7c7d5c29043

Turn:

    01a109f5-6ce8-7d81-a0ba-47022768dfc9

Clipboard content SHA-256:

    9f00751dde18d2c1c3705bb387d58c857c96682af6be79735ae27b34e091482d

Original Clipboard Register observation preserved:

    #15476

Redundant observations removed:

    1246

The Clipboard Register cleanup count is not identical to the delivery-attempt
count. Cleanup was performed by content hash and therefore also includes any
matching clipboard mutations produced during investigation and reproduction.

# Failure Mechanism

handoff_delivery.py invoked:

    codex-handoff-copy --thread THREAD_ID --turn TURN_ID

using Python subprocess capture_output=True and a 30 second timeout.

catcopy rendered the canonical Codex session correctly and invoked xclip.

On X11, xclip accepted the clipboard payload and forked a long-lived
selection-owner process.

That detached process inherited catcopy's stdout/stderr descriptors.
Because handoff_delivery.py was capturing those descriptors, Python did
not observe EOF even after the foreground catcopy/xclip path had completed.

The clipboard mutation had already succeeded, but the caller eventually
timed out after 30 seconds and recorded the delivery as failed.

The recovery watcher then retried the same side effect.

Observed cycle:

    recovery_delivery_claimed
        -> clipboard mutation succeeds
        -> captured pipe remains open
        -> 30 second timeout
        -> delivery_failure
        -> retry

The cycle repeated approximately every 31 seconds.

# Investigation Evidence

Direct timing of the affected canonical turn showed:

    codex-handoff-render: approximately 0.061 s
    xclip write:          approximately 0.005 s
    catcopy direct:       approximately 0.085 s

The failure reproduced specifically when catcopy was executed beneath a
caller using capture_output=True.

Clipboard Register observations were approximately 189 KB each and shared
the same content hash.

The delivery database eventually recorded:

    status: delivered
    attempts: 1245
    delivery_source: recovery

# Fix

catcopy now launches clipboard providers with:

    stdout=subprocess.DEVNULL
    stderr=subprocess.DEVNULL

The long-lived X11 clipboard selection owner can therefore no longer retain
the captured output pipes of handoff_delivery.py.

Commit:

    3735253 Prevent clipboard owner pipe inheritance

# Verification

A direct reproduction of the formerly failing captured subprocess path
returned successfully in approximately 0.081 seconds.

A regression test was added which models a clipboard provider that accepts
stdin, forks a long-lived descendant, and exits.

The complete Jarri Codex Handoff test suite passed:

    28 tests
    28 passed

git diff --check also passed before commit.

# Cleanup

Only redundant Clipboard Register observation rows for the affected content
hash were removed.

The earliest observation was retained as the historical original.

The shared content object was deliberately retained because the surviving
observation still references it.

A consistent pre-cleanup SQLite backup was written to:

    /tmp/clipboard_register-before-codex-repeat-cleanup-20261005.sqlite3

# Follow-up: stale observer recovery failure (2026-10-22)

The observer launched on October 5 remained alive through October 22. Its
already-loaded Python code referenced the obsolete bundled executable path
`bin/catcopy`, which had since been removed. Editing or installing current
scripts does not update code already loaded in a long-running process.

The current notify configuration remained present and the independent notify
hook continued to be invoked. Logs show a Jarri Chat delivery succeeding at
18:07:38 on October 22 while the old observer was separately failing recovery
deliveries. The stale observer had no bounded retry state: its in-memory
pending map retried every five seconds, while the database only recorded each
failure. Repeated launches also could not replace that process because of its
watcher lock.

The delivery implementation now uses the maintained `codex-handoff-copy`
entry point for both notify and recovery. That command locates the external
Catcopy executable through PATH. SQLite migrations add durable retry metadata
without replacing existing delivery rows. Recovery is capped at eight
attempts with exponential delay from 30 seconds to one hour; permanent backend
or configuration failures stop automatic retry. A watcher startup baselines
existing rollout files and considers only recent, unrecorded completions, so
prior failed records are not automatically replayed to the clipboard. An
operator can inspect and explicitly retry a selected historical failure via
`handoff_delivery.py recover THREAD TURN REPOSITORY`.

Safe restart procedure: stop the old watcher, verify no watcher remains,
confirm `codex-handoff-copy` and `catcopy` resolve through PATH, inspect the
registry and logs, then start the maintained watcher. Do not erase the SQLite
registry to clear retry state. For a historical failure, review its recorded
error and choose one thread and turn for explicit manual recovery. Canonical
Codex session JSONL remains read-only throughout.
