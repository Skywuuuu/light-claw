import tempfile
import unittest
from pathlib import Path

from light_claw.runtime._skills import parse_skill_frontmatter, scan_skills_dir


class ParseSkillFrontmatterTest(unittest.TestCase):
    def test_parses_name_and_description(self) -> None:
        text = "---\nname: pdf\ndescription: Handle PDF files\n---\nBody."
        self.assertEqual(parse_skill_frontmatter(text), ("pdf", "Handle PDF files"))

    def test_returns_none_when_no_frontmatter(self) -> None:
        self.assertIsNone(parse_skill_frontmatter("Just a regular file."))

    def test_returns_none_when_name_missing(self) -> None:
        self.assertIsNone(parse_skill_frontmatter("---\ndescription: no name\n---\n"))

    def test_strips_quotes_from_values(self) -> None:
        text = '---\nname: "my-skill"\ndescription: \'A skill\'\n---\n'
        self.assertEqual(parse_skill_frontmatter(text), ("my-skill", "A skill"))

    def test_truncates_long_description(self) -> None:
        text = "---\nname: verbose\ndescription: {}\n---\n".format("x" * 300)
        result = parse_skill_frontmatter(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result[1]), 200)

    def test_empty_description(self) -> None:
        self.assertEqual(
            parse_skill_frontmatter("---\nname: minimal\n---\n"),
            ("minimal", ""),
        )


class ScanSkillsDirTest(unittest.TestCase):
    def test_finds_skills_with_valid_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = Path(tmp) / "skills"
            pdf_dir = skills_dir / "pdf"
            pdf_dir.mkdir(parents=True)
            (pdf_dir / "SKILL.md").write_text(
                "---\nname: pdf\ndescription: PDF tools\n---\n", encoding="utf-8"
            )
            result = scan_skills_dir(skills_dir)
            self.assertEqual(result, [("pdf", "PDF tools")])

    def test_skips_hidden_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = Path(tmp) / "skills"
            hidden = skills_dir / ".hidden"
            hidden.mkdir(parents=True)
            (hidden / "SKILL.md").write_text(
                "---\nname: hidden\ndescription: secret\n---\n", encoding="utf-8"
            )
            self.assertEqual(scan_skills_dir(skills_dir), [])

    def test_skips_dirs_without_skill_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = Path(tmp) / "skills"
            no_skill = skills_dir / "empty"
            no_skill.mkdir(parents=True)
            (no_skill / "README.md").write_text("not a skill", encoding="utf-8")
            self.assertEqual(scan_skills_dir(skills_dir), [])

    def test_returns_empty_for_nonexistent_dir(self) -> None:
        self.assertEqual(scan_skills_dir(Path("/nonexistent")), [])

    def test_returns_sorted_by_dir_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = Path(tmp) / "skills"
            for name in ("zeta", "alpha", "mid"):
                d = skills_dir / name
                d.mkdir(parents=True)
                (d / "SKILL.md").write_text(
                    "---\nname: {}\ndescription: desc\n---\n".format(name),
                    encoding="utf-8",
                )
            result = scan_skills_dir(skills_dir)
            self.assertEqual([r[0] for r in result], ["alpha", "mid", "zeta"])


if __name__ == "__main__":
    unittest.main()
