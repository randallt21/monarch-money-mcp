"""
Local launchd integration for unattended Monarch maintenance.

Usage:
    uv run python -m monarch.scheduler install-launchd --hour 9 --minute 0
    uv run python -m monarch.scheduler status
    uv run python -m monarch.scheduler run-job
    uv run python -m monarch.scheduler uninstall-launchd
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from monarch.maintain import format_summary, run_maintain

DEFAULT_LABEL = "com.monarch-money-mcp.maintain"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_MAX_BYTES = 256 * 1024
DEFAULT_MAINTAIN_RUN_RETENTION = 45
DEFAULT_SLOW_RUN_SECONDS = 300
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 240.0


def _support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "monarch-money-mcp"


def _state_dir() -> Path:
    return _support_dir() / "maintain"


def _runs_dir() -> Path:
    return _state_dir() / "runs"


def _logs_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "monarch-money-mcp"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path(label: str) -> Path:
    return _launch_agents_dir() / f"{label}.plist"


def _ensure_dirs() -> None:
    for path in (_state_dir(), _runs_dir(), _logs_dir(), _launch_agents_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _now() -> datetime:
    return datetime.now().astimezone()


def _timestamp_slug(dt: datetime) -> str:
    return dt.strftime("%Y%m%d-%H%M%S")


def _find_uv() -> str:
    uv_path = shutil.which("uv")
    if not uv_path:
        raise RuntimeError("Could not find `uv` in PATH")
    return uv_path


def _stable_path() -> str:
    entries: list[str] = []
    uv_parent = str(Path(_find_uv()).resolve().parent)
    for candidate in (
        uv_parent,
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ):
        if candidate not in entries:
            entries.append(candidate)
    return ":".join(entries)


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        text=True,
        capture_output=True,
        check=check,
    )


def _build_plist(label: str, *, hour: int, minute: int, refresh: bool) -> dict[str, Any]:
    stdout_path = _logs_dir() / "maintain.stdout.log"
    stderr_path = _logs_dir() / "maintain.stderr.log"
    program_arguments = [
        _find_uv(),
        "run",
        "python",
        "-m",
        "monarch.scheduler",
        "run-job",
    ]
    if refresh:
        program_arguments.append("--refresh")

    return {
        "Label": label,
        "WorkingDirectory": str(REPO_ROOT),
        "ProgramArguments": program_arguments,
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": _stable_path(),
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
        "RunAtLoad": False,
        "StartCalendarInterval": {
            "Hour": hour,
            "Minute": minute,
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ThrottleInterval": 300,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _trim_log_file(path: Path, *, max_bytes: int = DEFAULT_LOG_MAX_BYTES) -> None:
    if not path.exists():
        return
    size = path.stat().st_size
    if size <= max_bytes:
        return

    with path.open("rb") as handle:
        handle.seek(size - max_bytes)
        tail = handle.read()

    newline_index = tail.find(b"\n")
    if newline_index != -1 and newline_index + 1 < len(tail):
        tail = tail[newline_index + 1 :]
    path.write_bytes(tail)


def _prune_old_entries(entries: list[Path], *, keep: int) -> None:
    if keep < 1:
        keep = 1
    for stale_path in sorted(entries, key=lambda path: path.name, reverse=True)[keep:]:
        stale_path.unlink(missing_ok=True)


def _housekeep_scheduler_artifacts() -> None:
    _ensure_dirs()
    _prune_old_entries(list(_runs_dir().glob("*.json")), keep=DEFAULT_MAINTAIN_RUN_RETENTION)
    _prune_old_entries(list(_runs_dir().glob("*.txt")), keep=DEFAULT_MAINTAIN_RUN_RETENTION)
    _trim_log_file(_logs_dir() / "maintain.stdout.log")
    _trim_log_file(_logs_dir() / "maintain.stderr.log")


def _latest_json_path() -> Path:
    return _state_dir() / "latest-summary.json"


def _latest_text_path() -> Path:
    return _state_dir() / "latest-summary.txt"


def _latest_error_json_path() -> Path:
    return _state_dir() / "latest-error.json"


def _latest_error_path() -> Path:
    return _state_dir() / "latest-error.txt"


def _run_file_paths(slug: str) -> tuple[Path, Path]:
    return (
        _runs_dir() / f"{slug}.json",
        _runs_dir() / f"{slug}.txt",
    )


def _wrap_summary(summary: dict[str, Any], *, started_at: datetime, finished_at: datetime) -> dict[str, Any]:
    return {
        "scheduler": {
            "label": DEFAULT_LABEL,
            "repo_root": str(REPO_ROOT),
            "hostname": socket.gethostname(),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        },
        "summary": summary,
    }


def _failure_payload(*, started_at: datetime, finished_at: datetime, error_text: str) -> dict[str, Any]:
    return {
        "scheduler": {
            "label": DEFAULT_LABEL,
            "repo_root": str(REPO_ROOT),
            "hostname": socket.gethostname(),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        },
        "error": error_text,
    }


def _log_run_event(
    event: str,
    *,
    started_at: datetime,
    month: str,
    refresh: bool,
    dry_run: bool,
    output: Any,
    finished_at: datetime | None = None,
    run_path: Path | None = None,
) -> None:
    details = [
        f"run={_timestamp_slug(started_at)}",
        f"month={month}",
        f"refresh={'yes' if refresh else 'no'}",
        f"dry_run={'yes' if dry_run else 'no'}",
    ]
    if finished_at is not None:
        duration = round((finished_at - started_at).total_seconds(), 3)
        details.append(f"duration_seconds={duration}")
    if run_path is not None:
        details.append(f"artifact={run_path}")
    print(
        f"[{(finished_at or started_at).isoformat()}] Monarch maintain {event} | "
        + " | ".join(details),
        file=output,
    )


def _notify_user(title: str, message: str) -> None:
    script = f'display notification {json.dumps(message[:240])} with title {json.dumps(title[:80])}'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)


def _describe_exception(exc: BaseException) -> str:
    detail = str(exc).strip()
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _latest_success_payload() -> dict[str, Any] | None:
    path = _latest_json_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_failure_payload() -> dict[str, Any] | None:
    path = _latest_error_json_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _summarize_scheduler_health(
    *,
    installed: bool,
    loaded: bool,
    state: str | None,
    latest_success: dict[str, Any] | None,
    latest_failure: dict[str, Any] | None,
    now: datetime | None = None,
) -> tuple[str, str]:
    if not installed:
        return ("missing", "LaunchAgent plist is not installed.")
    if not loaded:
        return ("missing", "launchd job is not loaded.")
    if state == "running":
        return ("running", "Maintenance job is currently running.")

    now_dt = now or _now()
    success_finished = _parse_iso_datetime((latest_success or {}).get("scheduler", {}).get("finished_at"))
    failure_finished = _parse_iso_datetime((latest_failure or {}).get("scheduler", {}).get("finished_at"))

    if failure_finished and (success_finished is None or failure_finished >= success_finished):
        return ("failing", "Latest maintenance run ended in failure.")
    if success_finished is None:
        return ("stale", "No successful maintenance run has been recorded yet.")
    success_duration = (latest_success or {}).get("scheduler", {}).get("duration_seconds")
    if isinstance(success_duration, (int, float)) and float(success_duration) > DEFAULT_SLOW_RUN_SECONDS:
        return (
            "degraded",
            "Latest successful maintenance run exceeded the slow-run threshold.",
        )
    if (now_dt - success_finished).total_seconds() > 48 * 3600:
        return ("stale", "Latest successful maintenance run is older than 48 hours.")
    return ("healthy", "Latest successful maintenance run is recent.")


async def _run_job(
    *,
    refresh: bool,
    month: str | None,
    lookback_months: int,
    history_months: int,
    min_history: int,
    min_confidence: float,
    dry_run: bool,
    max_attempts: int = 2,
    retry_delay_seconds: float = 5.0,
    attempt_timeout_seconds: float = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
) -> int:
    if attempt_timeout_seconds <= 0:
        raise ValueError("attempt_timeout_seconds must be > 0")

    _housekeep_scheduler_artifacts()
    lock_path = _state_dir() / "maintain.lock"
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another Monarch maintain run is already in progress.", file=sys.stderr)
            return 0

        started_at = _now()
        slug = _timestamp_slug(started_at)
        run_json_path, run_text_path = _run_file_paths(slug)
        selected_month = month or started_at.strftime("%Y-%m")
        _log_run_event(
            "start",
            started_at=started_at,
            month=selected_month,
            refresh=refresh,
            dry_run=dry_run,
            output=sys.stdout,
        )

        try:
            summary: dict[str, Any] | None = None
            last_error: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    summary = await asyncio.wait_for(
                        run_maintain(
                            month=selected_month,
                            lookback_months=lookback_months,
                            history_months=history_months,
                            min_history=min_history,
                            min_confidence=min_confidence,
                            refresh=refresh,
                            dry_run=dry_run,
                        ),
                        timeout=attempt_timeout_seconds,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt >= max_attempts:
                        raise
                    print(
                        f"Maintain attempt {attempt} failed: {_describe_exception(exc)}. "
                        f"Retrying in {retry_delay_seconds:.0f}s...",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(retry_delay_seconds)
            if summary is None and last_error is not None:
                raise last_error
            finished_at = _now()
            payload = _wrap_summary(summary, started_at=started_at, finished_at=finished_at)
            text_summary = format_summary(summary, color=False)
            _write_json(_latest_json_path(), payload)
            _write_text(_latest_text_path(), text_summary)
            _write_json(run_json_path, payload)
            _write_text(run_text_path, text_summary)
            if _latest_error_json_path().exists():
                _latest_error_json_path().unlink()
            if _latest_error_path().exists():
                _latest_error_path().unlink()
            print(text_summary, end="")
            _log_run_event(
                "success",
                started_at=started_at,
                finished_at=finished_at,
                month=selected_month,
                refresh=refresh,
                dry_run=dry_run,
                run_path=run_json_path,
                output=sys.stdout,
            )
            auto_applied = (summary or {}).get("auto_applied", 0)
            rules_created = (summary or {}).get("rules_created", 0)
            needs_review = (summary or {}).get("needs_review", 0)
            parts: list[str] = []
            if auto_applied:
                parts.append(f"{auto_applied} sorted")
            if rules_created:
                parts.append(f"{rules_created} rule{'s' if rules_created != 1 else ''} created")
            if needs_review:
                parts.append(f"{needs_review} needs you")
            notify_body = ", ".join(parts) if parts else "All clear"
            if not dry_run:
                _notify_user("Monarch", notify_body)
            return 0
        except Exception:
            finished_at = _now()
            error_text = traceback.format_exc()
            failure_payload = _failure_payload(
                started_at=started_at,
                finished_at=finished_at,
                error_text=error_text,
            )
            _write_json(run_json_path, failure_payload)
            _write_text(run_text_path, error_text)
            _write_json(_latest_error_json_path(), failure_payload)
            _write_text(_latest_error_path(), error_text)
            _log_run_event(
                "failure",
                started_at=started_at,
                finished_at=finished_at,
                month=selected_month,
                refresh=refresh,
                dry_run=dry_run,
                run_path=run_json_path,
                output=sys.stderr,
            )
            print(error_text, file=sys.stderr, end="")
            _notify_user("Monarch: run failed", "Daily maintenance failed — check the scheduler log.")
            return 1


def cmd_install_launchd(label: str, *, hour: int, minute: int, refresh: bool, run_now: bool) -> int:
    _housekeep_scheduler_artifacts()
    plist_path = _plist_path(label)
    payload = _build_plist(label, hour=hour, minute=minute, refresh=refresh)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)

    domain = _launchd_domain()
    _launchctl("bootout", domain, str(plist_path), check=False)
    _launchctl("bootstrap", domain, str(plist_path))
    _launchctl("enable", f"{domain}/{label}", check=False)
    if run_now:
        _launchctl("kickstart", "-k", f"{domain}/{label}")

    print(f"Installed LaunchAgent: {plist_path}")
    print(f"Label: {label}")
    print(f"Schedule: daily at {hour:02d}:{minute:02d}")
    print(f"Repo: {REPO_ROOT}")
    print(f"Latest summary: {_latest_text_path()}")
    return 0


def cmd_uninstall_launchd(label: str) -> int:
    plist_path = _plist_path(label)
    domain = _launchd_domain()
    _launchctl("bootout", domain, str(plist_path), check=False)
    if plist_path.exists():
        plist_path.unlink()
    print(f"Removed LaunchAgent: {plist_path}")
    return 0


def _extract_launchctl_value(output: str, prefix: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return None


def cmd_status(label: str, *, verbose: bool) -> int:
    plist_path = _plist_path(label)
    print(f"Label: {label}")
    print(f"Repo: {REPO_ROOT}")
    print(f"Plist: {plist_path}")
    print(f"Latest summary: {_latest_text_path()}")
    print(f"Latest json: {_latest_json_path()}")
    print(f"Latest error json: {_latest_error_json_path()}")
    print(f"Latest error: {_latest_error_path()}")
    print(f"Stdout log: {_logs_dir() / 'maintain.stdout.log'}")
    print(f"Stderr log: {_logs_dir() / 'maintain.stderr.log'}")
    latest_summary = _latest_success_payload()
    if latest_summary:
        scheduler = latest_summary.get("scheduler") or {}
        summary = latest_summary.get("summary") or {}
        print(f"Latest successful run start: {scheduler.get('started_at', '—')}")
        print(f"Latest successful run end: {scheduler.get('finished_at', '—')}")
        print(f"Latest successful month: {summary.get('month', '—')}")
    latest_failure = _latest_failure_payload()
    if latest_failure:
        scheduler = latest_failure.get("scheduler") or {}
        print(f"Latest failed run start: {scheduler.get('started_at', '—')}")
        print(f"Latest failed run end: {scheduler.get('finished_at', '—')}")
    installed = plist_path.exists()
    print(f"Installed: {'yes' if installed else 'no'}")
    loaded = False
    state: str | None = None
    try:
        result = _launchctl("print", f"{_launchd_domain()}/{label}")
        loaded = True
        print("Loaded: yes")
        state = _extract_launchctl_value(result.stdout, "state")
        runs = _extract_launchctl_value(result.stdout, "runs")
        last_exit = _extract_launchctl_value(result.stdout, "last exit code")
        if state:
            print(f"State: {state}")
        if runs:
            print(f"Runs: {runs}")
        if last_exit:
            print(f"Last exit code: {last_exit}")
        if verbose and result.stdout.strip():
            print()
            print(result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        print("Loaded: no")
        details = (exc.stderr or exc.stdout or "").strip()
        if details:
            print(details)
    health, detail = _summarize_scheduler_health(
        installed=installed,
        loaded=loaded,
        state=state,
        latest_success=latest_summary,
        latest_failure=latest_failure,
    )
    print(f"Health: {health}")
    print(f"Health detail: {detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local launchd scheduling for Monarch maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install-launchd", help="Install and load the launchd job")
    install.add_argument("--label", default=DEFAULT_LABEL)
    install.add_argument("--hour", type=_hour_arg, default=9)
    install.add_argument("--minute", type=_minute_arg, default=0)
    install.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip account refresh in the scheduled job",
    )
    install.add_argument(
        "--run-now",
        action="store_true",
        help="Kick off the job immediately after install",
    )

    uninstall = subparsers.add_parser("uninstall-launchd", help="Unload and remove the launchd job")
    uninstall.add_argument("--label", default=DEFAULT_LABEL)

    status = subparsers.add_parser("status", help="Show launchd and artifact status")
    status.add_argument("--label", default=DEFAULT_LABEL)
    status.add_argument("--verbose", action="store_true", help="Print raw launchctl details")

    run_job = subparsers.add_parser("run-job", help="Run the scheduled maintenance job once")
    run_job.add_argument("--month", help="Override reporting month (YYYY-MM)")
    run_job.add_argument("--lookback-months", type=int, default=3)
    run_job.add_argument("--history-months", type=int, default=12)
    run_job.add_argument("--min-history", type=int, default=3)
    run_job.add_argument("--min-confidence", type=float, default=0.8)
    run_job.add_argument("--refresh", action="store_true", help="Request an account refresh first")
    run_job.add_argument("--dry-run", action="store_true", help="Do not apply safe fixes")
    run_job.add_argument("--max-attempts", type=int, default=2)
    run_job.add_argument("--retry-delay-seconds", type=float, default=5.0)
    run_job.add_argument(
        "--attempt-timeout-seconds",
        type=float,
        default=DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
        help=(
            "Maximum seconds for each maintain attempt before retry/failure "
            f"(default: {DEFAULT_ATTEMPT_TIMEOUT_SECONDS:.0f})"
        ),
    )

    return parser


def _hour_arg(value: str) -> int:
    parsed = int(value)
    if parsed < 0 or parsed > 23:
        raise argparse.ArgumentTypeError("hour must be between 0 and 23")
    return parsed


def _minute_arg(value: str) -> int:
    parsed = int(value)
    if parsed < 0 or parsed > 59:
        raise argparse.ArgumentTypeError("minute must be between 0 and 59")
    return parsed


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install-launchd":
        raise SystemExit(
            cmd_install_launchd(
                args.label,
                hour=args.hour,
                minute=args.minute,
                refresh=not args.no_refresh,
                run_now=args.run_now,
            )
        )
    if args.command == "uninstall-launchd":
        raise SystemExit(cmd_uninstall_launchd(args.label))
    if args.command == "status":
        raise SystemExit(cmd_status(args.label, verbose=args.verbose))
    if args.command == "run-job":
        raise SystemExit(
            asyncio.run(
                _run_job(
                    refresh=args.refresh,
                    month=args.month,
                    lookback_months=args.lookback_months,
                    history_months=args.history_months,
                    min_history=args.min_history,
                    min_confidence=args.min_confidence,
                    dry_run=args.dry_run,
                    max_attempts=args.max_attempts,
                    retry_delay_seconds=args.retry_delay_seconds,
                    attempt_timeout_seconds=args.attempt_timeout_seconds,
                )
            )
        )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
