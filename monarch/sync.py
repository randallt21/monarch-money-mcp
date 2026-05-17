"""
Read-only CLI for Monarch Money data.

Usage:
    uv run python -m monarch.sync accounts
    uv run python -m monarch.sync transactions [--month YYYY-MM] [--uncategorized] [--limit N]
    uv run python -m monarch.sync budgets [--month YYYY-MM]
    uv run python -m monarch.sync cashflow [--month YYYY-MM]
    uv run python -m monarch.sync categories
    uv run python -m monarch.sync review
"""

import argparse
import asyncio
import sys
from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime

from monarch.budgets import (
    budget_category_rows,
    budget_month_summary,
    budget_watchlist,
    count_nonzero_transfer_rows,
)
from monarch.client import get_client
from monarch.rules import fetch_rules
from monarch.transactions import (
    fetch_all_transactions,
    month_start_months_ago,
    transaction_account_name,
    transaction_category_name,
    transaction_merchant_name,
)

# ANSI color codes
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _month_range(month_str: str) -> tuple[str, str]:
    """Return (start_date, end_date) as YYYY-MM-DD strings for a YYYY-MM month."""
    year, month = int(month_str[:4]), int(month_str[5:7])
    last_day = monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _parse_date(date_str: str) -> date:
    return datetime.strptime(date_str, "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _col(text: str, width: int, align: str = "<") -> str:
    """Return text padded/truncated to exactly `width` characters."""
    text = str(text)
    if len(text) > width:
        text = text[: width - 1] + "…"
    return f"{text:{align}{width}}"


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _signed_money(amount: float) -> str:
    sign = "+" if amount >= 0 else "-"
    return f"{sign}{_money(abs(amount))}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_accounts() -> None:
    mm = await get_client()
    data = await mm.get_accounts()

    accounts = data.get("accounts", [])
    if not accounts:
        print("No accounts found.")
        return

    header = (
        f"  {_col('Name', 32)}  {_col('Type', 14)}  {_col('Balance', 14, '>')}  "
        f"{_col('Last Sync', 12)}  {_col('Status', 8)}"
    )
    separator = "-" * len(header)
    print(f"\n{BOLD}Accounts{RESET}")
    print(separator)
    print(header)
    print(separator)

    for acct in sorted(accounts, key=lambda a: a.get("displayName", "")):
        name = acct.get("displayName") or acct.get("name", "")
        acct_type = acct.get("type", {}).get("name", "") if isinstance(acct.get("type"), dict) else acct.get("type", "")
        balance = acct.get("currentBalance", 0) or 0
        last_sync_raw = acct.get("syncedAt") or acct.get("lastSyncedAt") or ""
        if last_sync_raw:
            try:
                last_sync = last_sync_raw[:10]
            except Exception:
                last_sync = str(last_sync_raw)[:10]
        else:
            last_sync = "—"
        is_active = acct.get("isActive", True)
        status = f"{GREEN}active{RESET}" if is_active else f"{YELLOW}inactive{RESET}"

        print(
            f"  {_col(name, 32)}  {_col(acct_type, 14)}  "
            f"{_col(_money(balance), 14, '>')}  {_col(last_sync, 12)}  {status}"
        )

    print(separator)
    total = sum(a.get("currentBalance", 0) or 0 for a in accounts)
    print(f"  {'Total':>62}  {_col(_money(total), 14, '>')}")
    print()


async def cmd_transactions(month: str, uncategorized: bool, limit: int) -> None:
    mm = await get_client()
    start_str, end_str = _month_range(month)

    data = await mm.get_transactions(
        start_date=start_str,
        end_date=end_str,
        limit=limit,
    )

    transactions = data.get("allTransactions", {}).get("results", [])

    if uncategorized:
        transactions = [
            t for t in transactions
            if not t.get("category") or t.get("category", {}).get("name") == "Uncategorized"
        ]

    if not transactions:
        print(f"No transactions found for {month}.")
        return

    header = (
        f"  {_col('Date', 10)}  {_col('Merchant', 30)}  "
        f"{_col('Category', 24)}  {_col('Amount', 12, '>')}  {_col('Account', 20)}"
    )
    separator = "-" * len(header)
    print(f"\n{BOLD}Transactions — {month}{RESET}")
    if uncategorized:
        print(f"  {YELLOW}Filtered: uncategorized only{RESET}")
    print(separator)
    print(header)
    print(separator)

    for txn in transactions:
        txn_date = str(txn.get("date", ""))[:10]
        merchant = txn.get("merchant", {}).get("name", "") if txn.get("merchant") else txn.get("name", "")
        category = txn.get("category", {}).get("name", "—") if txn.get("category") else "—"
        amount = txn.get("amount", 0) or 0
        account_name = txn.get("account", {}).get("displayName", "") if txn.get("account") else ""

        # In the raw transaction feed, outflows are typically negative and inflows positive.
        amount_str = _money(abs(amount))
        color = RED if amount < 0 else GREEN
        colored_amount = f"{color}{amount_str}{RESET}"
        sign = "-" if amount < 0 else "+"

        needs_review = txn.get("needsReview", False)
        flag = f" {YELLOW}[review]{RESET}" if needs_review else ""

        print(
            f"  {_col(txn_date, 10)}  {_col(merchant, 30)}  "
            f"{_col(category, 24)}  {sign}{colored_amount:>{12}}  "
            f"{_col(account_name, 20)}{flag}"
        )

    print(separator)
    print(f"  {len(transactions)} transaction(s) shown")
    print()


async def cmd_budgets(month: str) -> None:
    mm = await get_client()
    start_str, end_str = _month_range(month)

    try:
        data = await mm.get_budgets(start_date=start_str, end_date=end_str)
    except Exception as e:
        if "None" in str(e):
            print("No budgets configured in your Monarch Money account.")
            return
        raise

    rows = budget_category_rows(data, include_transfers=False, nonzero_only=True)
    skipped_transfer_rows = count_nonzero_transfer_rows(data)
    summary = budget_month_summary(data)
    if not rows:
        print(f"No budget data available for {month}.")
        return

    header = (
        f"  {_col('Group', 22)}  {_col('Category', 28)}  "
        f"{_col('Planned', 12, '>')}  {_col('Actual', 12, '>')}  {_col('Diff', 12, '>')}"
    )
    separator = "-" * len(header)
    print(f"\n{BOLD}Budgets — {month}{RESET}")
    if skipped_transfer_rows:
        print(f"  {CYAN}Excluded {skipped_transfer_rows} transfer row(s) from this budget view{RESET}")
    if summary:
        planned_margin_color = GREEN if summary["planned_margin"] >= 0 else RED
        actual_net_color = GREEN if summary["actual_net"] >= 0 else RED
        remaining_margin_color = GREEN if summary["remaining_margin"] >= 0 else RED
        print()
        print(f"  {BOLD}Summary{RESET}")
        print(
            f"  Planned income {_money(summary['planned_income'])} | "
            f"Budgeted expenses {_money(summary['planned_expenses'])} | "
            f"Planned margin {planned_margin_color}{_signed_money(summary['planned_margin'])}{RESET}"
        )
        print(
            f"  Actual income {_money(summary['actual_income'])} | "
            f"Actual expenses {_money(summary['actual_expenses'])} | "
            f"Actual net {actual_net_color}{_signed_money(summary['actual_net'])}{RESET}"
        )
        print(
            f"  Remaining income {_money(summary['remaining_income'])} | "
            f"Remaining expense budget {_money(summary['remaining_expenses'])} | "
            f"Remaining margin {remaining_margin_color}{_signed_money(summary['remaining_margin'])}{RESET}"
        )
    print(separator)
    print(header)
    print(separator)

    last_group = ""

    visible_planned = 0.0
    visible_actual = 0.0
    for row in rows:
        group_name = row.group_name
        category_name = row.category_name
        planned = row.planned
        actual = row.actual
        if group_name != last_group and group_name:
            print(f"  {CYAN}{BOLD}{group_name}{RESET}")
            last_group = group_name

        diff = planned - actual  # positive = under budget (good for expenses)
        if planned == 0:
            diff_str = "—"
            color = RESET
        elif diff < 0:
            diff_str = f"-{_money(abs(diff))}"
            color = RED
        else:
            diff_str = f"+{_money(diff)}"
            color = GREEN

        print(
            f"  {_col('', 22)}  {_col(category_name, 28)}  "
            f"{_col(_money(planned), 12, '>')}  {_col(_money(actual), 12, '>')}  "
            f"{color}{_col(diff_str, 12, '>')}{RESET}"
        )
        visible_planned += planned
        visible_actual += actual

    print(separator)
    if summary and (
        abs(visible_planned - summary["planned_expenses"]) > 0.01
        or abs(visible_actual - summary["actual_expenses"]) > 0.01
    ):
        print(
            f"  {CYAN}Note:{RESET} visible category rows sum to "
            f"{_money(visible_planned)} planned / {_money(visible_actual)} actual, "
            f"while Monarch's official expense totals are "
            f"{_money(summary['planned_expenses'])} planned / {_money(summary['actual_expenses'])} actual."
        )
    print()


async def cmd_cashflow(month: str) -> None:
    mm = await get_client()
    start_str, end_str = _month_range(month)

    data = await mm.get_cashflow(start_date=start_str, end_date=end_str)

    # API returns byCategoryGroup with nested groupBy.categoryGroup and summary.sum
    by_group = data.get("byCategoryGroup", [])
    summary_list = data.get("summary", [])

    header = (
        f"  {_col('Category Group', 32)}  {_col('Type', 10)}  "
        f"{_col('Amount', 14, '>')}"
    )
    separator = "-" * len(header)
    print(f"\n{BOLD}Cashflow — {month}{RESET}")
    print(separator)
    print(header)
    print(separator)

    if by_group:
        for entry in by_group:
            grp = entry.get("groupBy", {}).get("categoryGroup", {})
            name = grp.get("name", "Unknown")
            grp_type = grp.get("type", "")
            amount = float(entry.get("summary", {}).get("sum", 0))
            color = GREEN if amount >= 0 else RED
            print(
                f"  {_col(name, 32)}  {_col(grp_type, 10)}  "
                f"{color}{_col(_money(amount), 14, '>')}{RESET}"
            )

    if summary_list:
        print(separator)
        s = summary_list[0].get("summary", {})
        income = float(s.get("sumIncome", 0))
        expenses = float(s.get("sumExpense", 0))
        savings = float(s.get("savings", 0))
        rate = float(s.get("savingsRate", 0))
        print(f"  {BOLD}Income:{RESET}    {GREEN}{_money(income)}{RESET}")
        print(f"  {BOLD}Expenses:{RESET}  {RED}{_money(expenses)}{RESET}")
        print(f"  {BOLD}Savings:{RESET}   {GREEN}{_money(savings)}{RESET}  ({rate:.1%} savings rate)")

    print()


async def cmd_categories() -> None:
    mm = await get_client()
    data = await mm.get_transaction_categories()

    # API returns flat list of categories, each with a nested group object
    cats = data.get("categories", [])
    if not cats:
        print("No categories found.")
        return

    # Group by category group
    by_group: dict[str, list[dict]] = {}
    for cat in cats:
        grp = cat.get("group", {})
        grp_name = grp.get("name", "Unknown")
        grp_type = grp.get("type", "")
        key = f"{grp_name} ({grp_type})"
        by_group.setdefault(key, []).append(cat)

    print(f"\n{BOLD}Transaction Categories{RESET}")
    print("-" * 70)

    for group_label, group_cats in sorted(by_group.items()):
        print(f"\n  {CYAN}{BOLD}{group_label}{RESET}")
        for cat in sorted(group_cats, key=lambda c: c.get("order", 0)):
            cat_id = cat.get("id", "")
            cat_name = cat.get("name", "")
            disabled = " (disabled)" if cat.get("isDisabled") else ""
            print(f"    {_col(cat_name, 34)}  {cat_id}{disabled}")

    print()


async def cmd_review() -> None:
    mm = await get_client()
    today = date.today()
    transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(3, today=today),
        end_date=today.isoformat(),
    )
    flagged = [t for t in transactions if t.get("needsReview")]

    if not flagged:
        print(f"\n{GREEN}No transactions flagged for review.{RESET}\n")
        return

    header = (
        f"  {_col('Date', 10)}  {_col('Merchant', 32)}  "
        f"{_col('Category', 22)}  {_col('Amount', 12, '>')}  {_col('ID', 22)}"
    )
    separator = "-" * len(header)
    print(f"\n{BOLD}Transactions Needing Review{RESET}  ({len(flagged)} found)")
    print(separator)
    print(header)
    print(separator)

    for txn in flagged:
        txn_date = str(txn.get("date", ""))[:10]
        merchant = txn.get("merchant", {}).get("name", "") if txn.get("merchant") else txn.get("name", "")
        category = txn.get("category", {}).get("name", "—") if txn.get("category") else "—"
        amount = txn.get("amount", 0) or 0
        txn_id = txn.get("id", "")
        amount_str = _money(abs(amount))
        color = RED if amount < 0 else GREEN
        sign = "-" if amount < 0 else "+"
        print(
            f"  {_col(txn_date, 10)}  {_col(merchant, 32)}  "
            f"{_col(category, 22)}  {sign}{color}{amount_str}{RESET}  {_col(txn_id, 22)}"
        )

    print(separator)
    print()


