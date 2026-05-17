"""
Lively emoji digest of the latest Monarch maintenance run.

Usage:
    uv run python -m monarch.digest
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any


def _support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "monarch-money-mcp" / "maintain"


def _load_latest() -> dict[str, Any] | None:
    path = _support_dir() / "latest-summary.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_latest_error() -> str | None:
    path = _support_dir() / "latest-error.txt"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _money(amount: float) -> str:
    return f"${abs(amount):,.2f}"


def _pct(value: float) -> str:
    return f"{value:.0%}"


_CATEGORY_EMOJI: dict[str, str] = {
    "groceries": "🛒",
    "restaurants": "🍽️",
    "dining": "🍽️",
    "coffee": "☕",
    "travel": "✈️",
    "vacation": "🏖️",
    "gas": "⛽",
    "fuel": "⛽",
    "shopping": "🛍️",
    "amazon": "📦",
    "entertainment": "🎬",
    "subscription": "📺",
    "utilities": "💡",
    "insurance": "🛡️",
    "health": "🏥",
    "medical": "🏥",
    "pharmacy": "💊",
    "income": "💰",
    "paycheck": "💸",
    "salary": "💸",
    "rent": "🏠",
    "mortgage": "🏠",
    "pets": "🐾",
    "clothing": "👗",
    "gym": "💪",
    "fitness": "💪",
    "personal care": "🧴",
    "education": "📚",
    "transfer": "↔️",
    "savings": "🏦",
    "investment": "📈",
    "charity": "❤️",
    "gifts": "🎁",
}


def _cat_emoji(category_name: str | None) -> str:
    if not category_name:
        return "🏷️"
    lower = category_name.lower()
    for key, emoji in _CATEGORY_EMOJI.items():
        if key in lower:
            return emoji
    return "🏷️"


def _budget_bar(actual: float, planned: float, width: int = 12) -> str:
    if planned <= 0:
        return "░" * width
    ratio = min(actual / planned, 1.5)
    filled = round(ratio * width)
    if ratio > 1.1:
        bar = "█" * min(filled, width) + "▓" * max(0, filled - width)
        return f"\033[31m{bar[:width]}\033[0m"
    if ratio > 0.8:
        return f"\033[33m{'█' * filled}{'░' * (width - filled)}\033[0m"
    return f"\033[32m{'█' * filled}{'░' * (width - filled)}\033[0m"


def render_digest(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    sched = summary.get("scheduler", {})
    inner = summary.get("summary", {})

    month = inner.get("month", "?")
    finished_at = sched.get("finished_at", "")[:16].replace("T", " ")
    dry_run = inner.get("dry_run", False)
    mode_tag = " [DRY RUN]" if dry_run else ""

    lines.append("")
    lines.append(f"  💚 Monarch Auto-Pilot — {month}{mode_tag}")
    lines.append(f"     Run finished: {finished_at}")
    lines.append("")

    # --- Applied changes ---
    applied = inner.get("applied", [])
    llm_applied = [a for a in applied if a.get("reason") == "llm_guess"]
    hist_applied = [a for a in applied if a.get("reason") != "llm_guess"]

    if applied:
        lines.append(f"  ✅ Auto-sorted {len(applied)} transaction(s):")
        for row in hist_applied[:8]:
            emoji = _cat_emoji(row.get("category"))
            lines.append(f"       {emoji}  {row['merchant']}  →  {row['category']}")
        if llm_applied:
            lines.append(f"       🤖  (+ {len(llm_applied)} LLM best-guess — glance & correct if wrong)")
            for row in llm_applied[:4]:
                emoji = _cat_emoji(row.get("category"))
                lines.append(f"           {emoji}  {row['merchant']}  →  {row['category']}")
    else:
        if dry_run:
            lines.append("  📋 Dry run — no changes applied")
        else:
            lines.append("  ✅ All clear — nothing new to categorize")
    lines.append("")

    # --- Rules created ---
    new_rules = inner.get("new_rules", [])
    actually_created = [r for r in new_rules if r.get("created") or r.get("dry_run")]
    if actually_created:
        label = "rule(s) staged" if dry_run else "rule(s) created"
        lines.append(f"  📐 {len(actually_created)} {label} (future transactions auto-handled):")
        for rule in actually_created[:6]:
            emoji = _cat_emoji(rule.get("category_name"))
            lines.append(f"       {emoji}  '{rule['merchant']}'  →  {rule['category_name']}")
        lines.append("")

    # --- Needs attention ---
    manual = inner.get("manual_suggestions", [])
    review_count = inner.get("remaining_review_count", 0)
    needs_something = bool(manual) or review_count > 0

    if needs_something:
        lines.append(f"  ⚠️  Needs your eye ({len(manual)} to sort, {review_count} in review queue):")
        for row in manual[:6]:
            emoji = _cat_emoji(row.get("suggested_category_name"))
            amount = _money(abs(float(row.get("amount") or 0)))
            target = row.get("suggested_category_name") or "uncategorized"
            reason = row.get("reason", "?")
            lines.append(
                f"       {emoji}  {row['date']}  {row['merchant']}  {amount}"
                f"  →  {target}  ({reason})"
            )
        lines.append("")
    else:
        lines.append("  🎉 Review queue is clear!")
        lines.append("")

    # --- Budget watchlist ---
    budget_watch = inner.get("budget_watch", [])
    budget_summary = inner.get("budget_summary")
    if budget_summary:
        planned_margin = float(budget_summary.get("planned_margin", 0) or 0)
        actual_net = float(budget_summary.get("actual_net", 0) or 0)
        net_sign = "+" if actual_net >= 0 else "-"
        margin_sign = "+" if planned_margin >= 0 else "-"
        lines.append(
            f"  💰 Net this month: {net_sign}{_money(actual_net)}  "
            f"(budget margin: {margin_sign}{_money(planned_margin)})"
        )

    if budget_watch:
        lines.append("  📊 Budget watch:")
        for row in budget_watch[:6]:
            actual = float(row.get("actual") or 0)
            planned = float(row.get("planned") or 0)
            bar = _budget_bar(actual, planned)
            if planned > 0:
                pct = _pct(actual / planned)
                lines.append(
                    f"       {bar}  {row['category']}: {_money(actual)} / {_money(planned)} ({pct})"
                )
            else:
                lines.append(
                    f"       {bar}  {row['category']}: {_money(actual)} (no budget set)"
                )
    lines.append("")

    # --- Stats footer ---
    rule_count = inner.get("rule_count", 0)
    llm_count = inner.get("llm_guesses_count", 0)
    timings = inner.get("phase_timings_seconds", {})
    total_time = round(sum(timings.values()), 1)
    lines.append(
        f"  📈 {rule_count} active rules  •  {llm_count} merchant(s) classified by AI  "
        f"•  ran in {total_time}s"
    )
    lines.append("")
    return "\n".join(lines)


def render_error(error_text: str) -> str:
    lines = [
        "",
        "  ❌ Last Monarch run FAILED",
        "",
    ]
    for line in error_text.splitlines()[-8:]:
        lines.append(f"     {line}")
    lines.append("")
    lines.append("  Run: uv run python -m monarch.scheduler status")
    lines.append("")
    return "\n".join(lines)


async def amain() -> int:
    latest = _load_latest()
    error_text = _load_latest_error()

    if error_text and not latest:
        print(render_error(error_text))
        return 1

    if error_text and latest:
        sched = latest.get("scheduler", {})
        error_time = ""
        if Path(_support_dir() / "latest-error.txt").exists():
            stat = (_support_dir() / "latest-error.txt").stat()
            import datetime
            error_time = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()[:16]
        if error_time > sched.get("finished_at", "")[:16]:
            print(render_error(error_text))
            print("  (Previous successful run shown below)")
            print(render_digest(latest))
            return 1

    if not latest:
        print()
        print("  ⚪ No maintain run recorded yet.")
        print("  Run: uv run python -m monarch.scheduler run-job")
        print()
        return 0

    print(render_digest(latest))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
