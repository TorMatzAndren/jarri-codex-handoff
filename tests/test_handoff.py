import json
import os
import subprocess
import time
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "bin" / "codex-handoff-render"
COPY = ROOT / "bin" / "codex-handoff-copy"
NOTIFY = ROOT / "bin" / "codex-handoff-notify"
DELIVERY = ROOT / "bin" / "handoff_delivery.py"
WATCH = ROOT / "bin" / "codex-handoff-watch"
SHELL = ROOT / "bin" / "codex-shell"


def record(kind, payload):
    from datetime import datetime, timezone
    return json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}) + "\n"


def make_rollout(home, sid, cwd, turns, name=None):
    path = home / ".codex" / "sessions" / "2026" / f"rollout-{name or sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [record("session_meta", {"id": sid, "cwd": str(cwd), "timestamp": sid})]
    for turn, label in turns:
        rows.extend([
            record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"user {label}"}]}),
            record("response_item", {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": f"comment {label}"}]}),
            record("response_item", {"type": "custom_tool_call", "name": "shell", "input": f"tool {label}"}),
            record("response_item", {"type": "custom_tool_call_output", "output": f"evidence {label}"}),
            record("response_item", {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": f"final {label}"}]}),
            record("event_msg", {"type": "task_complete", "turn_id": turn}),
        ])
    path.write_text("".join(rows), encoding="utf-8")
    return path


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.env = {**os.environ, "HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1"}

    def tearDown(self):
        self.temp.cleanup()

    def render_exact(self, sid, turn):
        return subprocess.run([str(RENDER), "--session-id", sid, "--through-turn", turn], cwd=self.repo, env=self.env, text=True, capture_output=True)

    def test_exact_identity_beats_newer_repo_session(self):
        make_rollout(self.home, "target", self.repo, [("turn-a", "A")], "a")
        newer = make_rollout(self.home, "newer", self.repo, [("turn-z", "NEW")], "z")
        os.utime(newer, (2_000_000_000, 2_000_000_000))
        result = self.render_exact("target", "turn-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final A", result.stdout)
        self.assertNotIn("NEW", result.stdout)

    def test_delayed_turn_is_bounded_and_later_turn_has_history(self):
        make_rollout(self.home, "thread", self.repo, [("turn-a", "A"), ("turn-b", "B")])
        a = self.render_exact("thread", "turn-a")
        b = self.render_exact("thread", "turn-b")
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertIn("user A", a.stdout)
        self.assertIn("comment A", a.stdout)
        self.assertIn("evidence A", a.stdout)
        self.assertIn("final A", a.stdout)
        self.assertNotIn("Turn B", a.stdout)
        self.assertNotIn("user B", a.stdout)
        self.assertIn("user A", b.stdout)
        self.assertIn("final A", b.stdout)
        self.assertIn("final B", b.stdout)

    def test_unknown_thread_and_turn_fail_without_fallback(self):
        make_rollout(self.home, "known", self.repo, [("turn-a", "A")])
        unknown_thread = self.render_exact("internal-only", "turn-a")
        unknown_turn = self.render_exact("known", "absent")
        self.assertNotEqual(unknown_thread.returncode, 0)
        self.assertIn("No canonical", unknown_thread.stderr)
        self.assertNotEqual(unknown_turn.returncode, 0)
        self.assertIn("not found", unknown_turn.stderr)

    def test_repository_mismatch_fails(self):
        other = self.base / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        make_rollout(self.home, "foreign", other, [("turn-a", "A")])
        result = self.render_exact("foreign", "turn-a")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong", result.stderr)

    def test_manual_selection_and_list_stay_repo_relative(self):
        make_rollout(self.home, "old", self.repo, [("old-turn", "OLD")], "old")
        make_rollout(self.home, "new", self.repo, [("new-turn", "NEW")], "new")
        old = subprocess.run([str(RENDER), "1"], cwd=self.repo, env=self.env, text=True, capture_output=True)
        listing = subprocess.run([str(RENDER), "--list"], cwd=self.repo, env=self.env, text=True, capture_output=True)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("OLD", old.stdout)
        self.assertIn("new", listing.stdout)
        self.assertIn("old", listing.stdout)


    def test_copy_does_not_wait_for_clipboard_owner_inherited_pipes(self):
        make_rollout(self.home, "thread", self.repo, [("turn-a", "A")])
        env, clipboard = self.make_clip_env()
        bindir = Path(env["PATH"].split(":", 1)[0])
        backend = bindir / "wl-copy"

        backend.write_text(
            "#!/bin/sh\n"
            f"cat > {clipboard}\n"
            # Simulate an X11-style detached clipboard owner that remains alive
            # after accepting stdin. If it inherits catcopy's stdout/stderr,
            # capture_output=True in the parent will wait for pipe EOF.
            "(sleep 5) &\n"
            "exit 0\n",
            encoding="utf-8",
        )
        backend.chmod(0o755)

        started = time.monotonic()
        result = subprocess.run(
            [str(COPY), "--thread", "thread", "--turn", "turn-a"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=1.0,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 1.0)
        self.assertTrue(clipboard.exists())
        self.assertIn("final A", clipboard.read_text())

    def test_copy_exact_path_uses_external_catcopy(self):
        make_rollout(self.home, "thread", self.repo, [("turn-a", "A"), ("turn-b", "B")])
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        clip = self.base / "clipboard"
        external = bin_dir / "catcopy"
        external.write_text(f"#!/bin/sh\ncat > {clip}\n", encoding="utf-8")
        external.chmod(0o755)
        result = subprocess.run([str(COPY), "--thread", "thread", "--turn", "turn-a"], cwd=self.repo, env={**self.env, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        copied = clip.read_text(encoding="utf-8")
        self.assertIn("PATH: CODEX:thread:thread:turn:turn-a", copied)
        self.assertIn("final A", copied)
        self.assertNotIn("user B", copied)

    def test_notifier_identity_logs_and_malformed_event_is_harmless(self):
        fake_bin = self.base / "notify-bin"
        fake_bin.mkdir()
        called = self.base / "catcopy-called"
        fake_notify = fake_bin / "codex-handoff-notify"
        fake_notify.write_bytes(NOTIFY.read_bytes())
        fake_notify.chmod(0o755)
        fake_copy = fake_bin / "codex-handoff-copy"
        fake_copy.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {called}\n", encoding="utf-8")
        fake_copy.chmod(0o755)
        (fake_bin / "handoff_delivery.py").write_bytes(DELIVERY.read_bytes())
        state = self.base / "state"
        payload = json.dumps({"type": "agent-turn-complete", "cwd": str(self.repo), "thread-id": "thr-exact", "turn-id": "turn-exact", "secret": "do-not-log"})
        result = subprocess.run([str(fake_notify), payload], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(called.read_text().strip(), "--thread thr-exact --turn turn-exact")
        log = (state / "jarri-codex-handoff" / "notify.log").read_text()
        self.assertIn("thread_id=thr-exact", log)
        self.assertIn("turn_id=turn-exact", log)
        self.assertNotIn("do-not-log", log)
        malformed = subprocess.run([str(fake_notify), "not-json"], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(malformed.returncode, 0)
        self.assertEqual(called.read_text().strip(), "--thread thr-exact --turn turn-exact")
        non_event = json.dumps({"type": "file-change", "cwd": str(self.repo), "thread-id": "thr-other", "turn-id": "turn-other"})
        ignored = subprocess.run([str(fake_notify), non_event], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(ignored.returncode, 0)
        self.assertEqual(called.read_text().strip(), "--thread thr-exact --turn turn-exact")
        self.assertIn("reason=non_agent_turn_complete", (state / "jarri-codex-handoff" / "notify.log").read_text())

    def test_internal_noncanonical_thread_cannot_copy_unrelated_rollout(self):
        make_rollout(self.home, "unrelated", self.repo, [("turn-valid", "UNRELATED")])
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        marker = self.base / "clipboard-called"
        backend = bin_dir / "wl-copy"
        backend.write_text(f"#!/bin/sh\ntouch {marker}\ncat >/dev/null\n", encoding="utf-8")
        backend.chmod(0o755)
        payload = json.dumps({"type": "agent-turn-complete", "cwd": str(self.repo), "thread-id": "internal-title-thread", "turn-id": "internal-turn"})
        result = subprocess.run([str(NOTIFY), payload], cwd=self.base, env={**self.env, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WAYLAND_DISPLAY": "test", "XDG_STATE_HOME": str(self.base / "state")}, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        log = (self.base / "state/jarri-codex-handoff/notify.log").read_text()
        self.assertIn("thread_id=internal-title-thread", log)
        self.assertIn("turn_id=internal-turn", log)

    def make_clip_env(self):
        bindir = self.base / "clip-bin"
        bindir.mkdir(exist_ok=True)
        clipboard = self.base / "clipboard"
        catcopy = bindir / "catcopy"
        catcopy.write_text(f"#!/bin/sh\ncat > {clipboard}\nprintf 'x' >> {self.base / 'copies'}\n", encoding="utf-8")
        catcopy.chmod(0o755)
        return {**self.env, "PATH": f"{bindir}:{os.environ['PATH']}", "XDG_STATE_HOME": str(self.base / "state")}, clipboard

    def make_failed_clip_env(self):
        bindir = self.base / "failed-clip-bin"
        bindir.mkdir(exist_ok=True)
        catcopy = bindir / "catcopy"
        catcopy.write_text(
            "#!/bin/sh\n"
            "cat >/dev/null\n"
            "exit 7\n",
            encoding="utf-8",
        )
        catcopy.chmod(0o755)
        return {
            **self.env,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "XDG_STATE_HOME": str(self.base / "state"),
        }

    def invoke_deliver(self, sid, turn, source="notify", repo=None, env=None):
        return subprocess.run(["python3", str(DELIVERY), "deliver", sid, turn, str(repo or self.repo), source], env=env or self.env, text=True, capture_output=True)

    def test_notify_delivery_records_success_and_is_idempotent(self):
        make_rollout(self.home, "once", self.repo, [("t1", "one")])
        env, clipboard = self.make_clip_env()
        first = self.invoke_deliver("once", "t1", env=env)
        second = self.invoke_deliver("once", "t1", "recovery", env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.base / "copies").read_text(), "x")
        import sqlite3
        row = sqlite3.connect(self.base / "state/jarri-codex-handoff/delivery.sqlite3").execute("SELECT status,delivery_source FROM deliveries WHERE thread_id='once' AND turn_id='t1'").fetchone()
        self.assertEqual(row, ("delivered", "notify"))

    def test_duplicate_completion_notifications_do_not_copy_twice(self):
        make_rollout(self.home, "duplicate", self.repo, [("t1", "DUPLICATE")])
        env, _ = self.make_clip_env()
        self.assertEqual(self.invoke_deliver("duplicate", "t1", "notify", env=env).returncode, 0)
        self.assertEqual(self.invoke_deliver("duplicate", "t1", "notify", env=env).returncode, 0)
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_observer_once_recovers_missed_notify(self):
        make_rollout(self.home, "recover", self.repo, [("t1", "recovered")])
        env, clipboard = self.make_clip_env()
        result = subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final recovered", clipboard.read_text())
        import sqlite3
        row = sqlite3.connect(self.base / "state/jarri-codex-handoff/delivery.sqlite3").execute("SELECT delivery_source,status FROM deliveries WHERE thread_id='recover' AND turn_id='t1'").fetchone()
        self.assertEqual(row, ("recovery", "delivered"))

    def test_notify_and_recovery_race_mutates_clipboard_once(self):
        make_rollout(self.home, "race", self.repo, [("t1", "race")])
        env, _ = self.make_clip_env()
        commands = [["python3", str(DELIVERY), "deliver", "race", "t1", str(self.repo), source] for source in ("notify", "recovery")]
        procs = [
            subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        results = [proc.communicate(timeout=30) for proc in procs]
        codes = [proc.returncode for proc in procs]
        self.assertEqual(
            codes,
            [0, 0],
            f"Concurrent delivery failed: {list(zip(('notify', 'recovery'), codes, results))}",
        )
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_failed_delivery_is_retryable(self):
        make_rollout(self.home, "retry", self.repo, [("t1", "retry")])
        env = self.make_failed_clip_env()
        failed = self.invoke_deliver("retry", "t1", env=env)
        self.assertEqual(failed.returncode, 1)
        env, _ = self.make_clip_env()
        retried = self.invoke_deliver("retry", "t1", "recovery", env=env)
        self.assertEqual(retried.returncode, 0, retried.stderr)

    def test_retry_backoff_and_exhaustion_persist_in_registry(self):
        import sqlite3
        make_rollout(self.home, "bounded", self.repo, [("t1", "bounded")])
        env = self.make_failed_clip_env()
        self.assertEqual(self.invoke_deliver("bounded", "t1", env=env).returncode, 1)
        db_path = self.base / "state/jarri-codex-handoff/delivery.sqlite3"
        db = sqlite3.connect(db_path)
        first = db.execute("SELECT attempts,next_attempt_at,failure_kind FROM deliveries WHERE thread_id='bounded'").fetchone()
        self.assertEqual(first[0], 1)
        self.assertGreater(first[1], time.time())
        self.assertEqual(first[2], "transient")
        for expected_attempt, expected_delay in ((2, 60), (3, 120)):
            db.execute("UPDATE deliveries SET next_attempt_at=0 WHERE thread_id='bounded'")
            db.commit()
            self.assertEqual(self.invoke_deliver("bounded", "t1", "recovery", env=env).returncode, 1)
            current = db.execute("SELECT attempts,next_attempt_at FROM deliveries WHERE thread_id='bounded'").fetchone()
            self.assertEqual(current[0], expected_attempt)
            self.assertGreaterEqual(current[1] - time.time(), expected_delay - 2)
        # A restart or eager observer poll cannot bypass its persisted due time.
        success_env, _ = self.make_clip_env()
        self.assertEqual(self.invoke_deliver("bounded", "t1", "recovery", env=success_env).returncode, 0)
        row = db.execute("SELECT attempts,status FROM deliveries WHERE thread_id='bounded'").fetchone()
        self.assertEqual(row, (3, "failed"))
        db.execute("UPDATE deliveries SET attempts=8,next_attempt_at=0 WHERE thread_id='bounded'")
        db.commit(); db.close()
        self.invoke_deliver("bounded", "t1", "recovery", env=success_env)
        db = sqlite3.connect(db_path)
        self.assertEqual(db.execute("SELECT status,attempts FROM deliveries WHERE thread_id='bounded'").fetchone(), ("exhausted", 8))
        db.close()

    def test_startup_does_not_replay_hundreds_of_old_failed_rows(self):
        import sqlite3
        env, _ = self.make_clip_env()
        db_path = self.base / "state/jarri-codex-handoff/delivery.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["python3", str(DELIVERY), "watch", "--status"], env=env, check=True, capture_output=True)
        db = sqlite3.connect(db_path)
        for i in range(300):
            db.execute("INSERT INTO deliveries(thread_id,turn_id,observed_at,status,attempts,last_error) VALUES(?,?,?,?,?,?)", (f"old-{i}", "turn", 1, "failed", 20, "historical failure"))
        db.commit(); db.close()
        make_rollout(self.home, "fresh", self.repo, [("new", "FRESH")])
        result = subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final FRESH", (self.base / "clipboard").read_text())
        db = sqlite3.connect(db_path)
        self.assertEqual(db.execute("SELECT count(*) FROM deliveries WHERE thread_id LIKE 'old-%' AND status='failed'").fetchone()[0], 300)
        db.close()

    def test_missing_external_catcopy_is_permanent(self):
        make_rollout(self.home, "no-catcopy", self.repo, [("t1", "NO_CATCOPY")])
        bindir = self.base / "empty-bin"; bindir.mkdir()
        (bindir / "python3").symlink_to("/usr/bin/python3")
        (bindir / "git").symlink_to("/usr/bin/git")
        env = {**self.env, "PATH": str(bindir), "XDG_STATE_HOME": str(self.base / "state")}
        result = self.invoke_deliver("no-catcopy", "t1", env=env)
        self.assertEqual(result.returncode, 1)
        import sqlite3
        row = sqlite3.connect(self.base / "state/jarri-codex-handoff/delivery.sqlite3").execute("SELECT status,failure_kind,last_error FROM deliveries WHERE thread_id='no-catcopy'").fetchone()
        self.assertEqual(row[:2], ("failed", "permanent"))
        self.assertIn("external catcopy command not found", row[2])

    def test_failed_exact_render_stays_failed_and_inspectable(self):
        make_rollout(self.home, "render-failure", self.repo, [("known", "KNOWN")])
        env, _ = self.make_clip_env()
        result = self.invoke_deliver("render-failure", "absent", env=env)
        self.assertEqual(result.returncode, 1)
        import sqlite3
        row = sqlite3.connect(self.base / "state/jarri-codex-handoff/delivery.sqlite3").execute("SELECT status,last_error FROM deliveries WHERE thread_id='render-failure' AND turn_id='absent'").fetchone()
        self.assertEqual(row[0], "failed")
        self.assertIn("not found", row[1])

    def test_old_sqlite_schema_migrates_without_losing_rows(self):
        import sqlite3
        state = self.base / "state/jarri-codex-handoff"
        state.mkdir(parents=True)
        path = state / "delivery.sqlite3"
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE deliveries(thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,observed_at REAL NOT NULL,delivered_at REAL,delivery_source TEXT,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,claim_until REAL,last_error TEXT,PRIMARY KEY(thread_id,turn_id)); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);")
        db.execute("INSERT INTO deliveries(thread_id,turn_id,observed_at,status,attempts,last_error) VALUES('kept','turn',?,'failed',4,'old reason')", (time.time(),))
        db.commit(); db.close()
        result = subprocess.run(["python3", str(DELIVERY), "watch", "--status"], env={**self.env, "XDG_STATE_HOME": str(self.base / "state")}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("SELECT status,attempts,last_error FROM deliveries WHERE thread_id='kept'").fetchone(), ("failed", 4, "old reason"))
        columns = {row[1] for row in db.execute("PRAGMA table_info(deliveries)")}
        self.assertTrue({"next_attempt_at", "failure_kind"}.issubset(columns))
        db.close()

    def test_delivery_has_no_obsolete_bundled_catcopy_path(self):
        source = DELIVERY.read_text(encoding="utf-8")
        self.assertNotIn("BASE / \"catcopy\"", source)
        self.assertIn("codex-handoff-copy", source)

    def test_delivery_timeout_is_failed_not_success(self):
        make_rollout(self.home, "timeout", self.repo, [("t1", "TIMEOUT")])
        env, _ = self.make_clip_env()
        catcopy = Path(env["PATH"].split(":", 1)[0]) / "catcopy"
        catcopy.write_text("#!/bin/sh\ncat >/dev/null\nsleep 2\n", encoding="utf-8"); catcopy.chmod(0o755)
        env["JARRI_HANDOFF_DELIVERY_TIMEOUT"] = "0.05"
        result = self.invoke_deliver("timeout", "t1", env=env)
        self.assertEqual(result.returncode, 1)
        import sqlite3
        row = sqlite3.connect(self.base / "state/jarri-codex-handoff/delivery.sqlite3").execute("SELECT status,last_error FROM deliveries WHERE thread_id='timeout'").fetchone()
        self.assertEqual(row[0], "failed")
        self.assertIn("timed out", row[1])

    def test_failed_older_recovery_cannot_overwrite_newer_clipboard(self):
        import sqlite3
        make_rollout(self.home, "older", self.repo, [("old", "OLD")])
        make_rollout(self.home, "newer", self.repo, [("new", "NEW")])
        failed_env = self.make_failed_clip_env()
        self.assertEqual(self.invoke_deliver("older", "old", env=failed_env).returncode, 1)
        env, clipboard = self.make_clip_env()
        db_path = self.base / "state/jarri-codex-handoff/delivery.sqlite3"
        db = sqlite3.connect(db_path)
        db.execute("UPDATE deliveries SET status='failed',attempts=1,next_attempt_at=0 WHERE thread_id='older' AND turn_id='old'")
        db.commit(); db.close()
        self.assertEqual(self.invoke_deliver("newer", "new", env=env).returncode, 0)
        newer_clip = clipboard.read_text()
        self.assertIn("final NEW", newer_clip)
        self.assertEqual(self.invoke_deliver("older", "old", "recovery", env=env).returncode, 0)
        self.assertEqual(clipboard.read_text(), newer_clip)

    def test_observer_does_not_recopy_after_restart(self):
        make_rollout(self.home, "restart-delivered", self.repo, [("t1", "one")])
        env, _ = self.make_clip_env()
        self.assertEqual(self.invoke_deliver("restart-delivered", "t1", env=env).returncode, 0)
        subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, check=True, capture_output=True)
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_old_historical_completions_are_outside_startup_horizon(self):
        path = make_rollout(self.home, "historical", self.repo, [("old", "OLD")])
        rows = path.read_text().replace('"timestamp": "', '"timestamp": "2000-01-01T00:00:00+00:00", "ignored": "')
        path.write_text(rows)
        os.utime(path, None)
        env, _ = self.make_clip_env()
        subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, check=True, capture_output=True)
        self.assertFalse((self.base / "copies").exists())

    def test_incremental_second_turn_and_partial_line(self):
        path = make_rollout(self.home, "incremental", self.repo, [("first", "first")])
        env, clipboard = self.make_clip_env()
        # Watch one processes both complete events without requiring any external service.
        with path.open("a") as f:
            f.write(record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "final second"}]}))
            f.write(record("event_msg", {"type": "task_complete", "turn_id": "second"}))
        subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, check=True, capture_output=True)
        self.assertIn("final second", clipboard.read_text())

    def test_internal_and_mismatched_repo_are_ignored_by_observer(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        subprocess.run(["git", "init", "-q", str(foreign)], check=True)
        make_rollout(self.home, "foreign-session", foreign, [("foreign-turn", "FOREIGN")])
        env, _ = self.make_clip_env()
        subprocess.run([str(WATCH), "--once", "--repo", str(self.repo), "--grace", "0"], env=env, check=True, capture_output=True)
        self.assertFalse((self.base / "copies").exists())

    def test_manual_copy_modes_stay_available(self):
        make_rollout(self.home, "manual-a", self.repo, [("t1", "one")], "a")
        newest = make_rollout(self.home, "manual-b", self.repo, [("t2", "two")], "b")
        os.utime(newest, (2_000_000_000, 2_000_000_000))
        env, clipboard = self.make_clip_env()
        newest_result = subprocess.run([str(COPY)], cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertEqual(newest_result.returncode, 0, newest_result.stderr)
        self.assertIn("final two", clipboard.read_text())
        historical = subprocess.run([str(COPY), "1"], cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertEqual(historical.returncode, 0, historical.stderr)
        self.assertIn("final one", clipboard.read_text())
        before_list = clipboard.read_text()
        listing = subprocess.run([str(COPY), "--list"], cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn("manual-a", listing.stdout)
        self.assertIn("manual-b", listing.stdout)
        self.assertEqual(clipboard.read_text(), before_list)

    def test_copy_strictly_requires_git_repository(self):
        outside = self.base / "outside"
        outside.mkdir()
        result = subprocess.run([str(COPY), "--list"], cwd=outside, env=self.env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Git repository", result.stderr)

    def test_install_and_uninstall_preserve_external_command_ownership(self):
        prefix = self.base / "prefix"
        bindir = prefix / "bin"
        bindir.mkdir(parents=True)
        c = bindir / "c"
        catcopy = bindir / "catcopy"
        c.write_text("external c", encoding="utf-8")
        catcopy.write_text("external catcopy", encoding="utf-8")
        env = {**self.env, "PREFIX": str(prefix)}
        installed = subprocess.run([str(ROOT / "install.sh")], cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(c.read_text(), "external c")
        self.assertEqual(catcopy.read_text(), "external catcopy")
        self.assertTrue((bindir / "codex-handoff-copy").is_file())
        removed = subprocess.run([str(ROOT / "uninstall.sh")], cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(c.read_text(), "external c")
        self.assertEqual(catcopy.read_text(), "external catcopy")
        self.assertFalse((bindir / "codex-handoff-copy").exists())

    def test_exact_turn_delivery_excludes_later_turn(self):
        make_rollout(self.home, "bounded", self.repo, [("A", "Turn A"), ("B", "Turn B")])
        env, clipboard = self.make_clip_env()
        result = self.invoke_deliver("bounded", "A", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = clipboard.read_text()
        self.assertIn("final Turn A", text)
        self.assertNotIn("Turn B", text)

    def test_observer_once_discovers_new_rollout_file(self):
        make_rollout(self.home, "new-rollout", self.repo, [("newturn", "NEW_ROLLOUT")])
        env, clipboard = self.make_clip_env()
        result = subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final NEW_ROLLOUT", clipboard.read_text())

    def test_pending_turns_in_one_rollout_deliver_in_event_order(self):
        make_rollout(self.home, "ordered", self.repo, [("first", "FIRST"), ("second", "SECOND")])
        env, clipboard = self.make_clip_env()
        result = subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("final SECOND", clipboard.read_text())
        self.assertEqual((self.base / "copies").read_text(), "xx")

    def test_partial_jsonl_tail_waits_for_completion(self):
        path = make_rollout(self.home, "partial", self.repo, [])
        partial = json.dumps({"timestamp": "2026-10-04T00:00:00+00:00", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "partial-turn"}})
        with path.open("a") as f:
            f.write(partial[:30])
        from importlib.util import spec_from_file_location, module_from_spec
        spec = spec_from_file_location("handoff_delivery", DELIVERY)
        module = module_from_spec(spec); spec.loader.exec_module(module)
        self.assertFalse(any(turn == "partial-turn" for turn, _ in module.complete_rows(path)))
        with path.open("a") as f:
            f.write(partial[30:] + "\n")
        rows = list(module.complete_rows(path))
        self.assertEqual(rows[-1][0], "partial-turn")

    def test_recorded_failure_is_not_replayed_after_restart_but_manual_recovery_works(self):
        make_rollout(self.home, "restart-pending", self.repo, [("t1", "recover-after-restart")])
        failed_env = self.make_failed_clip_env()
        self.assertEqual(self.invoke_deliver("restart-pending", "t1", env=failed_env).returncode, 1)
        env, clipboard = self.make_clip_env()
        result = subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.base / "copies").exists())
        manual = subprocess.run(["python3", str(DELIVERY), "recover", "restart-pending", "t1", str(self.repo)], env=env, text=True, capture_output=True)
        self.assertEqual(manual.returncode, 0, manual.stderr)
        self.assertIn("final recover-after-restart", clipboard.read_text())

    def test_healthy_notify_during_grace_does_not_duplicate(self):
        make_rollout(self.home, "healthy", self.repo, [("t1", "healthy")])
        env, _ = self.make_clip_env()
        watcher = subprocess.Popen([str(WATCH), "--poll", "0.02", "--retry", "0.1", "--grace", "0.3"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import time
            time.sleep(0.08)
            notified = self.invoke_deliver("healthy", "t1", "notify", env=env)
            self.assertEqual(notified.returncode, 0, notified.stderr)
            time.sleep(0.45)
        finally:
            watcher.terminate()
            watcher.wait(timeout=3)
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_running_observer_discovers_appended_completion(self):
        path = make_rollout(self.home, "append-live", self.repo, [])
        env, _ = self.make_clip_env()
        watcher = subprocess.Popen([str(WATCH), "--poll", "0.02", "--retry", "0.1", "--grace", "0"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import time
            time.sleep(0.1)
            with path.open("a") as stream:
                stream.write(record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "final incremental"}]}))
                stream.write(record("event_msg", {"type": "task_complete", "turn_id": "incremental-turn"}))
            deadline = time.monotonic() + 3
            while not (self.base / "copies").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            watcher.terminate()
            watcher.wait(timeout=3)
        self.assertTrue((self.base / "copies").exists())
        self.assertIn("final incremental", (self.base / "clipboard").read_text())

    def test_live_recovery_failure_retries(self):
        path = make_rollout(self.home, "retry-watch", self.repo, [])
        env, _ = self.make_clip_env()
        backend = Path(env["PATH"].split(":", 1)[0]) / "wl-copy"
        backend.write_text("#!/bin/sh\nexit 7\n")
        backend.chmod(0o755)
        watcher = subprocess.Popen([str(WATCH), "--poll", "0.02", "--retry", "0.15", "--grace", "0"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import time
            time.sleep(0.1)
            with path.open("a") as stream:
                stream.write(record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "final retry"}]}))
                stream.write(record("event_msg", {"type": "task_complete", "turn_id": "retry-turn"}))
            time.sleep(0.25)
            backend.write_text(f"#!/bin/sh\ncat > {self.base / 'clipboard'}\nprintf x >> {self.base / 'copies'}\n")
            backend.chmod(0o755)
            deadline = time.monotonic() + 3
            while not (self.base / "copies").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            watcher.terminate()
            watcher.wait(timeout=3)
        self.assertTrue((self.base / "copies").exists())
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_rollout_truncation_resets_incremental_offset(self):
        path = make_rollout(self.home, "replace", self.repo, [("before", "BEFORE")])
        env, clipboard = self.make_clip_env()
        subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, check=True, capture_output=True)
        path.write_text("".join([record("session_meta", {"id": "replace", "cwd": str(self.repo)}),
                                  record("response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "final after"}]}),
                                  record("event_msg", {"type": "task_complete", "turn_id": "after"})]))
        # A fresh one-shot pass safely sees the replacement; prior identity stays delivered.
        subprocess.run([str(WATCH), "--once", "--grace", "0"], env=env, check=True, capture_output=True)
        self.assertIn("final after", clipboard.read_text())

    def test_registry_is_safe_across_processes(self):
        make_rollout(self.home, "concurrent", self.repo, [("t1", "ONE")])
        env, _ = self.make_clip_env()
        commands = [["python3", str(DELIVERY), "deliver", "concurrent", "t1", str(self.repo), s] for s in ("notify", "recovery")]
        procs = [
            subprocess.Popen(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for command in commands
        ]
        results = [proc.communicate(timeout=30) for proc in procs]
        codes = [proc.returncode for proc in procs]
        self.assertEqual(
            sorted(codes),
            [0, 0],
            f"Registry concurrency failure: {list(zip(('notify', 'recovery'), codes, results))}",
        )
        self.assertEqual((self.base / "copies").read_text(), "x")

    def test_delivery_logs_do_not_include_transcript_content(self):
        make_rollout(self.home, "secret-session", self.repo, [("secret-turn", "TRANSCRIPT_SECRET")])
        env, _ = self.make_clip_env()
        self.invoke_deliver("secret-session", "secret-turn", env=env)
        log = (self.base / "state/jarri-codex-handoff/handoff.log").read_text()
        self.assertIn("thread_id=secret-session", log)
        self.assertIn("turn_id=secret-turn", log)
        self.assertNotIn("TRANSCRIPT_SECRET", log)



    def test_codex_shell_explicit_codex_bin_preserves_arguments(self):
        bin_dir = self.base / "shell-bin"
        bin_dir.mkdir()

        launcher = bin_dir / "codex-shell"
        launcher.write_bytes(SHELL.read_bytes())
        launcher.chmod(0o755)

        for name in (
            "codex-handoff-notify",
            "codex-handoff-copy",
            "codex-handoff-render",
            "codex-handoff-watch",
        ):
            helper = bin_dir / name
            helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            helper.chmod(0o755)

        explicit = self.base / "explicit-codex"
        explicit.write_text(
            "#!/bin/sh\n"
            "printf 'EXPLICIT_CODEX\\n'\n"
            "printf 'ARG=<%s>\\n' \"$@\"\n",
            encoding="utf-8",
        )
        explicit.chmod(0o755)

        env = {
            **self.env,
            "CODEX_HOME": str(self.base / "shell-home"),
            "CODEX_BIN": str(explicit),
        }
        result = subprocess.run(
            [str(launcher), "alpha", "two words"],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EXPLICIT_CODEX", result.stdout)
        self.assertIn("ARG=<alpha>", result.stdout)
        self.assertIn("ARG=<two words>", result.stdout)

    def test_codex_shell_missing_explicit_codex_bin_returns_127(self):
        env = {
            **self.env,
            "CODEX_BIN": str(self.base / "does-not-exist"),
        }
        result = subprocess.run(
            [str(SHELL)],
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 127)
        self.assertIn(
            f"Codex CLI was not found: {self.base / 'does-not-exist'}",
            result.stderr,
        )

if __name__ == "__main__":
    unittest.main()
