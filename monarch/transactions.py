"""Shared transaction helpers for Monarch CLI and MCP surfaces."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Any

from monarchmoney import MonarchMoney


def month_start_months_ago(months: int, *, today: date | None = None) -> str:
    """Return the first day of the month N months ago as YYYY-MM-DD."""
    if months < 1:
        raise ValueError("months must be >= 1")

    today = today or date.today()
    month_index = today.month - (months - 1)
    year = today.year

    while month_index <= 0:
        month_index += 12
        year -= 1

    return f"{year:04d}-{month_index:02d}-01"


async def fetch_all_transactions(
    mm: MonarchMoney,
    *,
    start_date: str,
    end_date: str,
    batch_size: int = 500,
    min_batch_size: int = 100,
    timeout_retries: int = 2,
    search: str = "",
    category_ids: Iterable[str] | None = None,
    account_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch all matching transactions using offset pagination.

    On request timeouts, retry the same page with progressively smaller limits.
    This keeps unattended jobs deterministic while reducing the chance that one
    slow page forces the scheduler to rerun the entire maintain workflow.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if min_batch_size < 1:
        raise ValueError("min_batch_size must be >= 1")
    if min_batch_size > batch_size:
        raise ValueError("min_batch_size must be <= batch_size")
    if timeout_retries < 0:
        raise ValueError("timeout_retries must be >= 0")

    results: list[dict[str, Any]] = []
    offset = 0
    total_count: int | None = None

    while total_count is None or offset < total_count:
        current_batch_size = batch_size
        last_error: TimeoutError | None = None
        for _attempt in range(timeout_retries + 1):
            try:
                data = await mm.get_transactions(
                    start_date=start_date,
                    end_date=end_date,
                    limit=current_batch_size,
                    offset=offset,
                    search=search,
                    category_ids=list(category_ids or []),
                    account_ids=list(account_ids or []),
                )
                break
            except TimeoutError as exc:
                last_error = exc
                if current_batch_size <= min_batch_size:
                    continue
                current_batch_size = max(
                    min_batch_size,
                    math.ceil(current_batch_size / 2),
                )
        else:
            assert last_error is not None
            raise last_error

        all_txns = data.get("allTransactions", {})
        batch = all_txns.get("results", [])
        total_count = all_txns.get("totalCount", len(batch))
        if not batch:
            break
        results.extend(batch)
        offset += len(batch)

    return results


def transaction_merchant_name(txn: dict[str, Any]) -> str:
    merchant = txn.get("merchant") or {}
    return (merchant.get("name") or txn.get("name") or "").strip()


def transaction_category_name(txn: dict[str, Any]) -> str:
    category = txn.get("category") or {}
    return (category.get("name") or "Uncategorized").strip()


def transaction_account_name(txn: dict[str, Any]) -> str:
    account = txn.get("account") or {}
    return (account.get("displayName") or "").strip()
