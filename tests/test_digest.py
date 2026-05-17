from __future__ import annotations

import unittest

from monarch.digest import render_digest, render_error


def _base_summary(
    *,
    dry_run: bool = False,
    applied: list | None = None,
    new_rules: list | None = None,
    manual_suggestions: list | None = None,
    remaining_review_count: int = 0,
    budget_watch: list | None = None,
    budget_summary: dict | None = None,
    rule_count: int = 10,
    llm_guesses_count: int = 0,
    phase_timings_seconds: dict | None = None,
) -> dict:
    return {
        "scheduler": {"finished_at": "2026-05-16T09:00:00"},
        "summary": {
            "month": "2026-05",
            "dry_run": dry_run,
            "applied": applied or [],
            "new_rules": new_rules or [],
            "manual_suggestions": manual_suggestions or [],
            "remaining_review_count": remaining_review_count,
            "budget_watch": budget_watch or [],
            "budget_summary": budget_summary,
            "rule_count": rule_count,
            "llm_guesses_count": llm_guesses_count,
            "phase_timings_seconds": phase_timings_seconds or {"total": 5.0},
        },
    }


class DigestRenderTests(unittest.TestCase):
    def test_shows_month_and_run_time(self) -> None:
        out = render_digest(_base_summary())
        self.assertIn("2026-05", out)
        self.assertIn("2026-05-16 09:00", out)

    def test_dry_run_tag_shown(self) -> None:
        out = render_digest(_base_summary(dry_run=True))
        self.assertIn("DRY RUN", out)

    def test_auto_sorted_count_shown(self) -> None:
        applied = [
            {"merchant": "Starbucks", "category": "Coffee Shops", "reason": "history"},
            {"merchant": "Amazon", "category": "Shopping", "reason": "history"},
        ]
        out = render_digest(_base_summary(applied=applied))
        self.assertIn("Auto-sorted 2 transaction", out)
        self.assertIn("Starbucks", out)

    def test_llm_guess_transactions_shown_separately(self) -> None:
        applied = [
            {"merchant": "Known Co", "category": "Groceries", "reason": "history"},
            {"merchant": "NewCo", "category": "Shopping", "reason": "llm_guess"},
        ]
        out = render_digest(_base_summary(applied=applied))
        self.assertIn("LLM best-guess", out)
        self.assertIn("NewCo", out)

    def test_clear_queue_message_when_nothing_manual(self) -> None:
        out = render_digest(_base_summary())
        self.assertIn("Review queue is clear", out)

    def test_needs_attention_section_shown(self) -> None:
        manual = [
            {
                "merchant": "WeirdPlace",
                "date": "2026-05-10",
                "amount": "42.00",
                "suggested_category_name": "Dining",
                "reason": "low_confidence",
            }
        ]
        out = render_digest(_base_summary(manual_suggestions=manual, remaining_review_count=1))
        self.assertIn("Needs your eye", out)
        self.assertIn("WeirdPlace", out)

    def test_rules_created_section_shown(self) -> None:
        new_rules = [
            {"merchant": "Trader Joes", "category_name": "Groceries", "created": True},
        ]
        out = render_digest(_base_summary(new_rules=new_rules))
        self.assertIn("rule", out.lower())
        self.assertIn("Trader Joes", out)

    def test_budget_summary_net_shown(self) -> None:
        bsummary = {"planned_margin": 50.0, "actual_net": -200.0}
        out = render_digest(_base_summary(budget_summary=bsummary))
        self.assertIn("Net this month", out)
        self.assertIn("200.00", out)

    def test_budget_watch_rows_shown(self) -> None:
        budget_watch = [
            {"category": "Dining", "actual": 150.0, "planned": 100.0},
        ]
        out = render_digest(_base_summary(budget_watch=budget_watch))
        self.assertIn("Dining", out)
        self.assertIn("150.00", out)

    def test_stats_footer_shown(self) -> None:
        out = render_digest(_base_summary(rule_count=46, llm_guesses_count=3))
        self.assertIn("46 active rules", out)
        self.assertIn("3 merchant", out)

    def test_dry_run_shows_no_changes_applied(self) -> None:
        out = render_digest(_base_summary(dry_run=True, applied=[]))
        self.assertIn("Dry run", out)


class DigestRenderErrorTests(unittest.TestCase):
    def test_shows_failed_header(self) -> None:
        out = render_error("SSL error: CERTIFICATE_VERIFY_FAILED\nTraceback...\nFailed.")
        self.assertIn("FAILED", out)

    def test_shows_last_lines_of_error(self) -> None:
        lines = [f"line {i}" for i in range(20)]
        out = render_error("\n".join(lines))
        self.assertIn("line 19", out)
        self.assertNotIn("line 0", out)

    def test_includes_status_command_hint(self) -> None:
        out = render_error("some error")
        self.assertIn("scheduler status", out)


if __name__ == "__main__":
    unittest.main()
