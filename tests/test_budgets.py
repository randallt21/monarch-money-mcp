from __future__ import annotations

import unittest

from monarch.budgets import (
    budget_category_rows,
    budget_month_summary,
    budget_watchlist,
    count_nonzero_transfer_rows,
)


def _sample_budget_payload() -> dict:
    return {
        "categoryGroups": [
            {
                "name": "Income",
                "type": "income",
                "categories": [{"id": "income-1", "name": "Paychecks"}],
            },
            {
                "name": "Housing",
                "type": "expense",
                "categories": [{"id": "expense-1", "name": "Rent"}],
            },
            {
                "name": "Shopping",
                "type": "expense",
                "categories": [{"id": "expense-2", "name": "Amazon"}],
            },
            {
                "name": "Transfers",
                "type": "transfer",
                "categories": [{"id": "transfer-1", "name": "Transfer"}],
            },
        ],
        "budgetData": {
            "monthlyAmountsByCategory": [
                {
                    "category": {"id": "income-1"},
                    "monthlyAmounts": [{"plannedCashFlowAmount": 5000, "actualAmount": 2500}],
                },
                {
                    "category": {"id": "expense-1"},
                    "monthlyAmounts": [{"plannedCashFlowAmount": 2000, "actualAmount": 2000}],
                },
                {
                    "category": {"id": "expense-2"},
                    "monthlyAmounts": [{"plannedCashFlowAmount": 200, "actualAmount": 250}],
                },
                {
                    "category": {"id": "transfer-1"},
                    "monthlyAmounts": [{"plannedCashFlowAmount": 0, "actualAmount": 500}],
                },
            ],
            "totalsByMonth": [
                {
                    "totalIncome": {
                        "plannedAmount": 5000,
                        "actualAmount": 2500,
                        "remainingAmount": 2500,
                    },
                    "totalExpenses": {
                        "plannedAmount": 2200,
                        "actualAmount": 2250,
                        "remainingAmount": -50,
                    },
                }
            ],
        },
    }


class BudgetHelpersTests(unittest.TestCase):
    def test_budget_month_summary_uses_monarch_official_totals(self) -> None:
        summary = budget_month_summary(_sample_budget_payload())

        assert summary is not None
        self.assertEqual(summary["planned_income"], 5000)
        self.assertEqual(summary["planned_expenses"], 2200)
        self.assertEqual(summary["planned_margin"], 2800)
        self.assertEqual(summary["actual_net"], 250)
        self.assertEqual(summary["remaining_margin"], 2550)

    def test_budget_rows_exclude_transfers_by_default(self) -> None:
        rows = budget_category_rows(_sample_budget_payload())

        self.assertEqual([row.category_name for row in rows], ["Rent", "Paychecks", "Amazon"])
        self.assertEqual(count_nonzero_transfer_rows(_sample_budget_payload()), 1)

    def test_budget_watchlist_flags_overages_and_unbudgeted_spend(self) -> None:
        watch = budget_watchlist(_sample_budget_payload())

        self.assertEqual(
            watch,
            [
                {"category": "Amazon", "planned": 200.0, "actual": 250.0},
            ],
        )


if __name__ == "__main__":
    unittest.main()
