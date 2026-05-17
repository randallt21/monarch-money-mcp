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

    def test_build_suggestions_transfer_exclusion_overrides_confidence(self) -> None:
        # Transfer categories must never be auto-applied regardless of confidence.
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
            min_history=2,
            min_confidence=0.7,
            transfer_category_ids={"transfer-id"},
            llm_guesses={},
            category_name_to_id={"transfer": "transfer-id"},
        )

        self.assertEqual(len(suggestions), 1)
        self.assertFalse(suggestions[0].safe)
        self.assertEqual(suggestions[0].reason, "transfer_excluded")

    def test_build_suggestions_history_gate_alone_makes_suggestion_safe(self) -> None:
        # With the aggressive OR gate: history >= min is sufficient even if confidence < min.
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
        # 3 history, 2/3 confidence — history passes (3 >= 2), confidence fails (0.67 < 0.7)
        # OR gate: safe because history gate passes.
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
            min_history=2,
            min_confidence=0.7,
            transfer_category_ids=set(),
            llm_guesses={},
            category_name_to_id={"groceries": "groceries-id", "shopping": "shopping-id"},
        )

        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0].safe)
        self.assertAlmostEqual(suggestions[0].confidence, 2 / 3)
        self.assertEqual(suggestions[0].reason, "matched_history")

    def test_build_suggestions_unsafe_when_both_gates_fail(self) -> None:
        # 1 history (< 2 min), 50% confidence (< 0.7 min) → both gates fail → unsafe.
        suggestions = _build_suggestions(
            [
                {
                    "id": "target",
                    "date": "2026-05-06",
                    "merchant": {"name": "NewMerchant"},
                    "account": {"displayName": "Checking"},
                    "category": {"name": "Uncategorized"},
                    "amount": 25,
                }
            ],
            [
                {
                    "id": "h1",
                    "merchant": {"name": "NewMerchant"},
                    "account": {"displayName": "Checking"},
                    "category": {"id": "a-id", "name": "CategoryA"},
                },
                {
                    "id": "h2",
                    "merchant": {"name": "NewMerchant"},
                    "account": {"displayName": "Checking"},
                    "category": {"id": "b-id", "name": "CategoryB"},
                },
            ],
            min_history=3,
            min_confidence=0.8,
            transfer_category_ids=set(),
            llm_guesses={},
            category_name_to_id={"categorya": "a-id", "categoryb": "b-id"},
        )

        self.assertEqual(len(suggestions), 1)
        self.assertFalse(suggestions[0].safe)
        self.assertEqual(suggestions[0].confidence, 0.5)

    def test_build_suggestions_llm_guess_used_for_no_history(self) -> None:
        # LLM guess creates a safe suggestion when there is no history.
        suggestions = _build_suggestions(
            [
                {
                    "id": "target",
                    "date": "2026-05-06",
                    "merchant": {"name": "BrandNewCafe"},
                    "account": {"displayName": "Credit"},
                    "category": {"name": "Uncategorized"},
                    "amount": 12,
                }
            ],
            [],
            min_history=2,
            min_confidence=0.7,
            transfer_category_ids=set(),
            llm_guesses={"BrandNewCafe": {"category": "Dining & Drinks", "confidence": 0.9}},
            category_name_to_id={"dining & drinks": "dining-id"},
        )

        self.assertEqual(len(suggestions), 1)
        self.assertTrue(suggestions[0].safe)
        self.assertEqual(suggestions[0].reason, "llm_guess")
        self.assertEqual(suggestions[0].suggested_category_name, "Dining & Drinks")
        self.assertAlmostEqual(suggestions[0].confidence, 0.9)


if __name__ == "__main__":
    unittest.main()