async def cmd_rule_candidates(
    months: int,
    min_history: int,
    min_confidence: float,
    limit: int,
) -> None:
    mm = await get_client()
    today = date.today()
    transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(months, today=today),
        end_date=today.isoformat(),
    )

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for txn in transactions:
        merchant = transaction_merchant_name(txn)
        account = transaction_account_name(txn)
        if merchant:
            grouped[(merchant, account)].append(txn)

    candidates: list[dict[str, object]] = []
    ambiguous: list[dict[str, object]] = []

    for (merchant, account), txns in grouped.items():
        unresolved = [
            txn
            for txn in txns
            if txn.get("needsReview") or transaction_category_name(txn) == "Uncategorized"
        ]
        if not unresolved:
            continue

        categorized = [
            txn for txn in txns if transaction_category_name(txn) != "Uncategorized"
        ]
        if not categorized:
            ambiguous.append(
                {
                    "merchant": merchant,
                    "account": account,
                    "unresolved": len(unresolved),
                    "top_categories": "no categorized history",
                }
            )
            continue

        category_counts = Counter(transaction_category_name(txn) for txn in categorized)
        top_category, top_count = category_counts.most_common(1)[0]
        confidence = top_count / len(categorized)
        row = {
            "merchant": merchant,
            "account": account,
            "unresolved": len(unresolved),
            "history": len(categorized),
            "suggested_category": top_category,
            "confidence": confidence,
            "top_categories": ", ".join(
                f"{name}:{count}" for name, count in category_counts.most_common(3)
            ),
        }

        if len(categorized) >= min_history and confidence >= min_confidence:
            candidates.append(row)
        else:
            ambiguous.append(row)

    candidates.sort(
        key=lambda row: (
            int(row["unresolved"]),
            float(row["confidence"]),
            int(row["history"]),
        ),
        reverse=True,
    )
    ambiguous.sort(
        key=lambda row: (
            int(row["unresolved"]),
            int(row.get("history", 0)),
        ),
        reverse=True,
    )

    print(f"\n{BOLD}Rule Candidates{RESET}  ({months} month lookback)")
    print(
        f"  Thresholds: min history {min_history}, min confidence {min_confidence:.0%}"
    )

    if not candidates:
        print(f"  {YELLOW}No strong candidates found with current thresholds.{RESET}")
    else:
        header = (
            f"  {_col('Merchant', 32)}  {_col('Account', 24)}  "
            f"{_col('Unresolved', 10, '>')}  {_col('History', 8, '>')}  "
            f"{_col('Suggested Category', 24)}  {_col('Confidence', 10, '>')}"
        )
        separator = "-" * len(header)
        print(separator)
        print(header)
        print(separator)
        for row in candidates[:limit]:
            confidence_text = f"{float(row['confidence']):.0%}"
            print(
                f"  {_col(str(row['merchant']), 32)}  {_col(str(row['account']), 24)}  "
                f"{_col(str(row['unresolved']), 10, '>')}  {_col(str(row['history']), 8, '>')}  "
                f"{_col(str(row['suggested_category']), 24)}  {_col(confidence_text, 10, '>')}"
            )
        print(separator)

    if ambiguous:
        print(f"\n{BOLD}Needs Manual Rule Design{RESET}")
        for row in ambiguous[:limit]:
            history = row.get("history", 0)
            confidence = row.get("confidence")
            confidence_str = (
                f", confidence {float(confidence):.0%}" if confidence is not None else ""
            )
            print(
                f"  {row['merchant']} [{row['account']}] -> unresolved {row['unresolved']}, "
                f"history {history}{confidence_str}; categories: {row['top_categories']}"
            )

    print(f"\n{BOLD}Suggested next step in Monarch{RESET}")
    for row in candidates[: min(5, len(candidates))]:
        print(
            f"  If merchant contains '{row['merchant']}'"
            f" and account is '{row['account']}', set category to "
            f"'{row['suggested_category']}' and auto-review."
        )
    print()


