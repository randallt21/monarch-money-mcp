from __future__ import annotations

import asyncio
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from monarch import scheduler


def _summary_payload(month: str) -> dict[str, object]:
    return {
        "month": month,
        "refresh": {"requested": False},
        "dry_run": True,
        "lookback_months": 3,
        "history_months": 12,
        "thresholds": {"min_history": 3, "min_confidence": 0.8},
        "targets_scanned": 0,
        "safe_suggestions": [],
        "manual_suggestions": [],
        "applied": [],
        "failures": [],
        "remaining_review_count": 0,
        "remaining_uncategorized_count": 0,
        "budget_summary": None,
        "budget_watch": [],
        "rule_count": 0,
        "phase_timings_seconds": {},
    }


class SchedulerTests(unittest.TestCase):
    def test_housekeep_scheduler_artifacts_prunes_old_runs_and_trims_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "maintain"
            runs_dir = state_dir / "runs"
            logs_dir = root / "logs"
            launch_agents_dir = root / "LaunchAgents"
            runs_dir.mkdir(parents=True)
            logs_dir.mkdir(parents=True)
            launch_agents_dir.mkdir(parents=True)

            for index in range(50):
                slug = f"20260413-{index:06d}"
                (runs_dir / f"{slug}.json").write_text("{}", encoding="utf-8")
                (runs_dir / f"{slug}.txt").write_text("summary", encoding="utf-8")

            huge_log = ("old-line\n" * 50000).encode("utf-8")
            (logs_dir / "maintain.stdout.log").write_bytes(huge_log)
            (logs_dir / "maintain.stderr.log").write_bytes(huge_log)

            with (
                patch("monarch.scheduler._state_dir", return_value=state_dir),
                patch("monarch.scheduler._runs_dir", return_value=runs_dir),
                patch("monarch.scheduler._logs_dir", return_value=logs_dir),
                patch("monarch.scheduler._launch_agents_dir", return_value=launch_agents_dir),
            ):
                scheduler._housekeep_scheduler_artifacts()

            self.assertEqual(len(list(runs_dir.glob("*.json"))), scheduler.DEFAULT_MAINTAIN_RUN_RETENTION)
            self.assertEqual(len(list(runs_dir.glob("*.txt"))), scheduler.DEFAULT_MAINTAIN_RUN_RETENTION)
            self.assertLessEqual(
                (logs_dir / "maintain.stdout.log").stat().st_size,
                scheduler.DEFAULT_LOG_MAX_BYTES,
            )
            self.assertLessEqual(
                (logs_dir / "maintain.stderr.log").stat().st_size,
                scheduler.DEFAULT_LOG_MAX_BYTES,
            )

    def test_failure_keeps_latest_success_summary_and_writes_latest_error_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "maintain"
            runs_dir = state_dir / "runs"
            logs_dir = root / "logs"
            launch_agents_dir = root / "LaunchAgents"

            started_at = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)
            finished_at = started_at + timedelta(seconds=20)
            failed_started_at = started_at + timedelta(days=1)
            failed_finished_at = failed_started_at + timedelta(seconds=7)

            with (
                patch("monarch.scheduler._state_dir", return_value=state_dir),
                patch("monarch.scheduler._runs_dir", return_value=runs_dir),
                patch("monarch.scheduler._logs_dir", return_value=logs_dir),
                patch("monarch.scheduler._launch_agents_dir", return_value=launch_agents_dir),
                patch(
                    "monarch.scheduler._now",
                    side_effect=[started_at, finished_at, failed_started_at, failed_finished_at],
                ),
                patch(
                    "monarch.scheduler.run_maintain",
                    new=AsyncMock(side_effect=[_summary_payload("2026-04"), RuntimeError("boom")]),
                ),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = asyncio.run(
                        scheduler._run_job(
                            refresh=False,
                            month="2026-04",
                            lookback_months=3,
                            history_months=12,
                            min_history=3,
                            min_confidence=0.8,
                            dry_run=True,
                            max_attempts=1,
                        )
                    )
                    self.assertEqual(exit_code, 0)

                    exit_code = asyncio.run(
                        scheduler._run_job(
                            refresh=False,
                            month="2026-05",
                            lookback_months=3,
                            history_months=12,
                            min_history=3,
                            min_confidence=0.8,
                            dry_run=True,
                            max_attempts=1,
                        )
                    )
                    self.assertEqual(exit_code, 1)

            latest_summary = json.loads((state_dir / "latest-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(latest_summary["summary"]["month"], "2026-04")

            latest_error = json.loads((state_dir / "latest-error.json").read_text(encoding="utf-8"))
            self.assertIn("RuntimeError: boom", latest_error["error"])
            self.assertEqual(
                latest_error["scheduler"]["started_at"],
                failed_started_at.isoformat(),
            )

            latest_summary_text = (state_dir / "latest-summary.txt").read_text(encoding="utf-8")
            self.assertIn("Monarch Maintain", latest_summary_text)
            self.assertNotIn("RuntimeError: boom", latest_summary_text)
            self.assertIn("Monarch maintain start", stdout.getvalue())
            self.assertIn("Monarch maintain success", stdout.getvalue())
            self.assertIn("Monarch maintain failure", stderr.getvalue())

    def test_run_job_times_out_each_maintain_attempt(self) -> None:
        async def slow_maintain(**_kwargs: object) -> dict[str, object]:
            await asyncio.sleep(1)
            return _summary_payload("2026-05")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "maintain"
            runs_dir = state_dir / "runs"
            logs_dir = root / "logs"
            launch_agents_dir = root / "LaunchAgents"

            started_at = datetime(2026, 5, 2, 9, 13, tzinfo=timezone.utc)
            finished_at = started_at + timedelta(seconds=12)

            with (
                patch("monarch.scheduler._state_dir", return_value=state_dir),
                patch("monarch.scheduler._runs_dir", return_value=runs_dir),
                patch("monarch.scheduler._logs_dir", return_value=logs_dir),
                patch("monarch.scheduler._launch_agents_dir", return_value=launch_agents_dir),
                patch("monarch.scheduler._now", side_effect=[started_at, finished_at]),
                patch("monarch.scheduler.run_maintain", new=slow_maintain),
            ):
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    exit_code = asyncio.run(
                        scheduler._run_job(
                            refresh=False,
                            month="2026-05",
                            lookback_months=3,
                            history_months=12,
                            min_history=3,
                            min_confidence=0.8,
                            dry_run=True,
                            max_attempts=1,
                            attempt_timeout_seconds=0.01,
                        )
                    )

            self.assertEqual(exit_code, 1)
            latest_error = json.loads((state_dir / "latest-error.json").read_text(encoding="utf-8"))
            self.assertIn("TimeoutError", latest_error["error"])
            self.assertIn("Monarch maintain failure", stderr.getvalue())

    def test_status_surfaces_latest_success_and_failure_metadata(self) -> None:
        summary_payload = {
            "scheduler": {
                "started_at": "2026-04-13T09:00:06-07:00",
                "finished_at": "2026-04-13T09:00:26-07:00",
            },
            "summary": {"month": "2026-04"},
        }
        failure_payload = {
            "scheduler": {
                "started_at": "2026-04-14T09:00:00-07:00",
                "finished_at": "2026-04-14T09:00:07-07:00",
            },
            "error": "traceback",
        }
        output = io.StringIO()
        with (
            patch("monarch.scheduler._latest_success_payload", return_value=summary_payload),
            patch("monarch.scheduler._latest_failure_payload", return_value=failure_payload),
            patch(
                "monarch.scheduler._launchctl",
                side_effect=subprocess.CalledProcessError(1, ["launchctl"], stderr="not loaded"),
            ),
            redirect_stdout(output),
        ):
            exit_code = scheduler.cmd_status("com.example.test", verbose=False)

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Latest successful run start: 2026-04-13T09:00:06-07:00", rendered)
        self.assertIn("Latest successful month: 2026-04", rendered)
        self.assertIn("Latest failed run start: 2026-04-14T09:00:00-07:00", rendered)
        self.assertIn("Loaded: no", rendered)

    def test_scheduler_health_marks_old_success_as_stale(self) -> None:
        health, detail = scheduler._summarize_scheduler_health(
            installed=True,
            loaded=True,
            state="waiting",
            latest_success={
                "scheduler": {
                    "finished_at": "2026-04-10T09:00:00-07:00",
                }
            },
            latest_failure=None,
            now=datetime(2026, 4, 13, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(health, "stale")
        self.assertIn("older than 48 hours", detail)

    def test_scheduler_health_marks_slow_success_as_degraded(self) -> None:
        health, detail = scheduler._summarize_scheduler_health(
            installed=True,
            loaded=True,
            state="waiting",
            latest_success={
                "scheduler": {
                    "finished_at": "2026-04-13T09:00:00-07:00",
                    "duration_seconds": scheduler.DEFAULT_SLOW_RUN_SECONDS + 1,
                }
            },
            latest_failure=None,
            now=datetime(2026, 4, 13, 18, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(health, "degraded")
        self.assertIn("slow-run threshold", detail)


if __name__ == "__main__":
    unittest.main()
