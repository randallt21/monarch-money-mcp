from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monarch import codex_audit


class CodexAuditLaunchdTests(unittest.TestCase):
    @patch("monarch.codex_audit._find_uv", return_value="/usr/local/bin/uv")
    def test_build_plist_runs_wrapper_command_instead_of_direct_codex_exec(self, _mock_find_uv) -> None:
        payload = codex_audit._build_plist("com.example.audit", weekday=1, hour=12, minute=0)

        self.assertEqual(
            payload["ProgramArguments"],
            [
                "/usr/local/bin/uv",
                "run",
                "python",
                "-m",
                "monarch.codex_audit",
                "run-agent",
            ],
        )
        self.assertEqual(payload["StartCalendarInterval"], {"Weekday": 1, "Hour": 12, "Minute": 0})

    def test_load_result_payload_returns_error_for_unreadable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "result.json"
            result_path.write_text("{not-json", encoding="utf-8")

            payload = codex_audit._load_result_payload(result_path, exit_code=0)

        self.assertEqual(payload["status"], "error")
        self.assertIn("unreadable structured result", payload["summary"])

    def test_load_result_payload_coerces_nonzero_exit_to_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "summary": "Applied a local fix.",
                        "files_changed": ["monarch/codex_audit.py"],
                        "verification_commands": ["python -m unittest tests.test_codex_audit"],
                        "question": None,
                    }
                ),
                encoding="utf-8",
            )

            payload = codex_audit._load_result_payload(result_path, exit_code=17)

        self.assertEqual(payload["status"], "error")
        self.assertIn("exited with code 17", payload["summary"])
        self.assertIn("Applied a local fix.", payload["summary"])

    def test_housekeep_codex_audit_artifacts_prunes_old_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            support_dir = root / "codex-audit"
            runs_dir = support_dir / "runs"
            logs_dir = root / "logs"
            launch_agents_dir = root / "LaunchAgents"
            runs_dir.mkdir(parents=True)
            logs_dir.mkdir(parents=True)
            launch_agents_dir.mkdir(parents=True)

            for index in range(40):
                run_dir = runs_dir / f"20260413-{index:06d}"
                run_dir.mkdir()
                (run_dir / "metadata.json").write_text("{}", encoding="utf-8")

            huge_log = ("old-line\n" * 50000).encode("utf-8")
            (logs_dir / "codex-audit.stdout.log").write_bytes(huge_log)
            (logs_dir / "codex-audit.stderr.log").write_bytes(huge_log)

            with (
                patch("monarch.codex_audit._support_dir", return_value=support_dir),
                patch("monarch.codex_audit._runs_dir", return_value=runs_dir),
                patch("monarch.codex_audit._logs_dir", return_value=logs_dir),
                patch("monarch.codex_audit._launch_agents_dir", return_value=launch_agents_dir),
            ):
                codex_audit._housekeep_codex_audit_artifacts()

            self.assertEqual(len([path for path in runs_dir.iterdir() if path.is_dir()]), codex_audit.DEFAULT_RUN_RETENTION)
            self.assertLessEqual((logs_dir / "codex-audit.stdout.log").stat().st_size, 256 * 1024)
            self.assertLessEqual((logs_dir / "codex-audit.stderr.log").stat().st_size, 256 * 1024)

    def test_codex_audit_health_marks_recent_success_healthy(self) -> None:
        health, detail = codex_audit._summarize_codex_audit_health(
            installed=True,
            loaded=True,
            state="waiting",
            latest_payload={
                "metadata": {"finished_at": "2026-04-13T11:33:18-07:00"},
                "result": {"status": "complete"},
            },
            latest_run_dir=None,
            now=codex_audit._parse_iso_datetime("2026-04-13T18:00:00+00:00"),
        )

        self.assertEqual(health, "healthy")
        self.assertIn("completed recently", detail)


if __name__ == "__main__":
    unittest.main()
