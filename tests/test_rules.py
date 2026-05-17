from __future__ import annotations

import unittest

from monarch.rules import _action_summary, _criteria_summary


class RuleSummaryTests(unittest.TestCase):
    def test_criteria_summary_includes_hidden_criteria_types(self) -> None:
        rule = {
            "merchantCriteriaUseOriginalStatement": True,
            "merchantCriteria": [{"operator": "contains", "value": "bank of america"}],
            "originalStatementCriteria": [{"operator": "eq", "value": "BANK OF AMERICA"}],
            "merchantNameCriteria": [{"operator": "contains", "value": "deposit"}],
            "amountCriteria": {"operator": "gt", "isExpense": False, "value": 0},
            "accounts": [{"displayName": "Checking"}],
        }

        summary = _criteria_summary(rule)

        self.assertIn("use_original_statement", summary)
        self.assertIn("contains:bank of america", summary)
        self.assertIn("stmt:eq:BANK OF AMERICA", summary)
        self.assertIn("mname:contains:deposit", summary)
        self.assertIn("inflow:gt:0", summary)
        self.assertIn("account:Checking", summary)

    def test_action_summary_includes_multiple_actions(self) -> None:
        rule = {
            "setCategoryAction": {"name": "Spouse Income"},
            "setMerchantAction": {"name": "Bank of America Payroll"},
            "reviewStatusAction": "reviewed",
            "setHideFromReportsAction": True,
            "sendNotificationAction": True,
            "addTagsAction": [{"name": "income"}],
        }

        summary = _action_summary(rule)

        self.assertIn("category:Spouse Income", summary)
        self.assertIn("merchant:Bank of America Payroll", summary)
        self.assertIn("review:reviewed", summary)
        self.assertIn("hide", summary)
        self.assertIn("notify", summary)
        self.assertIn("tags:income", summary)


if __name__ == "__main__":
    unittest.main()
