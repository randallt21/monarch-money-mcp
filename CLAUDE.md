# Monarch Money CLI Toolkit

This file is legacy assistant guidance. The current repo-level operator guide is in `AGENTS.md`.

## Quick Reference

```bash
# Read data
uv run python -m monarch.sync accounts
uv run python -m monarch.sync transactions [--month YYYY-MM] [--uncategorized] [--limit N]
uv run python -m monarch.sync budgets [--month YYYY-MM]
uv run python -m monarch.sync cashflow [--month YYYY-MM]
uv run python -m monarch.sync categories
uv run python -m monarch.sync review
uv run python -m monarch.sync dashboard [--month YYYY-MM]
uv run python -m monarch.sync rule-candidates [--months N] [--min-history N] [--min-confidence 0.8]
uv run python -m monarch.maintain [--refresh] [--dry-run] [--json]
uv run python -m monarch.scheduler install-launchd [--hour 9] [--minute 0] [--run-now]
uv run python -m monarch.scheduler status
uv run python -m monarch.scheduler run-job [--dry-run]
uv run python -m monarch.scheduler uninstall-launchd
uv run python -m monarch.codex_audit run-agent
uv run python -m monarch.codex_audit install-launchd [--weekday 1] [--hour 10] [--minute 30] [--run-now]
uv run python -m monarch.codex_audit status
uv run python -m monarch.codex_audit uninstall-launchd

# Write data
uv run python -m monarch.categories list [--group GROUP]
uv run python -m monarch.categories create --group GROUP --name NAME [--icon ICON] [--budget AMOUNT] [--future]
uv run python -m monarch.categories delete CATEGORY_NAME_OR_ID
uv run python -m monarch.update categorize <transaction_id> <category_name_or_id>
uv run python -m monarch.update budget <category_name_or_id> <amount> [--from YYYY-MM-DD] [--future]
uv run python -m monarch.update bulk-categorize <merchant_pattern> <category_name_or_id> [--months N] [--yes]
uv run python -m monarch.update resolve-review [--apply-safe]
uv run python -m monarch.update refresh

# Rules
uv run python -m monarch.rules list
uv run python -m monarch.rules preview --merchant PATTERN [--category CATEGORY]
uv run python -m monarch.rules create --merchant PATTERN [--category CATEGORY] [--dry-run]
uv run python -m monarch.rules update RULE_ID [--merchant PATTERN] [--category CATEGORY] [--dry-run]
uv run python -m monarch.rules delete RULE_ID
```

## Rules

- **Use the CLI toolkit, not MCP tools.** The `monarch/` package is the primary interface.
- **Auth is via `.env` file** in project root (gitignored). Session is cached in `.mm/` to avoid TOTP exhaustion.
- **Never hardcode credentials** in scripts. Always use env vars or `.env`.
- **Date strings only** — the library expects `"YYYY-MM-DD"` strings, not `date` objects.
- **Category creation supports emoji icons** directly. Use `monarch.categories create` for custom categories and `monarch.categories list` to inspect icon-aware category data.
- **Category name resolution** is case-insensitive; exact match preferred over substring.
- **Repo-side policy overrides** live in `monarch-policies.json`. Use them for one-off merchant exceptions that should not become broad Monarch rules.
- **Daily writes stay deterministic.** `monarch.scheduler` runs `monarch.maintain` via `launchd` and is the only unattended path that should touch live Monarch data.
- **Weekly Codex audit is repo-only.** `monarch.codex_audit` may improve code and automation, but must not mutate live Monarch data.

## API Quirks

- `MonarchMoneyEndpoints.BASE_URL` must be patched to `"https://api.monarch.com"`
- `mm._headers["Origin"]` must be `"https://app.monarch.com"`
- Login uses `try/except TypeError` for library version compatibility
- `get_budgets()` response has category IDs but not names — cross-reference with `categoryGroups`
- `set_budget_amount(amount=0)` clears a budget
- The MCP server (`server.py`) still exists but is not the preferred workflow
