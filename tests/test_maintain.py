from __future__ import annotations

import unittest
from datetime import date

from monarch.maintain import _build_suggestions, _split_uncategorized_by_cutoff, format_summary


class MaintainStatusTests(unittest.TestCase):
    def test_split_uncategorized_by_cutoff_separates_future_dated_items(self) -> None:
        current, future = _split_uncategorized_by_cutoff(
            [
                {
                    "id": "past",
                    "date": "2026-04-26",
                    "category": {"name": "Uncategorized"},
                },
                {
                    "id": "today",
                    "date": "2026-04-27",
                    "category": {"name": "Uncategorized"},
                },
                {
                    "id": "future",
                    "date": "2026-04-30",
                    "category": {"name": "Uncategorized"},
                },
                {
                    "id": "categorized",
                    "date": "2026-04-30",
                    "category": {"name": "Groceries"},
                },
            ],
            cutoff=date(2026, 4, 27),
        )

        self.assertEqual([txn["id"] for txn in current], ["past", "today"])
        self.assertEqual([txn["id"] for txn in future], ["future"])

    def test_format_summary_surfaces_future_uncategorized_count(self) -> None:
        rendered = format_summary(
            {
                "month": "2026-04",
                "refresh": {"requested": False},
                "dry_run": True,
                "targets_scanned": 0,
                "safe_suggestions": [],
                "manual_suggestions": [],
                "applied": [],
                "failures": [],
                "remaining_review_count": 0,
                "remaining_uncategorized_count": 0,
                "future_uncategorized_count": 1,
                "budget_summary": None,
                "budget_watch": [],
                "rule_count": 42,
            },
            color=False,
        )

        self.assertIn("Uncategorized this month: 0 (+1 future-dated)", rendered)

    def test_build_suggestions_distinguishes_insufficient_history_from_confidence(self) -> None:
        suggestions = _build_suggestions(
            [
                {
                    "id": "target",
                    "date": "2026-05-06",
                    "needsReview": True,
                    "merchant": {"name": "TFR Sofi Bank"},
                    "account": {"displayName": "Checking"},
                    "category": {"name": "Transfer"},
                    "amount": 1000,
                }
            ],
            [
                {
                    "id": "history-1",
                    "merchant": {"name": "TFR Sofi Bank"},
                    "account": {"displayName": "Checking"},
                    "category": {"id": "transfer-id", "name": "Transfer"},
                }
            ],
            min_history=3,
            min_confidence=0.8,
        )

        self.assertEqual(len(suggestions), 1)
        self.assertFalse(suggestions[0].safe)
        self.assertEqual(suggestions[0].confidence, 1.0)
        self.assertEqual(suggestions[0].reason, "insufficient_history")

    def test_build_suggestions_distinguishes_low_confidence_after_enough_history(self) -> None:
        history = [
            {
                "id": f"groceries-{index}",
                "merchant": {"name": "Target"},
                "account": {"displayName": "Credit"},
                "category": {"id": "groceries-id", "name": "Groceries"},
            }
            for index in range(2)
        ] + [
            {
                "id": "shopping-1",
                "merchant": {"name": "Target"},
                "account": {"displayName": "Credit"},
                "category": {"id": "shopping-id", "name": "Shopping"},
            }
        ]
        suggestions = _build_suggestions(
            [
                {
                    "id": "target",
                    "date": "2026-05-06",
                    "merchant": {"name": "Target"},
                    "account": {"displayName": "Credit"},
                    "category": {"name": "Uncategorized"},
                    "amount": 50,
                }
            ],
            history,
            min_history=3,
            min_confidence=0.8,
        )

        self.assertEqual(len(suggestions), 1)
        self.assertFalse(suggestions[0].safe)
        self.assertAlmostEqual(suggestions[0].confidence, 2 / 3)
        self.assertEqual(suggestions[0].reason, "insufficient_confidence")


if __name__ == "__main__":
    unittest.main()
