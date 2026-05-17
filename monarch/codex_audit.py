"""
Scheduled Codex engineering audit for the Monarch toolkit repo.

Usage:
    uv run python -m monarch.codex_audit run-agent
    uv run python -m monarch.codex_audit install-launchd --weekday 1 --hour 12 --minute 0
    uv run python -m monarch.codex_audit status
    uv run python -m monarch.codex_audit uninstall-launchd
"""

from __future__ import annotations

import argparse
import fcntl
import json
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from monarch.scheduler import (
    _find_uv,
    _launchctl,
    _launchd_domain,
    _minute_arg,
    _hour_arg,
    _stable_path,
    _trim_log_file,
)

DEFAULT_LABEL = "com.monarch-money-mcp.codex-audit"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_TIMEOUT_SECONDS = 15 * 60
DEFAULT_RUN_RETENTION = 26
DEFAULT_STALE_AFTER_DAYS = 10


def _support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "monarch-money-mcp" / "codex-audit"


def _runs_dir() -> Path:
    return _support_dir() / "runs"


def _logs_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "monarch-money-mcp"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path(label: str) -> Path:
    return _launch_agents_dir() / f"{label}.plist"


def _maintain_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "monarch-money-mcp" / "maintain"


def _ensure_dirs() -> None:
    for path in (_support_dir(), _runs_dir(), _logs_dir(), _launch_agents_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _find_codex() -> str:
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("Could not find `codex` in PATH")
    return codex_path


def _now() -> datetime:
    return datetime.now().astimezone()


def _timestamp_slug(dt: datetime) -> str:
    return dt.strftime("%Y%m%d-%H%M%S")


def _latest_result_path() -> Path:
    return _support_dir() / "latest-result.json"


def _latest_stdout_path() -> Path:
    return _support_dir() / "latest-stdout.txt"


def _latest_stderr_path() -> Path:
    return _support_dir() / "latest-stderr.txt"


def _latest_question_path() -> Path:
    return _support_dir() / "latest-question.txt"


def _latest_result_payload() -> dict[str, Any] | None:
    path = _latest_result_path()
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


def _result_error_payload(summary: str) -> dict[str, Any]:
    return {
        "status": "error",
        "summary": summary,
        "files_changed": [],
        "verification_commands": [],
        "question": None,
    }


def _load_result_payload(result_path: Path, *, exit_code: int) -> dict[str, Any]:
    if not result_path.exists():
        return _result_error_payload("Codex audit did not produce a structured result.")

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _result_error_payload(
            f"Codex audit produced an unreadable structured result: {exc}"
        )

    if not isinstance(payload, dict):
        return _result_error_payload("Codex audit produced a non-object structured result.")

    status = payload.get("status")
    if not isinstance(status, str):
        return _result_error_payload("Codex audit result is missing a valid `status` field.")

    if exit_code != 0 and status != "error":
        summary = str(payload.get("summary") or "").strip()
        detail = f"Codex audit exited with code {exit_code}."
        payload = dict(payload)
        payload["status"] = "error"
        payload["summary"] = f"{detail} {summary}".strip()
    return payload


def _latest_run_dir() -> Path | None:
    runs_root = _runs_dir()
    if not runs_root.exists():
        return None
    candidates = [path for path in runs_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def _prune_old_run_dirs(*, keep: int = DEFAULT_RUN_RETENTION) -> None:
    runs_root = _runs_dir()
    if not runs_root.exists():
        return
    candidates = sorted((path for path in runs_root.iterdir() if path.is_dir()), key=lambda path: path.name, reverse=True)
    for stale_path in candidates[keep:]:
        shutil.rmtree(stale_path, ignore_errors=True)


def _housekeep_codex_audit_artifacts() -> None:
    _ensure_dirs()
    _prune_old_run_dirs()
    _trim_log_file(_logs_dir() / "codex-audit.stdout.log")
    _trim_log_file(_logs_dir() / "codex-audit.stderr.log")


def _run_paths(slug: str) -> dict[str, Path]:
    run_dir = _runs_dir() / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "dir": run_dir,
        "result": run_dir / "result.json",
        "stdout": run_dir / "stdout.txt",
        "stderr": run_dir / "stderr.txt",
        "prompt": run_dir / "prompt.md",
        "metadata": run_dir / "metadata.json",
    }


def _running_metadata(*, started_at: datetime) -> dict[str, Any]:
    return {
        "label": DEFAULT_LABEL,
        "repo_root": str(REPO_ROOT),
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "duration_seconds": None,
        "exit_code": None,
        "timed_out": False,
        "run_state": "running",
        "git_status_before": [],
        "git_status_after": [],
        "stdout_path": None,
        "stderr_path": None,
    }


def _read_prompt_template() -> str:
    return (REPO_ROOT / "automation" / "codex-audit-prompt.md").read_text(encoding="utf-8")


def _schema_path() -> Path:
    return REPO_ROOT / "automation" / "codex-audit-output.schema.json"


def _build_prompt() -> str:
    return _read_prompt_template().replace("{{REPO_ROOT}}", str(REPO_ROOT)).replace(
        "{{LATEST_SUMMARY_JSON}}",
        str(_maintain_dir() / "latest-summary.json"),
    ).replace(
        "{{LATEST_SUMMARY_TEXT}}",
        str(_maintain_dir() / "latest-summary.txt"),
    ).replace(
        "{{MAINTAIN_STDOUT_LOG}}",
        str(_logs_dir() / "maintain.stdout.log"),
    ).replace(
        "{{MAINTAIN_STDERR_LOG}}",
        str(_logs_dir() / "maintain.stderr.log"),
    )


def _git_status_lines() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _build_plist(label: str, *, weekday: int, hour: int, minute: int) -> dict[str, Any]:
    return {
        "Label": label,
        "WorkingDirectory": str(REPO_ROOT),
        "ProgramArguments": [
            _find_uv(),
            "run",
            "python",
            "-m",
            "monarch.codex_audit",
            "run-agent",
        ],
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PATH": _stable_path(),
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
        "RunAtLoad": False,
        "StartCalendarInterval": {
            "Weekday": weekday,
            "Hour": hour,
            "Minute": minute,
        },
        "StandardOutPath": str(_logs_dir() / "codex-audit.stdout.log"),
        "StandardErrorPath": str(_logs_dir() / "codex-audit.stderr.log"),
        "ThrottleInterval": 1800,
    }


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _notify_user(title: str, message: str) -> None:
    script = f'display notification {json.dumps(message[:240])} with title {json.dumps(title[:80])}'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)


