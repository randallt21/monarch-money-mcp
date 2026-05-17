from __future__ import annotations

import unittest

from monarch.autorules import _find_rule_candidates


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


if __name__ == "__main__":
    unittest.main()
