#!/usr/bin/env python3
"""Shared exact-turn delivery and rollout recovery support."""

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


BASE = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "jarri-codex-handoff"
DB_PATH = STATE_DIR / "delivery.sqlite3"
LOG_PATH = STATE_DIR / "handoff.log"
LOG_MAX = 1024 * 1024
GRACE = float(os.environ.get("JARRI_HANDOFF_GRACE", "3"))
LEASE = 60
MAX_ATTEMPTS = 8
RETRY_BASE = 30.0
RETRY_MAX = 3600.0
DELIVERY_TIMEOUT = float(os.environ.get("JARRI_HANDOFF_DELIVERY_TIMEOUT", "30"))


def log(event, thread="-", turn="-", source="-"):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size >= LOG_MAX:
            backup = LOG_PATH.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            LOG_PATH.replace(backup)
        with LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} pid={os.getpid()} event={event} thread_id={thread} turn_id={turn} source={source}\n")
    except OSError:
        pass


def connect():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    db.execute("PRAGMA busy_timeout=15000")

    # Concurrent first-time startup can race while enabling WAL.
    # Retry only SQLite locking errors, within a bounded deadline.
    deadline = time.monotonic() + 15
    while True:
        try:
            db.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode not in (
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            ):
                db.close()
                raise
            if time.monotonic() >= deadline:
                db.close()
                raise
            time.sleep(0.025)
    db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
        thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
        observed_at REAL NOT NULL, delivered_at REAL,
        delivery_source TEXT, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, claim_until REAL,
        last_error TEXT, PRIMARY KEY(thread_id, turn_id))""")
    db.execute("CREATE INDEX IF NOT EXISTS deliveries_observed ON deliveries(observed_at)")
    # Serialize additive migrations across simultaneous first connections.
    db.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in db.execute("PRAGMA table_info(deliveries)")}
    if "next_attempt_at" not in columns:
        db.execute("ALTER TABLE deliveries ADD COLUMN next_attempt_at REAL")
    if "failure_kind" not in columns:
        db.execute("ALTER TABLE deliveries ADD COLUMN failure_kind TEXT")
    db.execute("COMMIT")
    db.execute("""CREATE TABLE IF NOT EXISTS metadata (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    now = time.time()
    pruned = db.execute("SELECT value FROM metadata WHERE key='last_retention_prune'").fetchone()
    if not pruned or now - float(pruned[0]) >= 86400:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM deliveries WHERE observed_at < ?", (now - 180 * 86400,))
        db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('last_retention_prune',?)", (str(now),))
        db.execute("COMMIT")
    return db


def observe(thread, turn, source="recovery"):
    db = connect()
    try:
        db.execute("INSERT OR IGNORE INTO deliveries(thread_id,turn_id,observed_at,status) VALUES(?,?,?,'observed')", (thread, turn, time.time()))
        row = db.execute("SELECT status FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
    finally:
        db.close()
    log("completion_observed", thread, turn, source)
    return row[0]


def retry_delay(attempt):
    return min(RETRY_BASE * (2 ** max(0, attempt - 1)), RETRY_MAX)


def deliver(thread, turn, repo, source, manual=False):
    db = connect()
    now = time.time()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO deliveries(thread_id,turn_id,observed_at,status) VALUES(?,?,?,'observed')", (thread, turn, now))
        row = db.execute("SELECT status,claim_until,attempts,next_attempt_at,failure_kind FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
        if row[0] == "delivered":
            db.execute("COMMIT")
            log("duplicate_noop", thread, turn, source)
            return 0
        if row[0] == "claiming" and row[1] and row[1] > now:
            db.execute("COMMIT")
            log("duplicate_noop", thread, turn, source)
            return 0
        # Duplicate completion notifications are idempotent. Retryable failures
        # are retried only by the durable recovery scheduler or explicit manual
        # recovery, never by repeated hook delivery for the same identity.
        if source == "notify" and row[0] in ("failed", "exhausted", "superseded"):
            db.execute("COMMIT")
            log("duplicate_noop", thread, turn, source)
            return 0
        if not manual and row[2] >= MAX_ATTEMPTS and source == "recovery":
            db.execute("UPDATE deliveries SET status='exhausted' WHERE thread_id=? AND turn_id=?", (thread, turn))
            db.execute("COMMIT")
            log("retry_exhausted", thread, turn, source)
            return 1
        if not manual and row[4] == "permanent" and row[0] in ("failed", "exhausted"):
            db.execute("COMMIT")
            return 1
        if not manual and source == "recovery" and row[3] and row[3] > now:
            db.execute("COMMIT")
            return 0
        # Serialize the clipboard side effect globally and discard an older
        # recovery attempt if a later turn has already won the clipboard.
        if source == "recovery" and not manual:
            this = db.execute("SELECT observed_at FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
            latest = db.execute("SELECT MAX(observed_at) FROM deliveries WHERE status='delivered'").fetchone()[0]
            if latest is not None and this[0] < latest:
                db.execute("UPDATE deliveries SET status='superseded',last_error='newer turn already delivered' WHERE thread_id=? AND turn_id=?", (thread, turn))
                db.execute("COMMIT")
                log("recovery_superseded", thread, turn, source)
                return 0
        db.execute("UPDATE deliveries SET status='claiming',claim_until=?,attempts=attempts+1,delivery_source=?,last_error=NULL,failure_kind=NULL WHERE thread_id=? AND turn_id=?", (now + LEASE, source, thread, turn))
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()

    log(f"{source}_delivery_claimed", thread, turn, source)
    clip_lock = None
    try:
        valid = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True).stdout.strip()
        if not valid:
            raise RuntimeError("unresolved repository")
        clip_lock = (STATE_DIR / "clipboard.lock").open("a+")
        fcntl.flock(clip_lock, fcntl.LOCK_EX)
        if source == "recovery" and not manual:
            check = connect()
            this_seen = check.execute("SELECT observed_at FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
            newer = check.execute("SELECT 1 FROM deliveries WHERE status='delivered' AND observed_at>? LIMIT 1", (this_seen[0],)).fetchone()
            check.close()
            if newer:
                status, error, kind = "superseded", "newer turn delivered before recovery copy", None
                db = connect()
                db.execute("UPDATE deliveries SET status='superseded',claim_until=NULL,last_error=? WHERE thread_id=? AND turn_id=?", (error, thread, turn))
                db.close()
                log("recovery_superseded", thread, turn, source)
                return 0
        result = subprocess.run([str(BASE / "codex-handoff-copy"), "--thread", thread, "--turn", turn], cwd=valid, timeout=DELIVERY_TIMEOUT, capture_output=True, text=True)
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.returncode:
            if result.stderr:
                sys.stderr.write(result.stderr)
            message = result.stderr or f"codex-handoff-copy exit {result.returncode}"
            if "No canonical Codex session" in message:
                log("unresolved_session", thread, turn, source)
            elif "not found in session" in message:
                log("unresolved_turn", thread, turn, source)
            raise RuntimeError(f"codex-handoff-copy exit {result.returncode}: {message.strip()}")
        status, error, kind = "delivered", None, None
    except Exception as exc:
        status, error = "failed", str(exc)[:240]
        message = str(exc).lower()
        kind = "permanent" if any(token in message for token in ("external catcopy command not found", "not found in path", "command not found", "not installed", "no canonical codex session", "not found in session", "does not belong to repository", "requires a git repository")) else "transient"
    finally:
        if clip_lock is not None:
            fcntl.flock(clip_lock, fcntl.LOCK_UN)
            clip_lock.close()
    db = connect()
    try:
        if status == "delivered":
            db.execute("UPDATE deliveries SET status='delivered',delivered_at=?,delivery_source=?,claim_until=NULL,last_error=NULL WHERE thread_id=? AND turn_id=?", (time.time(), source, thread, turn))
            log("delivery_success", thread, turn, source)
            return 0
        row = db.execute("SELECT attempts FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
        attempts = row[0] if row else MAX_ATTEMPTS
        exhausted = attempts >= MAX_ATTEMPTS
        db.execute("UPDATE deliveries SET status=?,claim_until=NULL,last_error=?,failure_kind=?,next_attempt_at=? WHERE thread_id=? AND turn_id=?", ("exhausted" if exhausted else "failed", error, kind, None if exhausted or kind == "permanent" else time.time() + retry_delay(attempts), thread, turn))
        log("delivery_failure", thread, turn, source)
        return 1
    finally:
        db.close()


def read_meta(path):
    try:
        with path.open("r", encoding="utf-8") as f:
            first = json.loads(f.readline())
        p = first.get("payload", {})
        return p.get("id") or p.get("session_id"), p.get("cwd")
    except (OSError, ValueError, AttributeError):
        return None, None


def complete_rows(path, offset=0, horizon=None):
    """Yield completion identities and complete-line offsets; ignore partial tails."""
    with path.open("rb") as f:
        f.seek(offset)
        while True:
            line = f.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break
            end = f.tell()
            try:
                item = json.loads(line)
                payload = item.get("payload", {})
                ts = item.get("timestamp")
                turn = None
                if (item.get("type") == "event_msg" and isinstance(payload, dict)
                        and payload.get("type") == "task_complete"):
                    if horizon is None or (ts and __import__("datetime").datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() >= horizon):
                        turn = payload.get("turn_id")
            except (ValueError, TypeError, AttributeError):
                turn = None
            yield turn, end
            offset = end
        return


def repo_root(cwd):
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=True).stdout.strip()
    except Exception:
        return ""


def watcher(args):
    root = Path(args.sessions).expanduser()
    horizon = args.horizon
    startup = True
    offsets = {}
    pending = {}
    pending_repos = {}
    lock_file = None
    if not args.once:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lock_file = (STATE_DIR / "watcher.lock").open("a+")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("observer_already_running", source="recovery")
            return 0
    log("observer_restart", source="recovery")

    def scan_startup():
        nonlocal startup
        if not root.exists():
            return
        for path in sorted(root.rglob("rollout-*.jsonl")):
            try:
                st = path.stat()
                cutoff = time.time() - horizon
                if st.st_mtime >= cutoff:
                    # Recover only very recent completions that have never
                    # been recorded. Existing failed rows remain manual-only.
                    process(path, 0, cutoff)
                else:
                    offsets[str(path)] = (st.st_ino, st.st_size, st.st_mtime_ns)
            except OSError:
                continue
        startup = False

    def process(path, offset, cutoff=None):
        try:
            initial_stat = path.stat()
        except OSError:
            return offset
        thread, cwd = read_meta(path)
        actual_repo = repo_root(cwd) if cwd else ""
        if not thread or not cwd or not actual_repo:
            log("unresolved_session", thread or "-", "-", "recovery")
            offsets[str(path)] = (initial_stat.st_ino, initial_stat.st_size, initial_stat.st_mtime_ns)
            return offset
        if args.repo and Path(actual_repo).resolve() != Path(repo_root(args.repo) or args.repo).resolve():
            log("unresolved_session", thread, "-", "recovery")
            offsets[str(path)] = (initial_stat.st_ino, initial_stat.st_size, initial_stat.st_mtime_ns)
            return offset
        newest = offset
        try:
            st = path.stat()
            key = str(path)
            old = offsets.get(key)
            if old and (st.st_ino != old[0] or st.st_size < old[1]):
                log("rollout_replaced_or_truncated", thread, "-", "recovery")
                offset = 0
            for turn, end in complete_rows(path, offset, cutoff):
                newest = end
                if turn:
                    prior = observe(thread, turn)
                    if prior == "observed" or (not startup and prior == "failed"):
                        pending.setdefault((thread, turn), time.monotonic() + args.grace)
                        pending_repos[(thread, turn)] = actual_repo
                        log("grace_scheduled", thread, turn, "recovery")
            offsets[key] = (st.st_ino, newest, st.st_mtime_ns)
            return offsets[key][1]
        except OSError:
            return offset

    if args.once:
        scan_startup()
        due = time.monotonic() + args.grace
        while pending and time.monotonic() < due:
            time.sleep(min(0.05, due - time.monotonic()))
        for (thread, turn), _ in list(pending.items()):
            db = connect()
            row = db.execute("SELECT status FROM deliveries WHERE thread_id=? AND turn_id=?", (thread, turn)).fetchone()
            db.close()
            if row and row[0] in ("observed", "failed"):
                deliver(thread, turn, pending_repos[(thread, turn)], "recovery")
        return 0

    while True:
        if startup:
            scan_startup()
        else:
            try:
                paths = sorted(root.rglob("rollout-*.jsonl"))
            except OSError:
                paths = []
            present = {str(p) for p in paths}
            for path in paths:
                key = str(path)
                try:
                    stat = path.stat()
                    old = offsets.get(key)
                    if old is None or stat.st_ino != old[0] or stat.st_size != old[1] or stat.st_mtime_ns != old[2]:
                        fresh = old is None or stat.st_ino != old[0] or stat.st_size < old[1]
                        replaced = old is not None and (stat.st_ino != old[0] or stat.st_size < old[1])
                        process(path, 0 if fresh else old[1], time.time() - horizon if old is None or replaced else None)
                except OSError:
                    pass
            for key in list(offsets):
                if key not in present:
                    del offsets[key]
        now = time.monotonic()
        for identity, due in list(pending.items()):
            if now >= due:
                thread, turn = identity
                db = connect()
                row = db.execute("SELECT status,next_attempt_at,failure_kind,attempts FROM deliveries WHERE thread_id=? AND turn_id=?", identity).fetchone()
                db.close()
                if row and row[0] in ("exhausted", "superseded") or (row and row[2] == "permanent"):
                    pending.pop(identity, None)
                    pending_repos.pop(identity, None)
                    continue
                if row and row[0] in ("observed", "failed") and (not row[1] or row[1] <= time.time()):
                    deliver(thread, turn, args.repo or pending_repos.get(identity) or repo_for(root, thread), "recovery")
                elif row and row[0] == "delivered":
                    pending.pop(identity, None)
                    pending_repos.pop(identity, None)
                    continue
                db = connect()
                next_row = db.execute("SELECT status,next_attempt_at,failure_kind,attempts FROM deliveries WHERE thread_id=? AND turn_id=?", identity).fetchone()
                db.close()
                if not next_row or next_row[0] in ("delivered", "exhausted", "superseded") or next_row[2] == "permanent" or next_row[3] >= MAX_ATTEMPTS:
                    pending.pop(identity, None)
                    pending_repos.pop(identity, None)
                else:
                    pending[identity] = max(now + 1, now + (next_row[1] - time.time() if next_row[1] else RETRY_BASE))
        time.sleep(args.poll)


def repo_for(root, thread):
    for path in root.rglob("rollout-*.jsonl"):
        sid, cwd = read_meta(path)
        if sid == thread and cwd:
            return repo_root(cwd)
    return ""


def main():
    p = argparse.ArgumentParser()
    subs = p.add_subparsers(dest="command", required=True)
    d = subs.add_parser("deliver")
    d.add_argument("thread"); d.add_argument("turn"); d.add_argument("repo"); d.add_argument("source", choices=("notify", "recovery"))
    m = subs.add_parser("recover", help="explicitly retry one historical failed turn")
    m.add_argument("thread"); m.add_argument("turn"); m.add_argument("repo")
    w = subs.add_parser("watch")
    w.add_argument("--sessions", default=str(Path.home() / ".codex/sessions"))
    w.add_argument("--repo")
    w.add_argument("--once", action="store_true")
    w.add_argument("--status", action="store_true")
    w.add_argument("--poll", type=float, default=1.0)
    w.add_argument("--retry", type=float, default=5.0)
    w.add_argument("--grace", type=float, default=GRACE)
    w.add_argument("--horizon", type=float, default=600)
    args = p.parse_args()
    if args.command == "deliver":
        return deliver(args.thread, args.turn, args.repo, args.source)
    if args.command == "recover":
        return deliver(args.thread, args.turn, args.repo, "recovery", manual=True)
    if args.status:
        db = connect()
        for row in db.execute("SELECT status,COUNT(*) FROM deliveries GROUP BY status"):
            print(f"{row[0]} {row[1]}")
        db.close()
        return 0
    return watcher(args)


if __name__ == "__main__":
    raise SystemExit(main())