def _extract_launchctl_value(output: str, prefix: str) -> str | None:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split("=", 1)[1].strip()
    return None


def _summarize_codex_audit_health(
    *,
    installed: bool,
    loaded: bool,
    state: str | None,
    latest_payload: dict[str, Any] | None,
    latest_run_dir: Path | None,
    now: datetime | None = None,
) -> tuple[str, str]:
    if not installed:
        return ("missing", "LaunchAgent plist is not installed.")
    if not loaded:
        return ("missing", "launchd job is not loaded.")
    if state == "running":
        return ("running", "Codex audit job is currently running.")

    metadata = (latest_payload or {}).get("metadata") or {}
    result = (latest_payload or {}).get("result") or {}
    finished_at = _parse_iso_datetime(metadata.get("finished_at"))
    started_at = _parse_iso_datetime(metadata.get("started_at"))
    latest_dt = finished_at or started_at
    now_dt = now or _now()

    if result.get("status") == "error":
        return ("failing", "Latest Codex audit run ended in error.")
    if latest_dt is None and latest_run_dir is not None:
        return ("stale", "A run directory exists but no structured result was recorded.")
    if latest_dt is None:
        return ("stale", "No structured Codex audit run has been recorded yet.")
    if (now_dt - latest_dt).total_seconds() > DEFAULT_STALE_AFTER_DAYS * 24 * 3600:
        return (
            "stale",
            f"Latest Codex audit activity is older than {DEFAULT_STALE_AFTER_DAYS} days.",
        )
    return ("healthy", "Latest Codex audit run completed recently.")


