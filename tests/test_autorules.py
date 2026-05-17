from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from monarch.autorules import _find_rule_candidates, auto_create_rules


def _txn(merchant: str, account: str, cat_id: str, cat_name: str) -> dict:
    return {
        "merchant": {"name": merchant},
        "account": {"displayName": account},
        "category": {"id": cat_id, "name": cat_name},
    }


class AutoRulesTests(unittest.TestCase):
    def test_qualifies_with_three_consistent_observations(self) -> None:
        history = [_txn("Starbucks", "Credit", "cafe-id", "Coffee Shops") for _ in range(3)]
        candidates = _find_rule_candidates(history, [], {}, set())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["merchant"], "Starbucks")
        self.assertEqual(candidates[0]["category_name"], "Coffee Shops")

    def test_does_not_qualify_with_fewer_than_three(self) -> None:
        history = [_txn("Starbucks", "Credit", "cafe-id", "Coffee Shops") for _ in range(2)]
        candidates = _find_rule_candidates(history, [], {}, set())
        self.assertEqual(candidates, [])

    def test_transfer_category_excluded(self) -> None:
        history = [_txn("SoFi Bank", "Checking", "transfer-id", "Transfer") for _ in range(5)]
        candidates = _find_rule_candidates(history, [], {}, {"transfer-id"})
        self.assertEqual(candidates, [])

    def test_deduplicates_same_merchant_different_accounts(self) -> None:
        history = (
            [_txn("Amazon", "Checking", "shop-id", "Shopping") for _ in range(3)]
            + [_txn("Amazon", "Credit", "shop-id", "Shopping") for _ in range(3)]
        )
        candidates = _find_rule_candidates(history, [], {}, set())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["merchant"], "Amazon")

    def test_skips_existing_rule_duplicate(self) -> None:
        from monarch.rules import _build_rule_input

        history = [_txn("Netflix", "Credit", "ent-id", "Entertainment") for _ in range(3)]
        existing_rule = {
            "merchantCriteria": [{"operator": "contains", "value": "Netflix"}],
            "setCategoryAction": {"id": "ent-id"},
            "accountIds": [],
            "reviewStatusAction": "reviewed",
            "setHideFromReportsAction": False,
            "amountCriteria": None,
        }
        candidates = _find_rule_candidates(history, [existing_rule], {}, set())
        self.assertEqual(candidates, [])

    def test_contradictory_categories_across_accounts_skipped(self) -> None:
        # Same merchant maps to different dominant categories on two accounts.
        # After A2 fix, the merchant-level dedup drops it as ambiguous.
        history = (
            [_txn("Amazon", "Checking", "shop-id", "Shopping") for _ in range(3)]
            + [_txn("Amazon", "Credit", "grocery-id", "Groceries") for _ in range(3)]
        )
        candidates = _find_rule_candidates(history, [], {}, set())
        self.assertEqual(candidates, [], "Ambiguous merchant should produce no rule candidates")

    def test_intra_account_tie_skipped(self) -> None:
        # Same merchant on same account with perfectly tied category counts.
        # After B1 fix, a tied top category is treated as ambiguous and skipped.
        history = (
            [_txn("WidgetCo", "Credit", "shop-id", "Shopping") for _ in range(3)]
            + [_txn("WidgetCo", "Credit", "elec-id", "Electronics") for _ in range(3)]
        )
        candidates = _find_rule_candidates(history, [], {}, set())
        self.assertEqual(candidates, [], "Tied dominant categories should produce no rule candidates")


class AutoRulesAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_create_rules_dry_run_returns_records_without_api(self) -> None:
        history = [_txn("Costco", "Credit", "grocery-id", "Groceries") for _ in range(3)]
        with patch("monarch.autorules.get_client") as mock_get_client:
            result = await auto_create_rules(history, [], {}, set(), dry_run=True)
        mock_get_client.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["dry_run"], True)
        self.assertIs(result[0]["created"], False)
        self.assertEqual(result[0]["merchant"], "Costco")
        self.assertEqual(result[0]["category_name"], "Groceries")

    async def test_auto_create_rules_live_path_records_gql_errors(self) -> None:
        history = [_txn("BestBuy", "Credit", "elec-id", "Electronics") for _ in range(3)]
        mock_client = AsyncMock()
        mock_client.gql_call = AsyncMock(
            return_value={"createTransactionRuleV2": {"errors": {"message": "boom", "fieldErrors": []}}}
        )
        with patch("monarch.autorules.get_client", return_value=mock_client):
            result = await auto_create_rules(history, [], {}, set(), dry_run=False)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["created"], False)
        self.assertIn("error", result[0])
        self.assertIn("boom", result[0]["error"])

    async def test_auto_create_rules_live_path_records_exception(self) -> None:
        history = [_txn("TechCo", "Checking", "elec-id", "Electronics") for _ in range(3)]
        mock_client = AsyncMock()
        mock_client.gql_call = AsyncMock(side_effect=RuntimeError("network failure"))
        with patch("monarch.autorules.get_client", return_value=mock_client):
            result = await auto_create_rules(history, [], {}, set(), dry_run=False)
        self.assertEqual(len(result), 1)
        self.assertIs(result[0]["created"], False)
        self.assertIn("network failure", result[0]["error"])


if __name__ == "__main__":
    unittest.main()
