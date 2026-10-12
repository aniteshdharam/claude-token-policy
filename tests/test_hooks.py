"""Tests for the token-policy hooks. Standard library only.

Each case runs a hook as a subprocess with sample hook-input JSON on stdin, in a fresh
temporary HOME and TMPDIR, and checks the exit code and the output shape.
Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")


class HookCase(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tp-test-")
        self.home = os.path.join(self.base, "home")
        self.tmp = os.path.join(self.base, "tmp")
        os.makedirs(self.home)
        os.makedirs(self.tmp)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.counter = 0

    def run_hook(self, script, payload, env=None):
        e = {k: v for k, v in os.environ.items()
             if not k.startswith("TOKEN_POLICY_") and k not in ("CLAUDE_PROJECT_DIR", "TMPDIR", "HOME")}
        e.update({"HOME": self.home, "TMPDIR": self.tmp})
        e.update(env or {})
        p = subprocess.run([sys.executable, os.path.join(HOOKS, script)], input=json.dumps(payload),
                           capture_output=True, text=True, env=e, timeout=30)
        return p

    def out(self, p):
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)["hookSpecificOutput"] if p.stdout.strip() else None

    def transcript(self, model="opus", ctx=1000, minutes_ago=0, name="t.jsonl"):
        ts = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
        rec = {"type": "assistant", "timestamp": ts,
               "message": {"model": model, "content": [{"type": "text", "text": "ok"}],
                           "usage": {"input_tokens": 10, "cache_read_input_tokens": ctx,
                                     "cache_creation_input_tokens": 0}}}
        path = os.path.join(self.base, name)
        with open(path, "w") as f:
            f.write(json.dumps(rec) + "\n")
        return path

    def uid(self):
        self.counter += 1
        return "tu-%d" % self.counter


class DispatchGuard(HookCase):
    def dispatch(self, tool_input, transcript=None, env=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_use_id": self.uid(),
                   "session_id": "s1", "tool_input": tool_input,
                   "transcript_path": transcript or self.transcript()}
        return self.run_hook("dispatch_guard.py", payload, env)

    def test_named_mid_tier_subagent_is_allowed(self):
        p = self.dispatch({"subagent_type": "builder", "model": "sonnet", "prompt": "do it"})
        self.assertIsNone(self.out(p))

    def test_explore_without_model_gets_fast_model(self):
        o = self.out(self.dispatch({"subagent_type": "Explore", "prompt": "find x"}))
        self.assertEqual(o["updatedInput"]["model"], "sonnet")
        self.assertIn("additionalContext", o)

    def test_generic_without_model_gets_default(self):
        o = self.out(self.dispatch({"subagent_type": "general-purpose", "prompt": "think"}))
        self.assertEqual(o["updatedInput"]["model"], "opus")

    def test_top_tier_subagent_is_refused(self):
        o = self.out(self.dispatch({"subagent_type": "general-purpose", "model": "fable", "prompt": "x"}))
        self.assertEqual(o["permissionDecision"], "deny")
        self.assertIn("Token policy", o["permissionDecisionReason"])

    def test_warn_mode_turns_refusal_into_warning(self):
        o = self.out(self.dispatch({"subagent_type": "general-purpose", "model": "fable", "prompt": "x"},
                                   env={"TOKEN_POLICY_MODE": "warn"}))
        self.assertNotIn("permissionDecision", o)
        self.assertIn("warn mode", o["additionalContext"])

    def test_top_tier_name_is_configurable(self):
        o = self.out(self.dispatch({"subagent_type": "x", "model": "opus", "prompt": "x"},
                                   env={"TOKEN_POLICY_TOP_TIER": "opus"}))
        self.assertEqual(o["permissionDecision"], "deny")

    def test_fork_from_top_tier_parent_is_refused(self):
        t = self.transcript(model="claude-fable-9", ctx=20000)
        o = self.out(self.dispatch({"subagent_type": "fork", "prompt": "x"}, transcript=t))
        self.assertEqual(o["permissionDecision"], "deny")

    def test_fork_above_limit_is_refused(self):
        t = self.transcript(model="opus", ctx=150000)
        o = self.out(self.dispatch({"subagent_type": "fork", "prompt": "x"}, transcript=t))
        self.assertEqual(o["permissionDecision"], "deny")
        self.assertIn("150k", o["permissionDecisionReason"])

    def test_small_fork_is_allowed_with_a_note(self):
        t = self.transcript(model="opus", ctx=40000)
        o = self.out(self.dispatch({"subagent_type": "fork", "prompt": "x"}, transcript=t))
        self.assertNotIn("permissionDecision", o)
        self.assertIn("fork allowed", o["additionalContext"])

    def test_fork_limit_is_configurable(self):
        t = self.transcript(model="opus", ctx=40000)
        o = self.out(self.dispatch({"subagent_type": "fork", "prompt": "x"}, transcript=t,
                                   env={"TOKEN_POLICY_FORK_MAX_TOKENS": "30000"}))
        self.assertEqual(o["permissionDecision"], "deny")

    def test_garbage_input_fails_open(self):
        p = subprocess.run([sys.executable, os.path.join(HOOKS, "dispatch_guard.py")], input="not json",
                           capture_output=True, text=True, env={"HOME": self.home, "TMPDIR": self.tmp})
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")


class ReadGuard(HookCase):
    def read(self, path, env=None, **extra):
        ti = {"file_path": path}
        ti.update(extra)
        return self.run_hook("read_guard.py", {"tool_name": "Read", "tool_use_id": self.uid(),
                                               "session_id": "s1", "transcript_path": "/x/sess.jsonl",
                                               "tool_input": ti}, env)

    def make(self, size):
        path = os.path.join(self.base, "f%d.txt" % size)
        with open(path, "w") as f:
            f.write("a" * size)
        return path

    def test_first_read_is_quiet_and_second_warns(self):
        path = self.make(100)
        self.assertIsNone(self.out(self.read(path)))
        o = self.out(self.read(path))
        self.assertIn("already read 1x", o["additionalContext"])
        self.assertEqual(o["hookEventName"], "PreToolUse")
        self.assertNotIn("permissionDecision", o)

    def test_ranged_re_read_is_quiet(self):
        path = self.make(100)
        self.out(self.read(path))
        self.assertIsNone(self.out(self.read(path, offset=10, limit=20)))

    def test_large_whole_file_warns_and_ranged_does_not(self):
        path = self.make(70000)
        self.assertIn("grep first", self.out(self.read(path))["additionalContext"])
        path2 = self.make(80000)
        self.assertIsNone(self.out(self.read(path2, limit=100)))

    def test_large_file_threshold_is_configurable(self):
        path = self.make(500)
        o = self.out(self.read(path, env={"TOKEN_POLICY_LARGE_FILE_BYTES": "100"}))
        self.assertIn("grep first", o["additionalContext"])


class TurnGuard(HookCase):
    def turn(self, transcript, event="UserPromptSubmit", sid="s1", env=None):
        return self.run_hook("turn_guard.py", {"hook_event_name": event, "session_id": sid,
                                               "prompt": self.uid(), "tool_use_id": self.uid(),
                                               "transcript_path": transcript}, env)

    def test_small_context_is_quiet(self):
        self.assertIsNone(self.out(self.turn(self.transcript(ctx=50000))))

    def test_checkpoint_line_is_a_fraction_of_the_window_and_latched(self):
        # default window 350k -> line at 269k. 280k is past it, 200k is not.
        self.assertIsNone(self.out(self.turn(self.transcript(ctx=200000), sid="a")))
        o = self.out(self.turn(self.transcript(ctx=280000), sid="b"))
        self.assertIn("checkpoint line", o["additionalContext"])
        self.assertIn("269k", o["additionalContext"])
        # PostToolUse stays quiet once the latch is set
        self.assertIsNone(self.out(self.turn(self.transcript(ctx=285000), event="PostToolUse", sid="b")))

    def test_line_follows_the_configured_window(self):
        os.makedirs(os.path.join(self.home, ".claude"))
        with open(os.path.join(self.home, ".claude", "settings.json"), "w") as f:
            json.dump({"autoCompactWindow": "200k"}, f)
        o = self.out(self.turn(self.transcript(ctx=160000)))  # 77% of 200k = 154k
        self.assertIn("154k", o["additionalContext"])

    def test_idle_resume_warns(self):
        o = self.out(self.turn(self.transcript(ctx=160000, minutes_ago=90)))
        self.assertIn("sat idle", o["additionalContext"])

    def test_idle_below_context_floor_is_quiet(self):
        self.assertIsNone(self.out(self.turn(self.transcript(ctx=100000, minutes_ago=90))))

    def test_idle_thresholds_are_configurable(self):
        o = self.out(self.turn(self.transcript(ctx=60000, minutes_ago=20),
                               env={"TOKEN_POLICY_IDLE_MINUTES": "10",
                                    "TOKEN_POLICY_IDLE_RESUME_TOKENS": "50000"}))
        self.assertIn("sat idle", o["additionalContext"])


class Checkpoint(HookCase):
    def precompact(self, sid, transcript):
        return self.run_hook("precompact_checkpoint.py", {"hook_event_name": "PreCompact", "session_id": sid,
                                                          "transcript_path": transcript, "trigger": "auto",
                                                          "cwd": self.base})

    def session_start(self, sid, source="compact", env=None):
        return self.run_hook("session_start_checkpoint.py",
                             {"hook_event_name": "SessionStart", "session_id": sid, "source": source}, env)

    def read_count(self, sid):
        with open(os.path.join(self.home, ".claude", "checkpoints", sid + ".count")) as f:
            return f.read().strip()

    def brief_transcript(self):
        path = os.path.join(self.base, "brief.jsonl")
        recs = [
            {"type": "user", "promptId": "p1", "message": {"role": "user", "content":
             "Build the widget parser in the fenced folder and report counts when done."}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Working on the parser now."},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/work/parser.py"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "python3 -m unittest"}}]}},
        ]
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return path

    def test_checkpoint_round_trip(self):
        t = self.brief_transcript()
        p = self.precompact("sess-a", t)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")  # PreCompact never emits a decision
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "checkpoints", "sess-a.md")))
        o = self.out(self.session_start("sess-a"))
        self.assertEqual(o["hookEventName"], "SessionStart")
        text = o["additionalContext"]
        self.assertIn("Build the widget parser", text)
        self.assertIn("/work/parser.py", text)
        self.assertIn("python3 -m unittest", text)
        self.assertIn("Compaction 1 of this session", text)

    def test_session_start_is_silent_unless_compact(self):
        self.precompact("sess-b", self.brief_transcript())
        for source in ("startup", "resume", "clear"):
            p = self.session_start("sess-b", source=source)
            self.assertEqual(p.returncode, 0)
            self.assertEqual(p.stdout.strip(), "")

    def test_missing_checkpoint_fails_open(self):
        p = self.session_start("never-compacted")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "")

    def test_compaction_counter_warns_from_the_fourth(self):
        t = self.brief_transcript()
        texts = []
        for n in range(1, 5):
            self.precompact("sess-c", t)
            texts.append(self.out(self.session_start("sess-c"))["additionalContext"])
        self.assertIn("Compaction 3 of this session", texts[2])
        self.assertNotIn("compacted 3 times", texts[2])
        self.assertNotIn("fresh session", texts[2])
        self.assertIn("Compaction 4 of this session", texts[3])
        self.assertIn("compacted 4 times", texts[3])
        self.assertIn("fresh session", texts[3])
        self.assertIn("Do not stop in the middle of a task", texts[3])
        self.assertEqual(self.read_count("sess-c"), "4")

    def test_compaction_limit_is_configurable(self):
        t = self.brief_transcript()
        self.precompact("sess-d", t)
        text = self.out(self.session_start("sess-d", env={"TOKEN_POLICY_MAX_COMPACTIONS": "1"}))["additionalContext"]
        self.assertIn("compacted 1 times", text)

    def test_counters_are_per_session(self):
        t = self.brief_transcript()
        self.precompact("sess-e", t)
        self.precompact("sess-e", t)
        self.precompact("sess-f", t)
        self.assertIn("Compaction 1 of this session", self.out(self.session_start("sess-f"))["additionalContext"])

    def test_precompact_counts_even_without_a_transcript(self):
        p = self.precompact("sess-g", "/nonexistent/transcript.jsonl")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.read_count("sess-g"), "1")

    def test_precompact_clears_the_context_latch(self):
        latch_dir = os.path.join(self.tmp, "claude-token-policy")
        os.makedirs(latch_dir)
        latch = os.path.join(latch_dir, "ctx-warned-" + "sess-h")
        open(latch, "w").close()
        self.precompact("sess-h", self.brief_transcript())
        self.assertFalse(os.path.exists(latch))


if __name__ == "__main__":
    unittest.main()
