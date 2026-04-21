# Show Runtime Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/skills` slash command in Feishu that discovers and displays installed skills/plugins for both Claude Code and Codex runtimes, plus configured MCP tools.

**Architecture:** Both Claude Code and Codex share the same SKILL.md format (YAML frontmatter with `name` + `description`) and the same two-tier layout: standalone skills (`~/.claude/skills/` / `~/.codex/skills/`) plus plugin-bundled skills. A shared `_skills.py` module provides the frontmatter parser and directory scanner. Each runtime class implements `list_skills()` using its own config discovery: Claude Code reads `installed_plugins.json` + `settings.json` (JSON); Codex walks `plugins/cache/` and parses `config.toml` (simple string parsing, no TOML dep). The `/skills` handler combines all discovered skills with agent MCP config.

**Tech Stack:** Python stdlib only (json, pathlib, re). No new dependencies. No TOML parser needed — Codex's `config.toml` is parsed with simple string matching for the specific `[plugins."name"]` pattern.

---

### File Structure

| File | Responsibility |
|---|---|
| `src/light_claw/runtime/_skills.py` (new) | Shared SKILL.md frontmatter parser + skill directory scanner |
| `src/light_claw/runtime/claude_code.py` | Add `config_dir` param, `list_skills()` using `installed_plugins.json` + `settings.json` |
| `src/light_claw/runtime/codex_cli.py` | Add `config_dir` param, `list_skills()` scanning `plugins/cache/` + `config.toml` |
| `src/light_claw/runtime/registry.py` | Add `list_skills()` to `CliRuntime` protocol |
| `src/light_claw/commands.py` | Add `/skills` parsing + help text |
| `src/light_claw/chat_commands.py` | Add `/skills` handler with `_render_skills()` + `_read_mcp_section()` |
| `tests/test_skills.py` (new) | Tests for shared parser + scanner |
| `tests/test_claude_code_runtime.py` | Tests for Claude Code skill discovery |
| `tests/test_codex_cli_runtime.py` | Tests for Codex skill discovery |
| `tests/test_commands.py` | Tests for `/skills` parsing |
| `tests/test_chat_commands.py` (new) | Tests for `/skills` handler end-to-end |

---

### Task 1: Add shared skill discovery utilities

**Files:**
- Create: `src/light_claw/runtime/_skills.py`
- Create: `tests/test_skills.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_skills.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_skills.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'light_claw.runtime._skills'`

- [ ] **Step 3: Implement `_skills.py`**

Create `src/light_claw/runtime/_skills.py`:

```python
"""Shared utilities for skill discovery across CLI runtimes."""

from __future__ import annotations

from pathlib import Path


def parse_skill_frontmatter(text: str) -> tuple[str, str] | None:
    """Extract name and description from SKILL.md YAML frontmatter.

    Returns (name, description) or None if the file lacks valid frontmatter.
    Handles the simple ``name:`` / ``description:`` pattern without a YAML library.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    name = ""
    description = ""
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            name = stripped[5:].strip().strip("\"'")
        elif stripped.startswith("description:"):
            description = stripped[12:].strip().strip("\"'")
    if not name:
        return None
    return (name, description[:200] if description else "")


def scan_skills_dir(skills_dir: Path) -> list[tuple[str, str]]:
    """Scan a directory of skill subdirectories for SKILL.md metadata.

    Each child directory that contains a ``SKILL.md`` with valid frontmatter
    contributes one (name, description) entry.  Hidden directories are skipped.
    """
    if not skills_dir.is_dir():
        return []
    skills: list[tuple[str, str]] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            parsed = parse_skill_frontmatter(
                skill_file.read_text(encoding="utf-8")
            )
            if parsed:
                skills.append(parsed)
        except OSError:
            continue
    return skills
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_skills.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/light_claw/runtime/_skills.py tests/test_skills.py
git commit -m "feat: add shared SKILL.md frontmatter parser and directory scanner"
```

---

