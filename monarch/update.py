"""
Write CLI for Monarch Money data mutations.

Usage:
    uv run python -m monarch.update categorize <transaction_id> <category_name_or_id>
    uv run python -m monarch.update budget <category_name_or_id> <amount> [--from YYYY-MM-DD] [--future]
    uv run python -m monarch.update bulk-categorize <merchant_pattern> <category_name_or_id>
    uv run python -m monarch.update refresh
"""

import argparse
import asyncio
import sys
from datetime import date, datetime

from monarchmoney import MonarchMoney

from monarch.client import get_client
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
# Category resolution helpers
# ---------------------------------------------------------------------------

def _money(amount: float) -> str:
    return f"${amount:,.2f}"


async def _fetch_categories(mm: MonarchMoney) -> list[dict]:
    """Return a flat list of all category dicts from the API."""
    data = await mm.get_transaction_categories()
    # API returns flat list of categories, each with a nested group object
    return data.get("categories", [])


def _resolve_category(
    categories: list[dict],
    name_or_id: str,
) -> dict:
    """
    Resolve a category by exact ID, exact name, or case-insensitive substring.

    Returns the matched category dict.
    Raises SystemExit if ambiguous or not found.
    """
    # Exact ID match first
    for cat in categories:
        if cat.get("id") == name_or_id:
            return cat

    # Exact case-insensitive name match
    needle = name_or_id.lower()
    exact = [c for c in categories if c.get("name", "").lower() == needle]
    if len(exact) == 1:
        return exact[0]

    # Substring match as fallback
    matches = [c for c in categories if needle in c.get("name", "").lower()]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        print(
            f"{YELLOW}Ambiguous category '{name_or_id}'. Multiple matches:{RESET}",
            file=sys.stderr,
        )
        for m in matches:
            print(f"  {m['id']}  {m['name']}", file=sys.stderr)
        print("Please be more specific.", file=sys.stderr)
        sys.exit(1)

    print(f"{RED}Category not found:{RESET} '{name_or_id}'", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_categorize(transaction_id: str, name_or_id: str) -> None:
    mm = await get_client()
    categories = await _fetch_categories(mm)
    cat = _resolve_category(categories, name_or_id)

    result = await mm.update_transaction(
        transaction_id=transaction_id,
        category_id=cat["id"],
    )

    txn = (
        result.get("updateTransaction", {})
        .get("transaction", result)
    )
    print(
        f"{GREEN}Updated:{RESET} transaction {transaction_id} "
        f"-> category '{cat['name']}' ({cat['id']})"
    )
    merchant = ""
    if isinstance(txn, dict):
        merchant = (txn.get("merchant") or {}).get("name") or txn.get("name", "")
    if merchant:
        print(f"  Merchant: {merchant}")


async def cmd_budget(
    name_or_id: str,
    amount: float,
    from_date: str | None,
    apply_to_future: bool,
) -> None:
    mm = await get_client()
    categories = await _fetch_categories(mm)
    cat = _resolve_category(categories, name_or_id)

    start_date: date | None = None
    if from_date:
        start_date = from_date
    else:
        today = date.today()
        start_date = today.replace(day=1).isoformat()

    await mm.set_budget_amount(
        amount=amount,
        category_id=cat["id"],
        timeframe="month",
        start_date=start_date,
        apply_to_future=apply_to_future,
    )

    future_note = " (and all future months)" if apply_to_future else ""
    print(
        f"{GREEN}Budget set:{RESET} '{cat['name']}' -> ${amount:,.2f}/month "
        f"starting {start_date}{future_note}"
    )


async def cmd_bulk_categorize(
    merchant_pattern: str,
    name_or_id: str,
    months: int,
    account_id: str | None,
    exact: bool,
    negative_only: bool,
    positive_only: bool,
    clear_review: bool,
    auto_approve: bool,
) -> None:
    mm = await get_client()
    categories = await _fetch_categories(mm)
    cat = _resolve_category(categories, name_or_id)

    today = date.today()
    transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(months, today=today),
        end_date=today.isoformat(),
    )

    needle = merchant_pattern.lower()
    matches = [
        t for t in transactions
        if (
            (
                ((t.get("merchant") or {}).get("name", "") or t.get("name", "")).lower() == needle
                if exact
                else needle in (((t.get("merchant") or {}).get("name", "") or t.get("name", "")).lower())
            )
            and (account_id is None or (t.get("account") or {}).get("id") == account_id)
            and (not negative_only or float(t.get("amount") or 0) < 0)
            and (not positive_only or float(t.get("amount") or 0) > 0)
        )
    ]

    if not matches:
        print(f"{YELLOW}No transactions found matching '{merchant_pattern}'.{RESET}")
        return

    print(
        f"Found {len(matches)} transaction(s) matching '{merchant_pattern}' "
        f"over the last {months} month(s). "
        f"Recategorizing to '{cat['name']}'..."
    )

    # Show preview
    for txn in matches[:10]:
        merchant = (txn.get("merchant") or {}).get("name", "") or txn.get("name", "")
        txn_date = str(txn.get("date", ""))[:10]
        amount = txn.get("amount", 0) or 0
        current_cat = (txn.get("category") or {}).get("name", "—")
        print(f"  {txn_date}  {merchant:<30}  ${abs(amount):>8,.2f}  (was: {current_cat})")

    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")

    if not auto_approve:
        print()
        confirm = input(f"Proceed with recategorizing all {len(matches)} transaction(s)? [y/N] ")
        if confirm.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return

    updated = 0
    failed = 0
    for txn in matches:
        txn_id = txn.get("id", "")
        try:
            await mm.update_transaction(
                transaction_id=txn_id,
                category_id=cat["id"],
                needs_review=False if clear_review else None,
            )
            updated += 1
        except Exception as exc:
            merchant = (txn.get("merchant") or {}).get("name", txn_id)
            print(f"  {RED}Failed{RESET} to update {merchant} ({txn_id}): {exc}")
            failed += 1

    print(
        f"\n{GREEN}Done.{RESET} Updated {updated} transaction(s)"
        + (f", {failed} failed." if failed else ".")
    )


