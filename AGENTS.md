# Monarch Money Toolkit

## Purpose

This repo is a local toolkit for reading and updating Monarch Money data.

- Prefer the `monarch/` CLI package over the MCP server.
- Treat `server.py` as compatibility glue, not the primary workflow.
- Use `.env` in the repo root for auth and `.mm/` for cached sessions.

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
uv run python -m monarch.codex_audit install-launchd [--weekday 1] [--hour 12] [--minute 0] [--run-now]
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

## Working Rules

- Do not hardcode Monarch credentials anywhere in the repo.
- Repo-side policy overrides live in `monarch-policies.json`. Use them for user-declared merchant exceptions that should not become broad Monarch rules.
- The upstream library expects date strings in `YYYY-MM-DD` format, not `date` objects.
- Use paginated transaction reads for anything beyond a single month or a small sample.
- Category resolution should stay case-insensitive, with exact name matches preferred over substring matches.
- Custom categories can be created from the CLI with an explicit emoji icon and optional initial budget.
- Be careful with transfer-like merchants. Some repeated merchants are not safe for blunt merchant-only categorization rules.

## Current Repo Posture

- `monarch/client.py` is the shared auth and session entrypoint.
- `monarch/sync.py` is the main read/reporting surface.
- `monarch/update.py` is the main mutation surface.
- `monarch/transactions.py` contains shared transaction pagination and normalization helpers.
- `server.py` should stay aligned with the CLI/client behavior but should not lead design decisions.

## Monarch-Specific Quirks

- Patch `MonarchMoneyEndpoints.BASE_URL` to `https://api.monarch.com`.
- Set `mm._headers["Origin"]` to `https://app.monarch.com`.
- Login may require a compatibility fallback around `TypeError` depending on library version.
- `get_budgets()` returns category IDs in budget rows and needs category-group cross-reference for readable output.
- Transfer-type categories can appear in the budget API payload even though they should generally be excluded from budget reporting.

## Automation Direction

- First priority: reduce `needsReview` and `Uncategorized` transactions.
- Prefer a two-step workflow:
  1. Use `rule-candidates` to find repeated, high-confidence merchant/account patterns.
  2. Turn safe patterns into Monarch rules and leave ambiguous merchants for manual review or richer logic.
- `monarch.maintain` is the unattended entrypoint for scheduled use. It scans recent `needsReview` and `Uncategorized` items, applies only safe history-backed fixes, and emits a summary.
- Use repo-side policy overrides for one-off merchants like gifts or special-case spending where a global Monarch rule would be too blunt.
- `monarch.scheduler` is the local macOS scheduler surface. It installs a `launchd` job that runs `monarch.maintain`, writes timestamped summaries under `~/Library/Application Support/monarch-money-mcp/maintain/`, and logs stdout/stderr under `~/Library/Logs/monarch-money-mcp/`.
- `monarch.codex_audit` is the weekly engineering agent surface. It runs `codex exec` against the repo to improve code and automation, but it must not mutate live Monarch data.
- Good candidates are repeated merchants with one dominant historical category.
- Bad candidates are merchants whose historical usage spans transfers, investments, and spending categories.
