"""スキル（.claude/skills）とエージェント（.claude/agents）の点検。

スキルは手順を文章で書くので、スクリプトの引数やファイル名が変わってもずれに気づきにくい。
ここでは、スキルが書いているものが実際にあるか・一覧どうしが揃っているかだけを機械的に確かめる。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from learn.web import jobs  # noqa: E402

SKILLS = ROOT / ".claude" / "skills"
AGENTS = ROOT / ".claude" / "agents"
DOCS = sorted(SKILLS.glob("*/SKILL.md")) + sorted(AGENTS.glob("*.md")) + [ROOT / "CLAUDE.md"]
SCRIPT_CALL_RE = re.compile(r"python3? (scripts/[a-z_]+\.py)([^`\n]*)")
PATH_RE = re.compile(r"`((?:scripts|templates|schema|docs)/[A-Za-z0-9_./-]+)`")


def front_matter(path):
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    meta = {}
    for line in (m.group(1).splitlines() if m else []):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


class SkillsTest(unittest.TestCase):
    def test_front_matter(self):
        for p in sorted(SKILLS.glob("*/SKILL.md")):
            with self.subTest(p.parent.name):
                meta = front_matter(p)
                self.assertEqual(meta.get("name"), p.parent.name, "name はフォルダ名と同じにする")
                self.assertTrue(meta.get("description"))
        for p in sorted(AGENTS.glob("*.md")):
            with self.subTest(p.name):
                meta = front_matter(p)
                self.assertEqual(meta.get("name"), p.stem)
                self.assertTrue(meta.get("description"))

    def test_claude_md_lists_every_skill(self):
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        section = text.split("## スキル", 1)[1].split("\n## ", 1)[0]
        listed = set(re.findall(r"^- `/([a-z][a-z0-9-]*)", section, re.M))
        dirs = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(listed, dirs, "CLAUDE.md の「スキル」の一覧と .claude/skills/ を揃える")

    def test_referenced_paths_exist(self):
        for doc in DOCS:
            for ref in PATH_RE.findall(doc.read_text(encoding="utf-8")):
                if "<" in ref or "*" in ref:
                    continue
                with self.subTest(doc=doc.relative_to(ROOT).as_posix(), ref=ref):
                    self.assertTrue((ROOT / ref).exists(), f"{ref} がない")

    def test_script_flags_exist(self):
        helps = {}
        for doc in DOCS:
            for script, args in SCRIPT_CALL_RE.findall(doc.read_text(encoding="utf-8")):
                with self.subTest(doc=doc.relative_to(ROOT).as_posix(), script=script):
                    self.assertTrue((ROOT / script).is_file(), f"{script} がない")
                    if script not in helps:
                        helps[script] = subprocess.run([sys.executable, str(ROOT / script), "--help"],
                                                       capture_output=True, text=True, cwd=ROOT).stdout
                    for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", args):
                        self.assertTrue(flag in helps[script], f"{script} に {flag} がない")

    def test_web_ui_allows_scripts(self):
        # web UI から `claude -p` で呼ぶスキルは、許可したコマンドしか使えない
        allowed = set(jobs.ALLOWED_TOOLS)
        for doc in DOCS:
            for script, _ in SCRIPT_CALL_RE.findall(doc.read_text(encoding="utf-8")):
                with self.subTest(script=script):
                    self.assertIn(f"Bash(python3 {script}:*)", allowed)


if __name__ == "__main__":
    unittest.main()
