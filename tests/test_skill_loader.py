"""Unit tests for harness.skills.loader — stdlib unittest only."""

import tempfile
import unittest
from pathlib import Path

from harness.skills.loader import discover_skills, load_skill, parse_frontmatter


class TestParseFrontmatter(unittest.TestCase):
    def test_valid_block(self):
        text = "---\nname: demo\ndescription: A demo skill.\nversion: 1.2.3\n---\n# Body\nhello"
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta, {"name": "demo", "description": "A demo skill.", "version": "1.2.3"})
        self.assertEqual(body, "# Body\nhello")

    def test_no_frontmatter(self):
        text = "# Just markdown\nno block here"
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_unterminated_block_treated_as_none(self):
        text = "---\nname: oops\n# Body without closing fence"
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta, {})
        self.assertEqual(body, text)

    def test_quoted_values_stripped(self):
        meta, _ = parse_frontmatter('---\nname: "quoted"\n---\nbody')
        self.assertEqual(meta["name"], "quoted")

    def test_malformed_lines_skipped(self):
        meta, body = parse_frontmatter("---\nname: ok\nthis line has no colon\n---\nrest")
        self.assertEqual(meta, {"name": "ok"})
        self.assertEqual(body, "rest")


class TestLoadSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_valid_skill(self):
        skill_dir = self.base / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: Does things.\nversion: 0.1.0\n---\n# Hello\n",
            encoding="utf-8",
        )
        skill = load_skill(skill_dir / "SKILL.md")
        self.assertEqual(skill["name"], "my-skill")
        self.assertEqual(skill["description"], "Does things.")
        self.assertEqual(skill["version"], "0.1.0")
        self.assertIn("Hello", skill["body"])

    def test_name_falls_back_to_directory(self):
        skill_dir = self.base / "fallback-name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# No frontmatter\n", encoding="utf-8")
        skill = load_skill(skill_dir / "SKILL.md")
        self.assertEqual(skill["name"], "fallback-name")
        self.assertEqual(skill["description"], "")

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_skill(self.base / "nope" / "SKILL.md"))


class TestDiscoverSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_skill(self, dirname: str, frontmatter: str):
        d = self.base / dirname
        d.mkdir()
        (d / "SKILL.md").write_text(frontmatter, encoding="utf-8")

    def test_discovers_sorted(self):
        self._make_skill("zeta", "---\nname: zeta\ndescription: Z.\n---\nbody")
        self._make_skill("alpha", "---\nname: alpha\ndescription: A.\n---\nbody")
        (self.base / "not-a-skill").mkdir()  # no SKILL.md inside
        skills = discover_skills([self.base])
        self.assertEqual([s["name"] for s in skills], ["alpha", "zeta"])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(discover_skills([self.base / "nope"]), [])

    def test_multiple_dirs(self):
        other = self.base / "other"
        other.mkdir()
        self._make_skill("one", "---\nname: one\ndescription: 1.\n---\nb")
        d = other / "two"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: two\ndescription: 2.\n---\nb", encoding="utf-8")
        skills = discover_skills([self.base, other])
        self.assertEqual([s["name"] for s in skills], ["one", "two"])


if __name__ == "__main__":
    unittest.main()
