import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDER = ROOT / "bin" / "codex-handoff-render"
CATCOPY = ROOT / "bin" / "catcopy"
NOTIFY = ROOT / "bin" / "codex-handoff-notify"


def record(kind, payload):
    return json.dumps({"type": kind, "payload": payload}) + "\n"


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

    def test_catcopy_exact_path_uses_stub_clipboard(self):
        make_rollout(self.home, "thread", self.repo, [("turn-a", "A"), ("turn-b", "B")])
        bin_dir = self.base / "bin"
        bin_dir.mkdir()
        clip = self.base / "clipboard"
        backend = bin_dir / "wl-copy"
        backend.write_text(f"#!/bin/sh\ncat > {clip}\n", encoding="utf-8")
        backend.chmod(0o755)
        result = subprocess.run([str(CATCOPY), "codex-turn", "thread", "turn-a"], cwd=self.repo, env={**self.env, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WAYLAND_DISPLAY": "test"}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        copied = clip.read_text(encoding="utf-8")
        self.assertIn("final A", copied)
        self.assertNotIn("user B", copied)

    def test_notifier_identity_logs_and_malformed_event_is_harmless(self):
        fake_bin = self.base / "notify-bin"
        fake_bin.mkdir()
        called = self.base / "catcopy-called"
        fake_notify = fake_bin / "codex-handoff-notify"
        fake_notify.write_bytes(NOTIFY.read_bytes())
        fake_notify.chmod(0o755)
        fake_catcopy = fake_bin / "catcopy"
        fake_catcopy.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {called}\n", encoding="utf-8")
        fake_catcopy.chmod(0o755)
        state = self.base / "state"
        payload = json.dumps({"type": "agent-turn-complete", "cwd": str(self.repo), "thread-id": "thr-exact", "turn-id": "turn-exact", "secret": "do-not-log"})
        result = subprocess.run([str(fake_notify), payload], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(called.read_text().strip(), "codex-turn thr-exact turn-exact")
        log = (state / "jarri-codex-handoff" / "notify.log").read_text()
        self.assertIn("thread_id=thr-exact", log)
        self.assertIn("turn_id=turn-exact", log)
        self.assertNotIn("do-not-log", log)
        malformed = subprocess.run([str(fake_notify), "not-json"], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(malformed.returncode, 0)
        self.assertEqual(called.read_text().strip(), "codex-turn thr-exact turn-exact")
        non_event = json.dumps({"type": "file-change", "cwd": str(self.repo), "thread-id": "thr-other", "turn-id": "turn-other"})
        ignored = subprocess.run([str(fake_notify), non_event], cwd=self.base, env={**self.env, "XDG_STATE_HOME": str(state)}, text=True, capture_output=True)
        self.assertEqual(ignored.returncode, 0)
        self.assertEqual(called.read_text().strip(), "codex-turn thr-exact turn-exact")
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


if __name__ == "__main__":
    unittest.main()