def cmd_run_agent() -> int:
    _housekeep_codex_audit_artifacts()
    lock_path = _support_dir() / "codex-audit.lock"
    with lock_path.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another Codex audit run is already in progress.", file=sys.stderr)
            return 0

        started_at = _now()
        slug = _timestamp_slug(started_at)
        paths = _run_paths(slug)
        _write_json(paths["metadata"], _running_metadata(started_at=started_at))
        prompt = _build_prompt()
        _write_text(paths["prompt"], prompt)
        before_status = _git_status_lines()

        command = [
            _find_codex(),
            "exec",
            "-C",
            str(REPO_ROOT),
            "-c",
            "mcp_servers={}",
            "-c",
            'model_reasoning_effort="medium"',
            "--color",
            "never",
            "-s",
            "workspace-write",
            "--add-dir",
            str(_maintain_dir()),
            "--add-dir",
            str(_logs_dir()),
            "--output-schema",
            str(_schema_path()),
            "-o",
            str(paths["result"]),
            "-",
        ]
        timeout_hit = False
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                text=True,
                input=prompt,
                capture_output=True,
                timeout=DEFAULT_RUN_TIMEOUT_SECONDS,
            )
            finished_at = _now()
            stdout_text = completed.stdout
            stderr_text = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            timeout_hit = True
            finished_at = _now()
            stdout_text = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr_text = exc.stderr if isinstance(exc.stderr, str) else ""
            stderr_text = (stderr_text.rstrip() + "\n\n" if stderr_text else "") + (
                f"Timed out after {DEFAULT_RUN_TIMEOUT_SECONDS} seconds."
            )
            exit_code = 124
        after_status = _git_status_lines()

        _write_text(paths["stdout"], stdout_text)
        _write_text(paths["stderr"], stderr_text)
        _write_text(_latest_stdout_path(), stdout_text)
        _write_text(_latest_stderr_path(), stderr_text)

        result_payload: dict[str, Any]
        if timeout_hit:
            result_payload = _result_error_payload(
                f"Codex audit timed out after {DEFAULT_RUN_TIMEOUT_SECONDS} seconds."
            )
        else:
            result_payload = _load_result_payload(paths["result"], exit_code=exit_code)

        metadata = {
            "label": DEFAULT_LABEL,
            "repo_root": str(REPO_ROOT),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
            "exit_code": exit_code,
            "timed_out": timeout_hit,
            "run_state": "finished",
            "git_status_before": before_status,
            "git_status_after": after_status,
            "stdout_path": str(paths["stdout"]),
            "stderr_path": str(paths["stderr"]),
        }
        _write_json(paths["metadata"], metadata)
        wrapped = {
            "metadata": metadata,
            "result": result_payload,
        }
        _write_json(_latest_result_path(), wrapped)

        question = result_payload.get("question")
        if isinstance(question, str) and question.strip():
            _write_text(_latest_question_path(), question.strip())
            _notify_user("Codex Audit Needs Input", question.strip())
        elif _latest_question_path().exists():
            _latest_question_path().unlink()
        if result_payload.get("status") == "error":
            _notify_user(
                "Codex Audit Error",
                str(result_payload.get("summary") or "The scheduled Codex audit failed."),
            )

        print(json.dumps(wrapped, indent=2))
        return exit_code


def cmd_install_launchd(label: str, *, weekday: int, hour: int, minute: int, run_now: bool) -> int:
    _housekeep_codex_audit_artifacts()
    plist_path = _plist_path(label)
    with plist_path.open("wb") as handle:
        plistlib.dump(_build_plist(label, weekday=weekday, hour=hour, minute=minute), handle, sort_keys=False)

    domain = _launchd_domain()
    _launchctl("bootout", domain, str(plist_path), check=False)
    _launchctl("bootstrap", domain, str(plist_path))
    _launchctl("enable", f"{domain}/{label}", check=False)
    if run_now:
        _launchctl("kickstart", "-k", f"{domain}/{label}")

    print(f"Installed LaunchAgent: {plist_path}")
    print(f"Label: {label}")
    print(f"Schedule: weekday {weekday} at {hour:02d}:{minute:02d}")
    print(f"Latest result: {_latest_result_path()}")
    return 0


