"""Cross-runtime manifest, action, and Markdown frontmatter contracts."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ACTIONS = {
    "init": "wiki-init",
    "compile": "wiki-compile",
    "search": "wiki-search-latest",
    "critique": "wiki-critique",
    "ideate": "wiki-ideate",
    "teach": "wiki-teach",
}
RELATION_FIELDS = {
    "related_papers",
    "seeded_from",
    "related_lectures",
    "topics",
}
WIKILINK = re.compile(r"^\[\[[^\]\r\n]+\]\]$")
FRONTMATTER = re.compile(
    r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)
MARKDOWN_FENCE = re.compile(
    r"```markdown[ \t]*\r?\n(?P<body>.*?)\r?\n```", re.DOTALL
)


def parse_frontmatter(text: str, source: str) -> dict:
    match = FRONTMATTER.match(text)
    if match is None:
        raise AssertionError(f"{source}: missing or unclosed YAML frontmatter")
    try:
        payload = yaml.safe_load(match.group("body"))
    except yaml.YAMLError as error:
        raise AssertionError(f"{source}: invalid YAML frontmatter: {error}") from error
    if not isinstance(payload, dict):
        raise AssertionError(f"{source}: frontmatter must be a YAML mapping")
    return payload


def skill_name(path: Path) -> str:
    payload = parse_frontmatter(path.read_text(encoding="utf-8"), str(path))
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise AssertionError(f"{path}: skill name must be a nonempty string")
    return name


class FrontmatterContractTests(unittest.TestCase):
    def assert_relation_fields(self, payload: dict, source: str) -> list[str]:
        seen = []
        for field in sorted(RELATION_FIELDS.intersection(payload)):
            seen.append(field)
            value = payload[field]
            self.assertIsInstance(value, list, f"{source}: {field} must be a list")
            self.assertTrue(value, f"{source}: {field} must not be empty")
            for item in value:
                self.assertIsInstance(
                    item, str, f"{source}: every {field} item must be a string"
                )
                self.assertRegex(
                    item,
                    WIKILINK,
                    f"{source}: {field} must preserve each complete [[id]] link",
                )
        return seen

    def test_real_markdown_frontmatter_is_yaml(self):
        seen = Counter()
        parsed = 0
        for path in sorted(ROOT.rglob("*.md")):
            if ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                continue
            relative = path.relative_to(ROOT).as_posix()
            payload = parse_frontmatter(text, relative)
            parsed += 1
            seen.update(self.assert_relation_fields(payload, relative))

        self.assertGreater(parsed, 0, "no Markdown frontmatter was exercised")
        self.assertEqual(seen["related_papers"], 1)
        self.assertEqual(seen["seeded_from"], 1)

    def test_template_markdown_frontmatter_is_yaml(self):
        seen = Counter()
        parsed = 0
        templates = (
            ROOT / "templates" / "research" / "WIKI.md.tmpl",
            ROOT / "templates" / "course" / "WIKI.md.tmpl",
        )
        for path in templates:
            text = path.read_text(encoding="utf-8")
            blocks = list(MARKDOWN_FENCE.finditer(text))
            self.assertTrue(blocks, f"{path}: no fenced Markdown schemas found")
            for index, match in enumerate(blocks, start=1):
                body = match.group("body")
                if not body.startswith("---"):
                    continue
                source = f"{path.relative_to(ROOT).as_posix()} fence {index}"
                payload = parse_frontmatter(body, source)
                parsed += 1
                seen.update(self.assert_relation_fields(payload, source))

        self.assertEqual(parsed, 6, "expected all six note-schema frontmatter blocks")
        self.assertEqual(
            seen,
            Counter(
                {
                    "related_papers": 1,
                    "seeded_from": 1,
                    "related_lectures": 1,
                    "topics": 1,
                }
            ),
        )


class ActionParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = yaml.safe_load(
            (ROOT / "docs" / "llm-wiki.protocol.yaml").read_text(encoding="utf-8")
        )

    def test_protocol_and_command_action_sets_match(self):
        entrypoints = self.protocol["entrypoints"]
        self.assertEqual(entrypoints["action_map"], EXPECTED_ACTIONS)
        self.assertEqual(
            entrypoints["codex"]["plugin"], "$paper-wiki:paper-wiki <action>"
        )
        self.assertEqual(
            entrypoints["codex"]["project"], "$paper-wiki-project wiki-<action>"
        )
        protocol_actions = self.protocol["actions"]
        for workflow in EXPECTED_ACTIONS.values():
            self.assertIn(workflow, protocol_actions)
            self.assertTrue(ROOT.joinpath("commands", f"{workflow}.md").is_file())

    def test_manifest_and_skill_names_form_documented_namespaces(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        global_skill = skill_name(ROOT / "skills" / "paper-wiki" / "SKILL.md")
        project_skill = skill_name(
            ROOT / "templates" / "common" / "paper-wiki-project.SKILL.md.tmpl"
        )
        self.assertEqual(manifest["name"], "paper-wiki")
        self.assertEqual(global_skill, "paper-wiki")
        self.assertEqual(project_skill, "paper-wiki-project")
        self.assertEqual(f"${manifest['name']}:{global_skill}", "$paper-wiki:paper-wiki")

    def test_global_and_project_skills_route_all_six_actions(self):
        global_skill = (ROOT / "skills" / "paper-wiki" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        project_skill = (
            ROOT / "templates" / "common" / "paper-wiki-project.SKILL.md.tmpl"
        ).read_text(encoding="utf-8")
        for action, workflow in EXPECTED_ACTIONS.items():
            self.assertRegex(global_skill, rf"(?m)^\| `{re.escape(action)}` \|")
            self.assertIn(f"`{workflow}`", global_skill)
            self.assertIn(f"`.claude/commands/{workflow}.md`", project_skill)

    def test_readmes_document_both_runtime_mappings(self):
        for readme_name in ("README.md", "README.en.md"):
            text = ROOT.joinpath(readme_name).read_text(encoding="utf-8")
            self.assertIn("codex plugin add paper-wiki@paper-wiki", text)
            for action, workflow in EXPECTED_ACTIONS.items():
                self.assertIn(f"`/paper-wiki:{workflow}`", text)
                self.assertIn(f"`$paper-wiki:paper-wiki {action}`", text)
                self.assertIn(f"`$paper-wiki-project {workflow}", text)


if __name__ == "__main__":
    unittest.main()
