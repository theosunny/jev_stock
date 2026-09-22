"""Installer contract tests: no network, no project data writes."""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.temp.name) / "home"
        self.home.mkdir()
        self.env = {**os.environ, "HOME": str(self.home)}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def install(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(INSTALLER), *args], cwd=ROOT, env=self.env,
            text=True, capture_output=True, check=check,
        )

    def test_codex_reinstall_preserves_local_data_without_polluting_source(self) -> None:
        destination = self.home / ".codex/skills/stock-signal"
        destination.mkdir(parents=True)
        (destination / ".env").write_text("LARK_USER_OPEN_ID=local\n")
        (destination / "plan.json").write_text('{"local": true}\n')
        (destination / "trade_log.jsonl").write_text('{"order": 1}\n')
        (destination / "scripts").mkdir()
        (destination / "scripts/.env").write_text("LEGACY_SCRIPT_ENV=keep\n")
        (destination / "scripts/plan.json").write_text('{"legacy": true}\n')
        (destination / "codex_monitor").mkdir()
        (destination / "codex_monitor/report.json").write_text('{"slack": "acked"}\n')
        (destination / "reviews").mkdir()
        (destination / "reviews/20260922.md").write_text("review\n")
        data_dir = pathlib.Path(self.temp.name) / "market-data"

        self.install("--data-dir", str(data_dir))
        self.install()

        self.assertEqual((destination / ".env").read_text(), "LARK_USER_OPEN_ID=local\n")
        self.assertEqual((destination / "plan.json").read_text(), '{"local": true}\n')
        self.assertEqual((destination / "trade_log.jsonl").read_text(), '{"order": 1}\n')
        self.assertEqual((destination / "scripts/.env").read_text(), "LEGACY_SCRIPT_ENV=keep\n")
        self.assertEqual((destination / "scripts/plan.json").read_text(), '{"legacy": true}\n')
        self.assertEqual((destination / "codex_monitor/report.json").read_text(), '{"slack": "acked"}\n')
        self.assertEqual((destination / "reviews/20260922.md").read_text(), "review\n")
        self.assertEqual((destination / "scripts/.data_dir").read_text().strip(), str(data_dir))
        self.assertFalse((destination / "scripts/__pycache__").exists())
        self.assertEqual(stat.S_IMODE((destination / ".env").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((destination / "plan.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((destination / "codex_monitor/report.json").stat().st_mode), 0o600)
        self.assertFalse((ROOT / "stock-signal/.env").exists())
        self.assertFalse((ROOT / "stock-signal/trade_log.jsonl").exists())

    def test_external_data_is_initialized_without_overwriting_existing_credentials(self) -> None:
        data_dir = pathlib.Path(self.temp.name) / "market-data"
        data_dir.mkdir()
        (data_dir / ".env").write_text("TYPESAFE_API_KEY=external\n")

        result = self.install("--data-dir", str(data_dir))

        self.assertIn("Configure %s/.env" % data_dir, result.stdout)
        self.assertEqual((data_dir / ".env").read_text(), "TYPESAFE_API_KEY=external\n")
        self.assertTrue((data_dir / "plan.json").is_file())
        self.assertEqual(stat.S_IMODE((data_dir / ".env").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((data_dir / "plan.json").stat().st_mode), 0o600)
        self.assertFalse((ROOT / "stock-signal/.env").exists())

    def test_installs_to_requested_targets(self) -> None:
        self.install()
        self.install("--target", "claude")
        self.assertTrue((self.home / ".codex/skills/stock-signal/SKILL.md").is_file())
        self.assertTrue((self.home / ".claude/skills/stock-signal/SKILL.md").is_file())

    def test_unknown_option_fails(self) -> None:
        result = self.install("--unknown", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)

    def test_relative_data_dir_fails_before_installing(self) -> None:
        result = self.install("--data-dir", "relative-data", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absolute path", result.stderr)
        self.assertFalse((self.home / ".codex/skills/stock-signal").exists())


if __name__ == "__main__":
    unittest.main()