async def cmd_refresh() -> None:
    mm = await get_client()
    print("Requesting account refresh...")
    accounts = await mm.get_accounts()
    account_ids = [
        acct["id"]
        for acct in accounts.get("accounts", [])
        if acct.get("isActive", True)
    ]
    success = await mm.request_accounts_refresh(account_ids)
    label = f"{GREEN}success{RESET}" if success else f"{RED}failed{RESET}"
    print(f"{GREEN}Refresh requested.{RESET}")
    print(f"  Accounts: {len(account_ids)}")
    print(f"  Status: {label}")


async def cmd_resolve_review(
    months: int,
    history_months: int,
    min_history: int,
    min_confidence: float,
    apply_safe: bool,
) -> None:
    mm = await get_client()
    today = date.today()
    review_transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(months, today=today),
        end_date=today.isoformat(),
    )
    review_transactions = [txn for txn in review_transactions if txn.get("needsReview")]

    if not review_transactions:
        print(f"{GREEN}No review transactions found.{RESET}")
        return

    history_transactions = await fetch_all_transactions(
        mm,
        start_date=month_start_months_ago(history_months, today=today),
        end_date=today.isoformat(),
    )

    suggestions: list[dict] = []
    for txn in review_transactions:
        merchant = transaction_merchant_name(txn)
        account = transaction_account_name(txn)
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
            top_id, top = max(counts.items(), key=lambda item: int(item[1]["count"]))
            top_count = int(top["count"])
            confidence = top_count / len(history)
            safe = len(history) >= min_history and confidence >= min_confidence
            suggestion = {
                "transaction": txn,
                "suggested_category_id": top_id,
                "suggested_category_name": str(top["name"]),
                "history_count": len(history),
                "confidence": confidence,
                "safe": safe,
            }
        else:
            suggestion = {
                "transaction": txn,
                "suggested_category_id": None,
                "suggested_category_name": None,
                "history_count": 0,
                "confidence": 0.0,
                "safe": False,
            }
        suggestions.append(suggestion)

    print(f"\n{BOLD}Review Resolution Plan{RESET}")
    print(
        f"  Found {len(suggestions)} review transaction(s). "
        f"Safe auto-apply threshold: history >= {min_history}, confidence >= {min_confidence:.0%}"
    )
    print("-" * 130)
    print(
        f"  {'Date':<10}  {'Merchant':<32}  {'Account':<24}  {'Amount':>10}  {'Suggestion':<22}  {'Confidence':>10}  {'Status':<8}"
    )
    print("-" * 130)
    for row in suggestions:
        txn = row["transaction"]
        amount = abs(float(txn.get("amount") or 0))
        print(
            f"  {str(txn.get('date', ''))[:10]:<10}  "
            f"{transaction_merchant_name(txn)[:32]:<32}  "
            f"{transaction_account_name(txn)[:24]:<24}  "
            f"{_money(amount):>10}  "
            f"{str(row['suggested_category_name'] or 'manual review')[:22]:<22}  "
            f"{f'{row['confidence']:.0%}':>10}  "
            f"{('safe' if row['safe'] else 'manual'):<8}"
        )
    print("-" * 130)

    safe_rows = [row for row in suggestions if row["safe"]]
    if not apply_safe:
        print(f"\n{CYAN}Dry run only.{RESET} Re-run with `--apply-safe` to update safe matches and clear review status.")
        return

    if not safe_rows:
        print(f"\n{YELLOW}No safe suggestions met the auto-apply threshold.{RESET}")
        return

    updated = 0
    for row in safe_rows:
        txn = row["transaction"]
        await mm.update_transaction(
            transaction_id=txn["id"],
            category_id=row["suggested_category_id"],
            needs_review=False,
        )
        updated += 1

    print(f"\n{GREEN}Updated {updated} transaction(s) and cleared review status.{RESET}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m monarch.update",
        description="Write CLI for Monarch Money",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cat_p = sub.add_parser("categorize", help="Recategorize a transaction")
    cat_p.add_argument("transaction_id", help="ID of the transaction to update")
    cat_p.add_argument("category", metavar="category_name_or_id",
                       help="Category name (fuzzy) or exact ID")

    bud_p = sub.add_parser("budget", help="Set a budget amount for a category")
    bud_p.add_argument("category", metavar="category_name_or_id",
                       help="Category name (fuzzy) or exact ID")
    bud_p.add_argument("amount", type=float, help="Monthly budget amount in dollars")
    bud_p.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD",
                       help="Start date (default: first of current month)")
    bud_p.add_argument("--future", action="store_true",
                       help="Apply this amount to all future months as well")

    bulk_p = sub.add_parser("bulk-categorize",
                            help="Recategorize all transactions matching a merchant pattern")
    bulk_p.add_argument("merchant_pattern",
                        help="Case-insensitive substring to match against merchant names")
    bulk_p.add_argument("category", metavar="category_name_or_id",
                        help="Category name (fuzzy) or exact ID")
    bulk_p.add_argument(
        "--account-id",
        help="Optional account ID scope",
    )
    bulk_p.add_argument(
        "--months",
        type=int,
        default=12,
        help="Months of history to search before bulk recategorizing (default: 12)",
    )
    bulk_p.add_argument(
        "--exact",
        action="store_true",
        help="Require exact merchant match instead of substring match",
    )
    bulk_p.add_argument(
        "--negative-only",
        action="store_true",
        help="Only match negative-amount transactions",
    )
    bulk_p.add_argument(
        "--positive-only",
        action="store_true",
        help="Only match positive-amount transactions",
    )
    bulk_p.add_argument(
        "--clear-review",
        action="store_true",
        help="Clear review status on updated transactions",
    )
    bulk_p.add_argument(
        "--yes",
        action="store_true",
        help="Apply without prompting for confirmation",
    )

    sub.add_parser("refresh", help="Trigger account refresh")

    resolve_p = sub.add_parser(
        "resolve-review",
        help="Suggest and optionally apply safe category fixes for review transactions",
    )
    resolve_p.add_argument(
        "--months",
        type=int,
        default=3,
        help="Lookback window for review transactions (default: 3)",
    )
    resolve_p.add_argument(
        "--history-months",
        type=int,
        default=12,
        help="Historical window used to infer categories (default: 12)",
    )
    resolve_p.add_argument(
        "--min-history",
        type=int,
        default=3,
        help="Minimum categorized history before auto-applying (default: 3)",
    )
    resolve_p.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Confidence threshold between 0 and 1 for auto-apply (default: 0.8)",
    )
    resolve_p.add_argument(
        "--apply-safe",
        action="store_true",
        help="Apply safe suggestions and clear review status",
    )

    return parser


async def async_main(args: argparse.Namespace) -> None:
    try:
        if args.command == "categorize":
            await cmd_categorize(args.transaction_id, args.category)
        elif args.command == "budget":
            await cmd_budget(
                name_or_id=args.category,
                amount=args.amount,
                from_date=args.from_date,
                apply_to_future=args.future,
            )
        elif args.command == "bulk-categorize":
            await cmd_bulk_categorize(
                args.merchant_pattern,
                args.category,
                months=args.months,
                account_id=args.account_id,
                exact=args.exact,
                negative_only=args.negative_only,
                positive_only=args.positive_only,
                clear_review=args.clear_review,
                auto_approve=args.yes,
            )
        elif args.command == "refresh":
            await cmd_refresh()
        elif args.command == "resolve-review":
            await cmd_resolve_review(
                months=args.months,
                history_months=args.history_months,
                min_history=args.min_history,
                min_confidence=args.min_confidence,
                apply_safe=args.apply_safe,
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
