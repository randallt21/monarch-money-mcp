"""Helpers for normalizing Monarch budget payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetCategoryRow:
    group_name: str
    group_type: str
    category_name: str
    planned: float
    actual: float


def _category_maps(data: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    category_names: dict[str, str] = {}
    group_names: dict[str, str] = {}
    group_types: dict[str, str] = {}

    for group in data.get("categoryGroups", []):
        group_name = group.get("name", "")
        group_type = group.get("type", "")
        for category in group.get("categories", []):
            category_id = category.get("id", "")
            category_names[category_id] = category.get("name", category_id)
            group_names[category_id] = group_name
            group_types[category_id] = group_type

    return category_names, group_names, group_types


def budget_category_rows(
    data: dict[str, Any],
    *,
    include_transfers: bool = False,
    nonzero_only: bool = True,
) -> list[BudgetCategoryRow]:
    """Return category-level budget rows with consistent names and types."""
    category_names, group_names, group_types = _category_maps(data)
    rows: list[BudgetCategoryRow] = []

    for item in data.get("budgetData", {}).get("monthlyAmountsByCategory", []):
        category = item.get("category", {})
        category_id = category.get("id", "")
        group_type = group_types.get(category_id, "")
        if group_type == "transfer" and not include_transfers:
            continue

        amounts = item.get("monthlyAmounts", [{}])
        first_amount = amounts[0] if amounts and isinstance(amounts[0], dict) else {}
        planned = float(first_amount.get("plannedCashFlowAmount", 0) or 0)
        actual = float(first_amount.get("actualAmount", 0) or 0)

        if nonzero_only and planned == 0 and actual == 0:
            continue

        rows.append(
            BudgetCategoryRow(
                group_name=group_names.get(category_id, ""),
                group_type=group_type,
                category_name=category_names.get(category_id, category.get("name", category_id)),
                planned=planned,
                actual=actual,
            )
        )

    rows.sort(key=lambda row: (row.group_name, row.category_name))
    return rows


def count_nonzero_transfer_rows(data: dict[str, Any]) -> int:
    """Count transfer-category rows that carry budget or activity."""
    return sum(
        1
        for row in budget_category_rows(data, include_transfers=True, nonzero_only=True)
        if row.group_type == "transfer"
    )


def budget_month_summary(data: dict[str, Any]) -> dict[str, float] | None:
    """Return Monarch's official month totals when present."""
    totals = data.get("budgetData", {}).get("totalsByMonth", [])
    if not totals:
        return None

    month_totals = totals[0]
    total_income = month_totals.get("totalIncome") or {}
    total_expenses = month_totals.get("totalExpenses") or {}

    planned_income = float(total_income.get("plannedAmount", 0) or 0)
    actual_income = float(total_income.get("actualAmount", 0) or 0)
    remaining_income = float(total_income.get("remainingAmount", 0) or 0)

    planned_expenses = float(total_expenses.get("plannedAmount", 0) or 0)
    actual_expenses = float(total_expenses.get("actualAmount", 0) or 0)
    remaining_expenses = float(total_expenses.get("remainingAmount", 0) or 0)

    return {
        "planned_income": planned_income,
        "actual_income": actual_income,
        "remaining_income": remaining_income,
        "planned_expenses": planned_expenses,
        "actual_expenses": actual_expenses,
        "remaining_expenses": remaining_expenses,
        "planned_margin": planned_income - planned_expenses,
        "actual_net": actual_income - actual_expenses,
        "remaining_margin": remaining_income - remaining_expenses,
    }


def budget_watchlist(data: dict[str, Any]) -> list[dict[str, float | str]]:
    """Return noteworthy budget rows that need attention."""
    watch: list[dict[str, float | str]] = []
    for row in budget_category_rows(data, include_transfers=False, nonzero_only=True):
        if row.planned > 0 and row.actual > row.planned:
            watch.append(
                {
                    "category": row.category_name,
                    "planned": row.planned,
                    "actual": row.actual,
                }
            )
        elif row.planned == 0 and row.actual != 0:
            watch.append(
                {
                    "category": row.category_name,
                    "planned": row.planned,
                    "actual": row.actual,
                }
            )

    watch.sort(key=lambda row: abs(float(row["actual"]) - float(row["planned"])), reverse=True)
    return watch
