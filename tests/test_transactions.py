from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from monarch.transactions import fetch_all_transactions


class FetchAllTransactionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_with_smaller_page_after_timeout(self) -> None:
        mm = AsyncMock()
        mm.get_transactions.side_effect = [
            TimeoutError("slow page"),
            {
                "allTransactions": {
                    "results": [{"id": "txn-1"}, {"id": "txn-2"}],
                    "totalCount": 2,
                }
            },
        ]

        results = await fetch_all_transactions(
            mm,
            start_date="2026-01-01",
            end_date="2026-01-31",
            batch_size=500,
            min_batch_size=100,
            timeout_retries=1,
        )

        self.assertEqual(results, [{"id": "txn-1"}, {"id": "txn-2"}])
        self.assertEqual(mm.get_transactions.await_count, 2)
        first_call = mm.get_transactions.await_args_list[0].kwargs
        second_call = mm.get_transactions.await_args_list[1].kwargs
        self.assertEqual(first_call["limit"], 500)
        self.assertEqual(second_call["limit"], 250)
        self.assertEqual(first_call["offset"], second_call["offset"])

    async def test_raises_after_exhausting_timeout_retries(self) -> None:
        mm = AsyncMock()
        mm.get_transactions.side_effect = TimeoutError("still timing out")

        with self.assertRaises(TimeoutError):
            await fetch_all_transactions(
                mm,
                start_date="2026-01-01",
                end_date="2026-01-31",
                batch_size=100,
                min_batch_size=100,
                timeout_retries=1,
            )

        self.assertEqual(mm.get_transactions.await_count, 2)


if __name__ == "__main__":
    unittest.main()
