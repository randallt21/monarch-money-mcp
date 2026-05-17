from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from monarch.classify import (
    UNCERTAIN_SENTINEL,
    _build_schema,
    classify_merchants,
)


def _envelope(results: list) -> str:
    return json.dumps({"type": "result", "structured_output": {"results": results}})


class ClassifySchemaTests(unittest.TestCase):
    def test_schema_includes_all_categories_and_sentinel(self) -> None:
        cats = ["Groceries", "Dining"]
        schema_str = _build_schema(cats)
        schema = json.loads(schema_str)
        enum = schema["properties"]["results"]["items"]["properties"]["category"]["enum"]
        self.assertIn("Groceries", enum)
        self.assertIn("Dining", enum)
        self.assertIn(UNCERTAIN_SENTINEL, enum)

    def test_schema_required_fields(self) -> None:
        schema = json.loads(_build_schema(["Groceries"]))
        required = schema["properties"]["results"]["items"]["required"]
        self.assertIn("merchant", required)
        self.assertIn("category", required)
        self.assertIn("confidence", required)


class ClassifyMerchantsTests(unittest.TestCase):
    def _run(self, stdout: str, returncode: int = 0) -> MagicMock:
        m = MagicMock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = ""
        return m

    def test_returns_guesses_for_valid_response(self) -> None:
        results = [
            {"merchant": "Starbucks", "category": "Coffee Shops", "confidence": 0.95},
            {"merchant": "Shell", "category": "Gas & Fuel", "confidence": 0.88},
        ]
        with patch("subprocess.run", return_value=self._run(_envelope(results))):
            guesses = classify_merchants(["Starbucks", "Shell"], ["Coffee Shops", "Gas & Fuel"])
        self.assertEqual(guesses["Starbucks"]["category"], "Coffee Shops")
        self.assertAlmostEqual(guesses["Starbucks"]["confidence"], 0.95)
        self.assertEqual(guesses["Shell"]["category"], "Gas & Fuel")

    def test_excludes_uncertain_sentinel(self) -> None:
        results = [
            {"merchant": "UnknownCo", "category": UNCERTAIN_SENTINEL, "confidence": 0.1},
        ]
        with patch("subprocess.run", return_value=self._run(_envelope(results))):
            guesses = classify_merchants(["UnknownCo"], ["Groceries"])
        self.assertNotIn("UnknownCo", guesses)

    def test_returns_empty_on_timeout(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 90)):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})

    def test_returns_empty_on_nonzero_exit(self) -> None:
        with patch("subprocess.run", return_value=self._run("error output", returncode=1)):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})

    def test_returns_empty_on_invalid_json(self) -> None:
        with patch("subprocess.run", return_value=self._run("not json")):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})

    def test_returns_empty_on_is_error_envelope(self) -> None:
        bad = json.dumps({"is_error": True, "error": {"message": "something failed"}})
        with patch("subprocess.run", return_value=self._run(bad)):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})

    def test_returns_empty_on_missing_structured_output(self) -> None:
        no_structured = json.dumps({"type": "result", "result": "plain text"})
        with patch("subprocess.run", return_value=self._run(no_structured)):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})

    def test_returns_empty_when_merchants_list_empty(self) -> None:
        with patch("subprocess.run") as mock_run:
            guesses = classify_merchants([], ["Coffee Shops"])
        mock_run.assert_not_called()
        self.assertEqual(guesses, {})

    def test_returns_empty_on_file_not_found(self) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("claude not found")):
            guesses = classify_merchants(["Starbucks"], ["Coffee Shops"])
        self.assertEqual(guesses, {})


if __name__ == "__main__":
    unittest.main()
