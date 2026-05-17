from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monarch import policies


class PolicyTests(unittest.TestCase):
    def test_load_merchant_overrides_rejects_invalid_json_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "monarch-policies.json"
            path.write_text('{"merchant_overrides":[}', encoding="utf-8")

            with patch("monarch.policies.POLICY_PATH", path):
                with self.assertRaises(policies.PolicyConfigError) as ctx:
                    policies.load_merchant_overrides()

        self.assertIn("Invalid JSON in monarch-policies.json", str(ctx.exception))
        self.assertIn("line 1", str(ctx.exception))

    def test_load_merchant_overrides_requires_non_empty_merchant_and_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "monarch-policies.json"
            path.write_text(
                json.dumps({"merchant_overrides": [{"merchant": "  ", "category": "Gifts"}]}),
                encoding="utf-8",
            )

            with patch("monarch.policies.POLICY_PATH", path):
                with self.assertRaises(policies.PolicyConfigError) as ctx:
                    policies.load_merchant_overrides()

        self.assertIn("override #1", str(ctx.exception))
        self.assertIn("merchant", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