### Task 2: Add `list_skills()` to `ClaudeCodeRuntime`

**Files:**
- Modify: `src/light_claw/runtime/claude_code.py:40-62` (add `config_dir` param + `list_skills()`)
- Test: `tests/test_claude_code_runtime.py`

Claude Code discovery:
- Standalone skills: `~/.claude/skills/*/SKILL.md`
- Plugin registry: `~/.claude/plugins/installed_plugins.json`
- Enabled state: `~/.claude/settings.json` → `enabledPlugins`
- Plugin skills: `<installPath>/skills/*/SKILL.md`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_claude_code_runtime.py`:

```python
import json
import tempfile
from pathlib import Path

from light_claw.runtime.claude_code import ClaudeCodeRuntime


class ClaudeCodeSkillDiscoveryTest(unittest.TestCase):
    def test_discovers_standalone_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            skill_dir = config_dir / "skills" / "pdf"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pdf\ndescription: PDF tools\n---\n", encoding="utf-8"
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertEqual(result["Standalone skills"], [("pdf", "PDF tools")])

    def test_discovers_enabled_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = config_dir / "plugins" / "cache" / "mp" / "myplugin" / "1.0.0"
            skill_dir = plugin_dir / "skills" / "tdd"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: tdd\ndescription: Test-driven dev\n---\n", encoding="utf-8"
            )
            (config_dir / "plugins").mkdir(exist_ok=True)
            (config_dir / "plugins" / "installed_plugins.json").write_text(
                json.dumps({
                    "version": 2,
                    "plugins": {
                        "myplugin@mp": [
                            {"installPath": str(plugin_dir), "version": "1.0.0"}
                        ]
                    },
                }),
                encoding="utf-8",
            )
            (config_dir / "settings.json").write_text(
                json.dumps({"enabledPlugins": {"myplugin@mp": True}}),
                encoding="utf-8",
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("myplugin (v1.0.0)", result)
            self.assertEqual(result["myplugin (v1.0.0)"], [("tdd", "Test-driven dev")])

    def test_skips_disabled_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = config_dir / "plugins" / "cache" / "mp" / "off" / "1.0"
            skill_dir = plugin_dir / "skills" / "nope"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: nope\ndescription: Disabled\n---\n", encoding="utf-8"
            )
            (config_dir / "plugins").mkdir(exist_ok=True)
            (config_dir / "plugins" / "installed_plugins.json").write_text(
                json.dumps({
                    "version": 2,
                    "plugins": {
                        "off@mp": [{"installPath": str(plugin_dir), "version": "1.0"}]
                    },
                }),
                encoding="utf-8",
            )
            (config_dir / "settings.json").write_text(
                json.dumps({"enabledPlugins": {"off@mp": False}}),
                encoding="utf-8",
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_returns_empty_for_nonexistent_config_dir(self) -> None:
        runtime = ClaudeCodeRuntime(config_dir=Path("/nonexistent"))
        self.assertEqual(runtime.list_skills(), {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_claude_code_runtime.py::ClaudeCodeSkillDiscoveryTest -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'config_dir'`

- [ ] **Step 3: Add `config_dir` parameter and `list_skills()` method**

In `src/light_claw/runtime/claude_code.py`, update `__init__` to accept `config_dir`:

```python
    def __init__(
        self,
        claude_bin: str = "claude",
        default_model: str | None = None,
        permission_mode: str = "bypassPermissions",
        timeout_min_seconds: int = 180,
        timeout_max_seconds: int = 900,
        timeout_per_char_ms: int = 80,
        extra_writable_dirs: list[str] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.default_model = default_model
        self.permission_mode = permission_mode
        self.timeout_min_seconds = timeout_min_seconds
        self.timeout_max_seconds = timeout_max_seconds
        self.timeout_per_char_ms = timeout_per_char_ms
        self.extra_writable_dirs = extra_writable_dirs or []
        self._config_dir = config_dir or Path.home() / ".claude"
```

Add `list_skills()` method to the class:

```python
    def list_skills(self) -> dict[str, list[tuple[str, str]]]:
        """Discover installed and enabled Claude Code skills.

        Returns dict mapping source label to list of (name, description) pairs.
        """
        from ._skills import scan_skills_dir

        result: dict[str, list[tuple[str, str]]] = {}

        # Standalone skills
        standalone = scan_skills_dir(self._config_dir / "skills")
        if standalone:
            result["Standalone skills"] = standalone

        # Plugin-bundled skills via installed_plugins.json
        registry_path = self._config_dir / "plugins" / "installed_plugins.json"
        if not registry_path.is_file():
            return result

        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return result

        enabled: dict[str, bool] = {}
        settings_path = self._config_dir / "settings.json"
        if settings_path.is_file():
            try:
                settings_data = json.loads(
                    settings_path.read_text(encoding="utf-8")
                )
                enabled = settings_data.get("enabledPlugins", {})
            except (OSError, json.JSONDecodeError):
                pass

        for plugin_id, installs in registry.get("plugins", {}).items():
            if not enabled.get(plugin_id, False):
                continue
            for install in installs:
                install_path = Path(install.get("installPath", ""))
                plugin_skills = scan_skills_dir(install_path / "skills")
                if plugin_skills:
                    label = plugin_id.split("@")[0]
                    version = install.get("version", "")
                    if version:
                        label = "{} (v{})".format(label, version)
                    result[label] = plugin_skills

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_claude_code_runtime.py::ClaudeCodeSkillDiscoveryTest -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/light_claw/runtime/claude_code.py tests/test_claude_code_runtime.py
git commit -m "feat: add skill discovery to ClaudeCodeRuntime"
```

---

### Task 3: Add `list_skills()` to `CodexCliRuntime`

**Files:**
- Modify: `src/light_claw/runtime/codex_cli.py:62-88` (add `config_dir` param + `list_skills()`)
- Test: `tests/test_codex_cli_runtime.py`

Codex has TWO sources of skills (same pattern as Claude Code):
- **Standalone skills:** `~/.codex/skills/*/SKILL.md` (e.g., slides, playwright, jupyter-notebook — 19 skills; `.system/` is hidden and auto-skipped)
- **Plugin skills:** `~/.codex/plugins/cache/<marketplace>/<plugin>/<hash>/skills/*/SKILL.md`
- Plugin metadata: `.codex-plugin/plugin.json` (JSON with `name`, `version`)
- Enabled state: `~/.codex/config.toml` → `[plugins."name@marketplace"]` sections

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_codex_cli_runtime.py`:

```python
import json
import tempfile
from pathlib import Path

from light_claw.runtime.codex_cli import CodexCliRuntime


class CodexSkillDiscoveryTest(unittest.TestCase):
    def test_discovers_standalone_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            skill_dir = config_dir / "skills" / "slides"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: slides\ndescription: Create slide decks\n---\n",
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertEqual(
                result["Standalone skills"], [("slides", "Create slide decks")]
            )

    def test_skips_system_skills_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            system_dir = config_dir / "skills" / ".system"
            system_dir.mkdir(parents=True)
            (system_dir / "SKILL.md").write_text(
                "---\nname: imagegen\ndescription: Internal\n---\n",
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_discovers_plugin_skills_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "openai-curated"
                / "github" / "abc123"
            )
            skill_dir = plugin_dir / "skills" / "gh-fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: gh-fix-ci\ndescription: Fix CI failures\n---\n",
                encoding="utf-8",
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "github", "version": "0.1.0"}),
                encoding="utf-8",
            )
            (config_dir / "config.toml").write_text(
                '[plugins."github@openai-curated"]\nenabled = true\n',
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("github (v0.1.0)", result)
            self.assertEqual(
                result["github (v0.1.0)"], [("gh-fix-ci", "Fix CI failures")]
            )

    def test_combines_standalone_and_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            # Standalone skill
            standalone_dir = config_dir / "skills" / "slides"
            standalone_dir.mkdir(parents=True)
            (standalone_dir / "SKILL.md").write_text(
                "---\nname: slides\ndescription: Decks\n---\n", encoding="utf-8"
            )
            # Plugin skill
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "gh" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: fix-ci\ndescription: CI\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "gh", "version": "1.0"}), encoding="utf-8"
            )
            (config_dir / "config.toml").write_text(
                '[plugins."gh@mp"]\nenabled = true\n', encoding="utf-8"
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertIn("gh (v1.0)", result)

    def test_skips_disabled_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "off" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "nope"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: nope\ndescription: Disabled\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "off", "version": "1.0"}), encoding="utf-8"
            )
            (config_dir / "config.toml").write_text(
                '[plugins."off@mp"]\nenabled = false\n', encoding="utf-8"
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_shows_all_plugins_when_no_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "test" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "myskill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: myskill\ndescription: A skill\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "test", "version": "0.1"}), encoding="utf-8"
            )
            # No config.toml — should still show plugins
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("test (v0.1)", result)

    def test_returns_empty_for_nonexistent_config_dir(self) -> None:
        runtime = CodexCliRuntime(config_dir=Path("/nonexistent"))
        self.assertEqual(runtime.list_skills(), {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_codex_cli_runtime.py::CodexSkillDiscoveryTest -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'config_dir'`

- [ ] **Step 3: Add `config_dir` parameter and TOML parser**

In `src/light_claw/runtime/codex_cli.py`, update `__init__` to accept `config_dir`:

```python
    def __init__(
        self,
        codex_bin: str = "codex",
        sandbox: str = "full-auto",
        default_model: str | None = None,
        default_search: bool = False,
        timeout_min_seconds: int = 180,
        timeout_max_seconds: int = 900,
        timeout_per_char_ms: int = 80,
        stall_timeout_seconds: int = 120,
        extra_writable_dirs: list[str] | None = None,
        config_dir: Path | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox
        self.default_model = default_model
        self.extra_writable_dirs = extra_writable_dirs or []
        self.default_search = default_search
        self.timeout_min_seconds = timeout_min_seconds
        self.timeout_max_seconds = timeout_max_seconds
        self.timeout_per_char_ms = timeout_per_char_ms
        self.stall_timeout_seconds = stall_timeout_seconds
        self._config_dir = config_dir or Path.home() / ".codex"
```

Add the TOML parser and `list_skills()` to the class. Add `import re` at the top of the file:

```python
    def list_skills(self) -> dict[str, list[tuple[str, str]]]:
        """Discover installed and enabled Codex skills.

        Returns dict mapping source label to list of (name, description) pairs.
        Scans ~/.codex/skills/ for standalone skills and walks the plugin cache
        directory for plugin-bundled skills. Checks config.toml for enabled state.
        """
        from ._skills import scan_skills_dir

        result: dict[str, list[tuple[str, str]]] = {}

        # Standalone skills in <config_dir>/skills/
        standalone = scan_skills_dir(self._config_dir / "skills")
        if standalone:
            result["Standalone skills"] = standalone

        # Plugin-bundled skills from cache
        cache_dir = self._config_dir / "plugins" / "cache"
        if not cache_dir.is_dir():
            return result

        enabled = self._read_enabled_plugins()

        for marketplace_dir in sorted(cache_dir.iterdir()):
            if not marketplace_dir.is_dir() or marketplace_dir.name.startswith("."):
                continue
            for plugin_name_dir in sorted(marketplace_dir.iterdir()):
                if not plugin_name_dir.is_dir() or plugin_name_dir.name.startswith("."):
                    continue
                for version_dir in sorted(plugin_name_dir.iterdir()):
                    if not version_dir.is_dir() or version_dir.name.startswith("."):
                        continue
                    plugin_id = "{}@{}".format(
                        plugin_name_dir.name, marketplace_dir.name
                    )
                    if enabled is not None and not enabled.get(plugin_id, True):
                        continue

                    metadata = self._read_codex_plugin_json(version_dir)
                    name = metadata.get("name", plugin_name_dir.name)
                    version = metadata.get("version", "")
                    label = name
                    if version:
                        label = "{} (v{})".format(name, version)

                    plugin_skills = scan_skills_dir(version_dir / "skills")
                    if plugin_skills:
                        result[label] = plugin_skills

        return result

    def _read_codex_plugin_json(self, plugin_dir: Path) -> dict:
        """Read .codex-plugin/plugin.json for name and version."""
        meta_path = plugin_dir / ".codex-plugin" / "plugin.json"
        if not meta_path.is_file():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_enabled_plugins(self) -> dict[str, bool] | None:
        """Parse plugin enabled state from config.toml.

        Returns a dict of {plugin_id: enabled} or None if config cannot be read.
        When None, callers should assume all installed plugins are enabled.
        """
        config_path = self._config_dir / "config.toml"
        if not config_path.is_file():
            return None
        try:
            text = config_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return self._parse_toml_enabled_plugins(text)

    @staticmethod
    def _parse_toml_enabled_plugins(text: str) -> dict[str, bool]:
        """Extract [plugins."name"] enabled states from config.toml.

        Simple string parser for the specific TOML pattern used by Codex.
        No TOML library required.
        """
        result: dict[str, bool] = {}
        current_plugin: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            match = re.match(r'^\[plugins\."(.+?)"\]$', stripped)
            if match:
                current_plugin = match.group(1)
                result[current_plugin] = True
                continue
            if stripped.startswith("["):
                current_plugin = None
                continue
            if current_plugin and stripped.startswith("enabled"):
                if "false" in stripped.lower():
                    result[current_plugin] = False
        return result
```

- [ ] **Step 4: Add `import re` at the top of `codex_cli.py`**

Add after the existing imports in `src/light_claw/runtime/codex_cli.py`:

```python
import re
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_codex_cli_runtime.py::CodexSkillDiscoveryTest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/light_claw/runtime/codex_cli.py tests/test_codex_cli_runtime.py
git commit -m "feat: add skill discovery to CodexCliRuntime via plugin cache scanning"
```

---

### Task 4: Add `list_skills()` to protocol + `/skills` command parsing

**Files:**
- Modify: `src/light_claw/runtime/registry.py:15-26`
- Modify: `src/light_claw/commands.py:13-48`
- Test: `tests/test_commands.py`

- [ ] **Step 1: Write the failing tests for command parsing**

Add to `tests/test_commands.py`:

```python
def test_parse_skills(self) -> None:
    command = parse_command("/skills")
    self.assertIsNotNone(command)
    self.assertEqual(command.kind, "skills")
    self.assertIsNone(command.argument)

def test_help_text_includes_skills(self) -> None:
    text = help_text()
    self.assertIn("/skills", text)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_commands.py::CommandsTest::test_parse_skills tests/test_commands.py::CommandsTest::test_help_text_includes_skills -v`
Expected: FAIL

- [ ] **Step 3: Add `list_skills()` to `CliRuntime` protocol**

In `src/light_claw/runtime/registry.py`, update the protocol (add `Dict` to the typing import):

```python
from typing import Callable, Dict, Iterable, List, Protocol, Tuple


class CliRuntime(Protocol):
    provider_id: str
    display_name: str

    async def run(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: str | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> CliRunResult:
        ...

    def list_skills(self) -> Dict[str, List[Tuple[str, str]]]:
        ...
```

- [ ] **Step 4: Add `/skills` parsing and update help text**

In `src/light_claw/commands.py`, add before the final `return None`:

```python
    if cmd == "/skills":
        return Command(kind="skills")
    return None
```

Update `help_text()`:

```python
def help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "/help",
            "/skills",
            "/cli list",
            "/cli current",
            "/cli use <provider>",
            "/reset",
        ]
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_commands.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/light_claw/runtime/registry.py src/light_claw/commands.py tests/test_commands.py
git commit -m "feat: add list_skills() to CliRuntime protocol and /skills command parsing"
```

---

### Task 5: Add `/skills` command handler

**Files:**
- Modify: `src/light_claw/chat_commands.py:32-97`
- Create: `tests/test_chat_commands.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chat_commands.py`:

```python
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.modules.setdefault("lark_oapi", types.SimpleNamespace())

from light_claw.chat_commands import ChatCommandHandler
from light_claw.commands import Command
from light_claw.communication.messages import InboundMessage, ReplyTarget
from light_claw.config import AgentSettings, Settings
from light_claw.models import CliProviderInfo, WorkspaceRecord
from light_claw.runtime import CliRuntimeRegistry
from light_claw.runtime.claude_code import ClaudeCodeRuntime
from light_claw.runtime.codex_cli import CodexCliRuntime
from light_claw.store import StateStore
from light_claw.workspaces import WorkspaceManager


class _FakeCommunicationChannel:
    async def send_text(self, target, content):
        pass


def _settings(tmp_dir: str) -> Settings:
    return Settings(
        base_dir=Path(tmp_dir), host="127.0.0.1", port=8000,
        data_dir=Path(tmp_dir) / ".data",
        database_path=Path(tmp_dir) / ".data" / "state.db",
        workspaces_dir=Path(tmp_dir) / ".data" / "workspaces",
        claude_bin="claude", claude_model=None,
        claude_permission_mode="bypassPermissions", claude_add_dirs=[],
        codex_bin="codex", codex_model=None, codex_search=False,
        codex_sandbox="full-auto", codex_timeout_min_seconds=180,
        codex_timeout_max_seconds=900, codex_timeout_per_char_ms=80,
        codex_stall_timeout_seconds=120, codex_add_dirs=[],
        status_heartbeat_enabled=False, status_heartbeat_seconds=3600,
        inbound_message_ttl_seconds=60, default_cli_provider="codex",
        feishu_enabled=False, feishu_event_mode="webhook",
        feishu_app_id=None, feishu_app_secret=None,
        feishu_verification_token=None, allow_from="*",
        default_workspace_name="default", agents=(),
    )


def _agent(
    mcp_config_path: Path | None = None,
) -> AgentSettings:
    return AgentSettings(
        agent_id="a", name="A", feishu_app_id=None, feishu_app_secret=None,
        feishu_verification_token=None, allow_from="*",
        default_workspace_name="default", default_cli_provider="codex",
        codex_model=None, codex_search=False, codex_sandbox="full-auto",
        skills_path=None, mcp_config_path=mcp_config_path,
    )


def _msg() -> InboundMessage:
    return InboundMessage(
        agent_id="a", bot_app_id="bot", owner_id="ou_1",
        conversation_id="c1", message_id="m1", message_type="text",
        content="/skills", reply_target=ReplyTarget("ou_1", "open_id"),
    )


def _registry(
    claude_config_dir: Path, codex_config_dir: Path,
) -> CliRuntimeRegistry:
    return CliRuntimeRegistry(
        providers=[
            CliProviderInfo("claude-code", "Claude Code", "test", True),
            CliProviderInfo("codex", "Codex", "test", True),
        ],
        runtimes={
            "claude-code": ClaudeCodeRuntime(config_dir=claude_config_dir),
            "codex": CodexCliRuntime(config_dir=codex_config_dir),
        },
    )


def _setup_workspace(store, tmp_dir, cli_provider):
    wp = Path(tmp_dir) / "workspaces" / "a"
    wp.mkdir(parents=True, exist_ok=True)
    store.create_workspace(WorkspaceRecord(
        agent_id="a", owner_id="ou_1", workspace_id="default",
        name="Default", path=wp, cli_provider=cli_provider,
        created_at=0.0, updated_at=0.0,
    ))


class SkillsCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_shows_claude_code_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            skill_dir = claude_dir / "skills" / "pdf"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pdf\ndescription: PDF tools\n---\n", encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "claude-code")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces", store,
                ),
                cli_registry=_registry(claude_dir, Path(tmp) / ".codex"),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Claude Code", result)
            self.assertIn("pdf", result)
            self.assertIn("PDF tools", result)
            store.close()

    async def test_shows_codex_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            plugin_dir = codex_dir / "plugins" / "cache" / "mp" / "gh" / "abc"
            skill_dir = plugin_dir / "skills" / "fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: fix-ci\ndescription: Fix CI\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "github", "version": "0.1.0"}),
                encoding="utf-8",
            )
            (codex_dir / "config.toml").write_text(
                '[plugins."gh@mp"]\nenabled = true\n', encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces", store,
                ),
                cli_registry=_registry(Path(tmp) / ".claude", codex_dir),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Codex", result)
            self.assertIn("fix-ci", result)
            store.close()

    async def test_shows_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / "mcp.json"
            mcp_file.write_text(
                '{"mcpServers": {"zotero": {}, "github": {}}}', encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp),
                agent=_agent(mcp_config_path=mcp_file),
                store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces", store,
                ),
                cli_registry=_registry(
                    Path(tmp) / ".claude", Path(tmp) / ".codex"
                ),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Configured MCP tools:", result)
            self.assertIn("github", result)
            self.assertIn("zotero", result)
            store.close()

    async def test_shows_no_skills_message_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces", store,
                ),
                cli_registry=_registry(
                    Path(tmp) / ".claude", Path(tmp) / ".codex"
                ),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("No skills or MCP tools found", result)
            store.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_chat_commands.py -v`
Expected: FAIL — `handle()` returns `None` for `kind="skills"`

- [ ] **Step 3: Add handler dispatch**

In `src/light_claw/chat_commands.py`, add before `if command.kind == "invalid"`:

```python
        if command.kind == "skills":
            workspace = self.ensure_workspace()
            return self._render_skills(workspace.cli_provider)
```

- [ ] **Step 4: Implement `_render_skills` and `_read_mcp_section`**

Add to `ChatCommandHandler` after `_render_cli_list`:

```python
    def _render_skills(self, provider_id: str) -> str:
        provider = self.cli_registry.get_provider(provider_id)
        runtime = self.cli_registry.get_runtime(provider_id)
        display = provider.display_name if provider else provider_id

        lines = ["{} ({}) available skills".format(display, provider_id), ""]

        skill_groups = runtime.list_skills()
        if skill_groups:
            for source, skills in skill_groups.items():
                lines.append("{}:".format(source))
                for name, desc in skills:
                    if desc:
                        lines.append("  - {} — {}".format(name, desc))
                    else:
                        lines.append("  - {}".format(name))
                lines.append("")

        mcp_section = self._read_mcp_section()
        if mcp_section:
            lines.extend(mcp_section)
            lines.append("")

        if not skill_groups and not self.agent.mcp_config_path:
            lines.append("No skills or MCP tools found for this agent.")

        return "\n".join(lines)

    def _read_mcp_section(self) -> list[str] | None:
        import json

        mcp_path = self.agent.mcp_config_path
        if not mcp_path:
            return None
        lines = ["Configured MCP tools:", "  Source: {}".format(mcp_path)]
        if not mcp_path.is_file():
            lines.append("  (path not found)")
            return lines
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            if servers:
                for name in sorted(servers.keys()):
                    lines.append("  - {}".format(name))
            else:
                lines.append("  (no servers configured)")
        except (OSError, json.JSONDecodeError):
            lines.append("  (unable to parse)")
        return lines
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_chat_commands.py -v`
Expected: ALL PASS

- [ ] **Step 6: Run the full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/light_claw/chat_commands.py tests/test_chat_commands.py
git commit -m "feat: add /skills command handler with skill discovery and MCP rendering"
```
