"""Tests for scripts/token_census.py. Standard library only.

Builds a small transcripts folder in a temporary directory and checks the per-session CSV:
a reply written as several lines counts once, a project folder reached through a symlink is
read once, a reply copied into a second transcript counts once, and a subagent's compaction
does not count as its parent session's.
Run from the repo root:  python3 -m unittest discover -s tests -v
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "scripts", "token_census.py")
TS = "2026-10-01T10:00:00Z"


def reply(mid, cache_read, output, block):
    usage = {"input_tokens": 10, "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 100, "output_tokens": output}
    return {"type": "assistant", "timestamp": TS, "message": {"id": mid, "model": "claude-opus-5-5", "usage": usage, "content": [block]}}


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class CensusCase(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="tp-census-")
        self.addCleanup(shutil.rmtree, self.base, True)
        self.root = os.path.join(self.base, "projects")
        real = os.path.join(self.root, "-proj-real")
        thinking = {"type": "thinking", "thinking": "..."}
        tool = {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/x"}}
        # s1: one reply written as two lines (thinking, then a tool call), then a compaction
        write(os.path.join(real, "s1.jsonl"), [
            {"type": "user", "timestamp": TS, "message": {"content": "hello"}},
            reply("m1", 1000, 5, thinking),
            reply("m1", 1000, 7, tool),
            {"type": "summary", "timestamp": TS, "summary": "compacted"},
        ])
        # s1's subagent: one reply, and its own compaction
        write(os.path.join(real, "s1", "subagents", "a1.jsonl"), [
            reply("m2", 500, 3, thinking),
            {"type": "user", "timestamp": TS, "message": {"content": "This session is being continued from a previous conversation"}},
        ])
        # the same folder again, reached through a symlink
        os.symlink(real, os.path.join(self.root, "-proj-alias"))
        # s2: a transcript that carries a copy of m1, then one new reply
        write(os.path.join(self.root, "-proj-copy", "s2.jsonl"), [
            reply("m1", 1000, 7, tool),
            reply("m3", 2000, 4, thinking),
        ])

    def test_each_reply_and_each_file_counts_once(self):
        out = os.path.join(self.base, "sessions.csv")
        r = subprocess.run([sys.executable, CENSUS, "--since", "2026-09-01", "--root", self.root, "--csv", out],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out) as f:
            rows = {row["sid"]: row for row in csv.DictReader(f)}
        self.assertEqual(sorted(rows), ["s1", "s2"])
        s1, s2 = rows["s1"], rows["s2"]
        self.assertEqual(int(s1["calls"]), 2)                    # m1 once, m2 once
        self.assertEqual(int(s1["ctx_sum"]), 1110 + 610)         # input + cache read + cache write
        self.assertEqual(int(s1["out"]), 7 + 3)                  # m1 keeps its largest output figure
        self.assertEqual(int(s1["compactions"]), 1)              # the subagent's compaction is not s1's
        self.assertEqual(int(s2["calls"]), 1)                    # m1 was already counted in s1
        self.assertEqual(int(s2["ctx_sum"]), 2110)


if __name__ == "__main__":
    unittest.main()