async def cmd_dashboard(month: str) -> None:
    mm = await get_client()
    today = date.today()
    start_str, end_str = _month_range(month)

    month_transactions = await fetch_all_transactions(
        mm,
        start_date=start_str,
        end_date=end_str,
    )
    review_transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(3, today=today),
        end_date=today.isoformat(),
    )
    review_transactions = [txn for txn in review_transactions if txn.get("needsReview")]
    uncategorized = [
        txn for txn in month_transactions
        if transaction_category_name(txn) == "Uncategorized"
    ]

    budgets = await mm.get_budgets(start_date=start_str, end_date=end_str)
    budget_watch = budget_watchlist(budgets)
    budget_summary = budget_month_summary(budgets)

    rules = await fetch_rules()
    hot_rules = sorted(
        [rule for rule in rules if (rule.get("recentApplicationCount") or 0) > 0],
        key=lambda rule: (
            int(rule.get("recentApplicationCount") or 0),
            str(rule.get("lastAppliedAt") or ""),
        ),
        reverse=True,
    )

    print(f"\n{BOLD}Monarch Dashboard — {month}{RESET}")
    print(
        f"  Review queue: {len(review_transactions)} | "
        f"Uncategorized this month: {len(uncategorized)} | "
        f"Rule count: {len(rules)}"
    )

    print(f"\n{BOLD}Review Queue{RESET}")
    if not review_transactions:
        print(f"  {GREEN}No transactions currently need review.{RESET}")
    else:
        for txn in review_transactions[:5]:
            print(
                f"  {str(txn.get('date', ''))[:10]}  "
                f"{transaction_merchant_name(txn)}  "
                f"{_money(abs(float(txn.get('amount') or 0)))}  "
                f"[{transaction_account_name(txn)}]"
            )

    print(f"\n{BOLD}Uncategorized This Month{RESET}")
    if not uncategorized:
        print(f"  {GREEN}No uncategorized transactions this month.{RESET}")
    else:
        for txn in uncategorized[:8]:
            print(
                f"  {str(txn.get('date', ''))[:10]}  "
                f"{transaction_merchant_name(txn)}  "
                f"{_money(abs(float(txn.get('amount') or 0)))}  "
                f"[{transaction_account_name(txn)}]"
            )

    print(f"\n{BOLD}Budget Watchlist{RESET}")
    if budget_summary:
        planned_margin_color = GREEN if budget_summary["planned_margin"] >= 0 else RED
        actual_net_color = GREEN if budget_summary["actual_net"] >= 0 else RED
        print(
            f"  Planned margin: {planned_margin_color}{_signed_money(budget_summary['planned_margin'])}{RESET} | "
            f"Actual net so far: {actual_net_color}{_signed_money(budget_summary['actual_net'])}{RESET}"
        )
    if not budget_watch:
        print(f"  {GREEN}No current budget anomalies detected.{RESET}")
    else:
        for item in budget_watch[:8]:
            name = str(item["category"])
            planned = float(item["planned"])
            actual = float(item["actual"])
            if planned > 0:
                delta = actual - planned
                print(
                    f"  {name}: actual {_money(actual)} vs planned {_money(planned)} "
                    f"({RED}+{_money(delta)}{RESET})"
                )
            else:
                print(
                    f"  {name}: {_money(actual)} with no budget set"
                )

    print(f"\n{BOLD}Active Rules{RESET}")
    if not hot_rules:
        print("  No recently applied rules.")
    else:
        for rule in hot_rules[:8]:
            merchant_bits = rule.get("merchantCriteria") or []
            merchant_label = (
                f"{merchant_bits[0].get('operator')}:{merchant_bits[0].get('value')}"
                if merchant_bits else "(other criteria)"
            )
            category = (rule.get("setCategoryAction") or {}).get("name") or "—"
            print(
                f"  #{rule.get('order', 0)} {merchant_label} -> {category} "
                f"(recent {rule.get('recentApplicationCount', 0)})"
            )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m monarch.sync",
        description="Read-only Monarch Money CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("accounts", help="List all accounts with balances")

    txn_p = sub.add_parser("transactions", help="List transactions")
    txn_p.add_argument("--month", default=_current_month(), metavar="YYYY-MM",
                       help="Month to query (default: current month)")
    txn_p.add_argument("--uncategorized", action="store_true",
                       help="Show only uncategorized transactions")
    txn_p.add_argument("--limit", type=int, default=500,
                       help="Maximum transactions to fetch (default: 500)")

    budget_p = sub.add_parser("budgets", help="Show budgets with planned vs actual")
    budget_p.add_argument("--month", default=_current_month(), metavar="YYYY-MM",
                          help="Month to query (default: current month)")

    cf_p = sub.add_parser("cashflow", help="Show cashflow summary by category group")
    cf_p.add_argument("--month", default=_current_month(), metavar="YYYY-MM",
                      help="Month to query (default: current month)")

    sub.add_parser("categories", help="List all categories grouped by category group")
    sub.add_parser("review", help="Show transactions flagged as needing review")
    dashboard_p = sub.add_parser("dashboard", help="Show a daily status summary")
    dashboard_p.add_argument("--month", default=_current_month(), metavar="YYYY-MM",
                             help="Month to summarize (default: current month)")

    rules_p = sub.add_parser(
        "rule-candidates",
        help="Suggest Monarch rules from repeated review/uncategorized patterns",
    )
    rules_p.add_argument(
        "--months",
        type=int,
        default=6,
        help="Months of history to analyze (default: 6)",
    )
    rules_p.add_argument(
        "--min-history",
        type=int,
        default=3,
        help="Minimum categorized history before suggesting a rule (default: 3)",
    )
    rules_p.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Dominant category confidence threshold between 0 and 1 (default: 0.8)",
    )
    rules_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum candidates to display per section (default: 10)",
    )

    return parser


async def async_main(args: argparse.Namespace) -> None:
    try:
        if args.command == "accounts":
            await cmd_accounts()
        elif args.command == "transactions":
            await cmd_transactions(
                month=args.month,
                uncategorized=args.uncategorized,
                limit=args.limit,
            )
        elif args.command == "budgets":
            await cmd_budgets(month=args.month)
        elif args.command == "cashflow":
            await cmd_cashflow(month=args.month)
        elif args.command == "categories":
            await cmd_categories()
        elif args.command == "review":
            await cmd_review()
        elif args.command == "dashboard":
            await cmd_dashboard(month=args.month)
        elif args.command == "rule-candidates":
            await cmd_rule_candidates(
                months=args.months,
                min_history=args.min_history,
                min_confidence=args.min_confidence,
                limit=args.limit,
            )
    except RuntimeError as exc:
        print(f"{RED}Error:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"{RED}Unexpected error:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
