"""
Unattended Monarch maintenance command.

Usage:
    uv run python -m monarch.maintain
    uv run python -m monarch.maintain --refresh
    uv run python -m monarch.maintain --dry-run
    uv run python -m monarch.maintain --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

from monarch.budgets import budget_month_summary, budget_watchlist
from monarch.client import get_client
from monarch.policies import load_merchant_overrides, match_merchant_override
from monarch.rules import fetch_rules
from monarch.transactions import (
    fetch_all_transactions,
    month_start_months_ago,
    transaction_account_name,
    transaction_category_name,
    transaction_merchant_name,
)

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


@dataclass
class Suggestion:
    transaction_id: str
    date: str
    merchant: str
    account: str
    amount: float
    current_category: str
    suggested_category_id: str | None
    suggested_category_name: str | None
    history_count: int
    confidence: float
    safe: bool
    reason: str
    source: str


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _style(text: str, code: str, *, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _month_range(month: str) -> tuple[str, str]:
    year, mon = month.split("-")
    first = date(int(year), int(mon), 1)
    if first.month == 12:
        next_first = date(first.year + 1, 1, 1)
    else:
        next_first = date(first.year, first.month + 1, 1)
    last = next_first.fromordinal(next_first.toordinal() - 1)
    return first.isoformat(), last.isoformat()


def _transaction_date(txn: dict[str, Any]) -> date | None:
    value = str(txn.get("date") or "")[:10]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _transaction_is_on_or_before(txn: dict[str, Any], cutoff: date) -> bool:
    parsed = _transaction_date(txn)
    return parsed is None or parsed <= cutoff


def _split_uncategorized_by_cutoff(
    transactions: list[dict[str, Any]],
    *,
    cutoff: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    future: list[dict[str, Any]] = []
    for txn in transactions:
        if transaction_category_name(txn) != "Uncategorized":
            continue
        if _transaction_is_on_or_before(txn, cutoff):
            current.append(txn)
        else:
            future.append(txn)
    return current, future


def _build_suggestions(
    target_transactions: list[dict[str, Any]],
    history_transactions: list[dict[str, Any]],
    *,
    min_history: int,
    min_confidence: float,
) -> list[Suggestion]:
    suggestions: list[Suggestion] = []
    overrides = load_merchant_overrides()

    for txn in target_transactions:
        merchant = transaction_merchant_name(txn)
        account = transaction_account_name(txn)
        current_category = transaction_category_name(txn)
        override = match_merchant_override(merchant, account, overrides)
        if override:
            suggestion = Suggestion(
                transaction_id=str(txn.get("id") or ""),
                date=str(txn.get("date") or "")[:10],
                merchant=merchant,
                account=account,
                amount=float(txn.get("amount") or 0),
                current_category=current_category,
                suggested_category_id=None,
                suggested_category_name=override.category,
                history_count=0,
                confidence=1.0,
                safe=True,
                reason="policy_override",
                source="needs_review" if txn.get("needsReview") else "uncategorized",
            )
            suggestions.append(suggestion)
            continue

        history = [
            item
            for item in history_transactions
            if item.get("id") != txn.get("id")
            and transaction_merchant_name(item) == merchant
            and transaction_account_name(item) == account
            and transaction_category_name(item) != "Uncategorized"
        ]

        counts: dict[str, dict[str, object]] = {}
        for item in history:
            category = item.get("category") or {}
            cid = category.get("id")
            cname = category.get("name")
            if not cid or not cname:
                continue
            bucket = counts.setdefault(cid, {"name": cname, "count": 0})
            bucket["count"] = int(bucket["count"]) + 1

        if counts:
            top_id, top = max(counts.items(), key=lambda entry: int(entry[1]["count"]))
            top_count = int(top["count"])
            confidence = top_count / len(history)
            safe = len(history) >= min_history and confidence >= min_confidence
            suggestion = Suggestion(
                transaction_id=str(txn.get("id") or ""),
                date=str(txn.get("date") or "")[:10],
                merchant=merchant,
                account=account,
                amount=float(txn.get("amount") or 0),
                current_category=current_category,
                suggested_category_id=top_id,
                suggested_category_name=str(top["name"]),
                history_count=len(history),
                confidence=confidence,
                safe=safe,
                reason="matched_history"
                if safe
                else _unsafe_history_reason(
                    history_count=len(history),
                    confidence=confidence,
                    min_history=min_history,
                    min_confidence=min_confidence,
                ),
                source="needs_review" if txn.get("needsReview") else "uncategorized",
            )
        else:
            suggestion = Suggestion(
                transaction_id=str(txn.get("id") or ""),
                date=str(txn.get("date") or "")[:10],
                merchant=merchant,
                account=account,
                amount=float(txn.get("amount") or 0),
                current_category=current_category,
                suggested_category_id=None,
                suggested_category_name=None,
                history_count=0,
                confidence=0.0,
                safe=False,
                reason="no_history",
                source="needs_review" if txn.get("needsReview") else "uncategorized",
            )
        suggestions.append(suggestion)

    suggestions.sort(
        key=lambda row: (
            not row.safe,
            row.reason,
            row.date,
            row.merchant.casefold(),
        )
    )
    return suggestions


def _unsafe_history_reason(
    *,
    history_count: int,
    confidence: float,
    min_history: int,
    min_confidence: float,
) -> str:
    lacks_history = history_count < min_history
    lacks_confidence = confidence < min_confidence
    if lacks_history and lacks_confidence:
        return "insufficient_history_and_confidence"
    if lacks_history:
        return "insufficient_history"
    return "insufficient_confidence"


async def _refresh_accounts_if_requested(do_refresh: bool) -> dict[str, Any]:
    if not do_refresh:
        return {"requested": False}

    mm = await get_client()
    accounts = await mm.get_accounts()
    account_ids = [
        acct["id"]
        for acct in accounts.get("accounts", [])
        if acct.get("isActive", True)
    ]
    success = await mm.request_accounts_refresh(account_ids)
    return {
        "requested": True,
        "account_count": len(account_ids),
        "success": bool(success),
    }


async def _collect_budget_details(month: str) -> tuple[list[dict[str, Any]], dict[str, float] | None]:
    mm = await get_client()
    start_str, end_str = _month_range(month)
    budgets = await mm.get_budgets(start_date=start_str, end_date=end_str)
    return budget_watchlist(budgets), budget_month_summary(budgets)


async def run_maintain(
    *,
    month: str,
    lookback_months: int,
    history_months: int,
    min_history: int,
    min_confidence: float,
    refresh: bool,
    dry_run: bool,
) -> dict[str, Any]:
    today = date.today()
    phase_timings: dict[str, float] = {}

    phase_started = _utc_now()
    refresh_info = await _refresh_accounts_if_requested(refresh)
    phase_timings["refresh_accounts"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    mm = await get_client()
    start_str, end_str = _month_range(month)

    phase_started = _utc_now()
    recent_transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(lookback_months, today=today),
        end_date=today.isoformat(),
    )
    phase_timings["fetch_recent_transactions"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    phase_started = _utc_now()
    history_transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(history_months, today=today),
        end_date=today.isoformat(),
    )
    phase_timings["fetch_history_transactions"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    phase_started = _utc_now()
    month_transactions = await fetch_all_transactions(
        mm,
        start_date=start_str,
        end_date=end_str,
    )
    phase_timings["fetch_month_transactions"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    phase_started = _utc_now()
    category_lookup = {
        str(category.get("id") or ""): str(category.get("name") or "")
        for category in (await mm.get_transaction_categories()).get("categories", [])
        if category.get("id") and category.get("name")
    }
    phase_timings["fetch_categories"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )
    category_name_to_id = {
        name.casefold(): category_id for category_id, name in category_lookup.items()
    }

    target_transactions = [
        txn
        for txn in recent_transactions
        if _transaction_is_on_or_before(txn, today)
        and (txn.get("needsReview") or transaction_category_name(txn) == "Uncategorized")
    ]

    suggestions = _build_suggestions(
        target_transactions,
        history_transactions,
        min_history=min_history,
        min_confidence=min_confidence,
    )
    safe_suggestions = [row for row in suggestions if row.safe]
    manual_suggestions = [row for row in suggestions if not row.safe]

    applied: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if not dry_run:
        phase_started = _utc_now()
        for row in safe_suggestions:
            try:
                category_id = row.suggested_category_id
                if not category_id and row.suggested_category_name:
                    category_id = category_name_to_id.get(row.suggested_category_name.casefold())
                if not category_id:
                    raise ValueError(
                        f"Could not resolve category id for {row.suggested_category_name!r}"
                    )
                await mm.update_transaction(
                    transaction_id=row.transaction_id,
                    category_id=category_id,
                    needs_review=False,
                )
                applied.append(
                    {
                        "transaction_id": row.transaction_id,
                        "merchant": row.merchant,
                        "category": row.suggested_category_name,
                        "source": row.source,
                    }
                )
            except Exception as exc:
                failures.append(
                    {
                        "transaction_id": row.transaction_id,
                        "merchant": row.merchant,
                        "error": str(exc),
                    }
                )
        phase_timings["apply_safe_suggestions"] = round(
            (_utc_now() - phase_started).total_seconds(), 3
        )
    else:
        phase_timings["apply_safe_suggestions"] = 0.0

    phase_started = _utc_now()
    final_recent = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(lookback_months, today=today),
        end_date=today.isoformat(),
    )
    phase_timings["fetch_final_recent_transactions"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    phase_started = _utc_now()
    final_month = await fetch_all_transactions(
        mm,
        start_date=start_str,
        end_date=end_str,
    )
    phase_timings["fetch_final_month_transactions"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )
    remaining_review = [txn for txn in final_recent if txn.get("needsReview")]
    remaining_uncategorized, future_uncategorized = _split_uncategorized_by_cutoff(
        final_month,
        cutoff=today,
    )

    phase_started = _utc_now()
    budget_watch, budget_summary = await _collect_budget_details(month)
    phase_timings["collect_budget_details"] = round(
        (_utc_now() - phase_started).total_seconds(), 3
    )

    phase_started = _utc_now()
    rules = await fetch_rules()
    phase_timings["fetch_rules"] = round((_utc_now() - phase_started).total_seconds(), 3)

    return {
        "month": month,
        "refresh": refresh_info,
        "dry_run": dry_run,
        "lookback_months": lookback_months,
        "history_months": history_months,
        "thresholds": {
            "min_history": min_history,
            "min_confidence": min_confidence,
        },
        "targets_scanned": len(target_transactions),
        "safe_suggestions": [asdict(row) for row in safe_suggestions],
        "manual_suggestions": [asdict(row) for row in manual_suggestions],
        "applied": applied,
        "failures": failures,
        "remaining_review_count": len(remaining_review),
        "remaining_uncategorized_count": len(remaining_uncategorized),
        "future_uncategorized_count": len(future_uncategorized),
        "budget_summary": budget_summary,
        "budget_watch": budget_watch[:8],
        "rule_count": len(rules),
        "phase_timings_seconds": phase_timings,
    }


def format_summary(summary: dict[str, Any], *, color: bool = True) -> str:
    lines: list[str] = []
    lines.append("")
    lines.append(_style(f"Monarch Maintain — {summary['month']}", BOLD, color=color))
    refresh = summary["refresh"]
    if refresh.get("requested"):
        status = _style(
            "requested" if refresh.get("success") else "failed",
            GREEN if refresh.get("success") else RED,
            color=color,
        )
        lines.append(
            f"  Refresh: {status} for {refresh.get('account_count', 0)} account(s)"
        )
    else:
        lines.append("  Refresh: skipped")

    mode = _style(
        "dry run" if summary["dry_run"] else "apply safe fixes",
        YELLOW if summary["dry_run"] else GREEN,
        color=color,
    )
    lines.append(
        f"  Mode: {mode} | Targets scanned: {summary['targets_scanned']} | "
        f"Safe: {len(summary['safe_suggestions'])} | Manual: {len(summary['manual_suggestions'])}"
    )

    lines.append("")
    lines.append(_style("Applied", BOLD, color=color))
    if not summary["applied"]:
        label = "No changes applied." if not summary["dry_run"] else "Dry run only."
        lines.append(f"  {_style(label, CYAN, color=color)}")
    else:
        for row in summary["applied"][:10]:
            lines.append(f"  {row['merchant']} -> {row['category']} [{row['source']}]")

    if summary["failures"]:
        lines.append("")
        lines.append(_style("Failures", BOLD, color=color))
        for row in summary["failures"][:10]:
            lines.append(f"  {row['merchant']}: {row['error']}")

    lines.append("")
    lines.append(_style("Still Needs Attention", BOLD, color=color))
    if not summary["manual_suggestions"]:
        lines.append(
            f"  {_style('No unresolved maintain suggestions.', GREEN, color=color)}"
        )
    else:
        for row in summary["manual_suggestions"][:10]:
            confidence = f"{row['confidence']:.0%}"
            amount = _money(abs(float(row["amount"])))
            target = row["suggested_category_name"] or "manual review"
            lines.append(
                f"  {row['date']}  {row['merchant']}  {amount}  "
                f"-> {target} ({confidence}, {row['reason']})"
            )

    lines.append("")
    lines.append(_style("Post-Run Status", BOLD, color=color))
    future_uncategorized_count = int(summary.get("future_uncategorized_count", 0) or 0)
    uncategorized_label = f"Uncategorized this month: {summary['remaining_uncategorized_count']}"
    if future_uncategorized_count:
        uncategorized_label += f" (+{future_uncategorized_count} future-dated)"
    lines.append(
        f"  Review queue: {summary['remaining_review_count']} | "
        f"{uncategorized_label} | "
        f"Rule count: {summary['rule_count']}"
    )
    budget_summary = summary.get("budget_summary")
    if budget_summary:
        planned_margin = float(budget_summary.get("planned_margin", 0) or 0)
        actual_net = float(budget_summary.get("actual_net", 0) or 0)
        planned_sign = "+" if planned_margin >= 0 else "-"
        actual_sign = "+" if actual_net >= 0 else "-"
        lines.append(
            f"  Budget margin: {planned_sign}{_money(abs(planned_margin))} | "
            f"Actual net so far: {actual_sign}{_money(abs(actual_net))}"
        )

    lines.append("")
    lines.append(_style("Budget Watchlist", BOLD, color=color))
    if not summary["budget_watch"]:
        lines.append(
            f"  {_style('No current budget anomalies detected.', GREEN, color=color)}"
        )
    else:
        for row in summary["budget_watch"]:
            if row["planned"] > 0:
                lines.append(
                    f"  {row['category']}: actual {_money(row['actual'])} vs planned {_money(row['planned'])}"
                )
            else:
                lines.append(
                    f"  {row['category']}: {_money(row['actual'])} with no budget set"
                )
    lines.append("")
    return "\n".join(lines)


def _print_summary(summary: dict[str, Any]) -> None:
    print(format_summary(summary), end="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run unattended Monarch maintenance")
    parser.add_argument("--month", default=_current_month(), metavar="YYYY-MM")
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=3,
        help="Recent window to scan for review and uncategorized items (default: 3)",
    )
    parser.add_argument(
        "--history-months",
        type=int,
        default=12,
        help="Historical window used to infer categories (default: 12)",
    )
    parser.add_argument(
        "--min-history",
        type=int,
        default=3,
        help="Minimum categorized history before auto-applying (default: 3)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Confidence threshold between 0 and 1 (default: 0.8)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Request an account refresh before maintenance",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not apply safe fixes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the final summary as JSON",
    )
    return parser


async def amain(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = await run_maintain(
            month=args.month,
            lookback_months=args.lookback_months,
            history_months=args.history_months,
            min_history=args.min_history,
            min_confidence=args.min_confidence,
            refresh=args.refresh,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{RED}Error:{RESET} {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_summary(summary)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