def cmd_uninstall_launchd(label: str) -> int:
    plist_path = _plist_path(label)
    _launchctl("bootout", _launchd_domain(), str(plist_path), check=False)
    if plist_path.exists():
        plist_path.unlink()
    print(f"Removed LaunchAgent: {plist_path}")
    return 0


def cmd_status(label: str, *, verbose: bool) -> int:
    plist_path = _plist_path(label)
    print(f"Label: {label}")
    print(f"Repo: {REPO_ROOT}")
    print(f"Plist: {plist_path}")
    print(f"Latest result: {_latest_result_path()}")
    print(f"Latest question: {_latest_question_path()}")
    print(f"Latest stdout: {_latest_stdout_path()}")
    print(f"Latest stderr: {_latest_stderr_path()}")
    print(f"Stdout log: {_logs_dir() / 'codex-audit.stdout.log'}")
    print(f"Stderr log: {_logs_dir() / 'codex-audit.stderr.log'}")
    latest_run_dir = _latest_run_dir()
    if latest_run_dir:
        print(f"Latest run dir: {latest_run_dir}")
        if not (latest_run_dir / "metadata.json").exists():
            print("Latest run dir state: in progress or interrupted before metadata write")
    latest_payload = _latest_result_payload()
    if latest_payload:
        metadata = latest_payload.get("metadata") or {}
        result = latest_payload.get("result") or {}
        started_at = metadata.get("started_at") or "—"
        finished_at = metadata.get("finished_at") or "—"
        exit_code = metadata.get("exit_code", "—")
        status = result.get("status") or "—"
        print(f"Latest structured run start: {started_at}")
        print(f"Latest structured run end: {finished_at}")
        print(f"Latest structured exit code: {exit_code}")
        print(f"Latest structured status: {status}")
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
        if verbose:
            print()
            print(result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        print("Loaded: no")
        details = (exc.stderr or exc.stdout or "").strip()
        if details:
            print(details)
    health, detail = _summarize_codex_audit_health(
        installed=installed,
        loaded=loaded,
        state=state,
        latest_payload=latest_payload,
        latest_run_dir=latest_run_dir,
    )
    print(f"Health: {health}")
    print(f"Health detail: {detail}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the scheduled Codex repo-audit agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run-agent", help="Run the Codex repo-audit agent once")

    install = subparsers.add_parser("install-launchd", help="Install and load the weekly Codex audit job")
    install.add_argument("--label", default=DEFAULT_LABEL)
    install.add_argument("--weekday", type=_weekday_arg, default=1, help="launchd weekday (1=Monday)")
    install.add_argument("--hour", type=_hour_arg, default=12)
    install.add_argument("--minute", type=_minute_arg, default=0)
    install.add_argument("--run-now", action="store_true")

    uninstall = subparsers.add_parser("uninstall-launchd", help="Unload and remove the weekly Codex audit job")
    uninstall.add_argument("--label", default=DEFAULT_LABEL)

    status = subparsers.add_parser("status", help="Show Codex audit status")
    status.add_argument("--label", default=DEFAULT_LABEL)
    status.add_argument("--verbose", action="store_true")
    return parser


def _weekday_arg(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 7:
        raise argparse.ArgumentTypeError("weekday must be between 1 (Monday) and 7 (Sunday)")
    return parsed


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-agent":
        raise SystemExit(cmd_run_agent())
    if args.command == "install-launchd":
        raise SystemExit(
            cmd_install_launchd(
                args.label,
                weekday=args.weekday,
                hour=args.hour,
                minute=args.minute,
                run_now=args.run_now,
            )
        )
    if args.command == "uninstall-launchd":
        raise SystemExit(cmd_uninstall_launchd(args.label))
    if args.command == "status":
        raise SystemExit(cmd_status(args.label, verbose=args.verbose))
    raise SystemExit(2)


if __name__ == "__main__":
    main()
