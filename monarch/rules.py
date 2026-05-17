"""
CLI for managing Monarch transaction rules.

Usage:
    uv run python -m monarch.rules list
    uv run python -m monarch.rules preview --merchant PATTERN [--operator contains] [--category CATEGORY]
    uv run python -m monarch.rules create --merchant PATTERN [--operator contains] [--category CATEGORY]
    uv run python -m monarch.rules update RULE_ID [--merchant PATTERN] [--category CATEGORY]
    uv run python -m monarch.rules delete RULE_ID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from gql import gql

from monarch.client import get_client
from monarch.update import _fetch_categories, _resolve_category

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
RESET = "\033[0m"

GET_RULES_QUERY = gql(
    """
    query GetTransactionRules {
      transactionRules {
        id
        order
        merchantCriteriaUseOriginalStatement
        merchantCriteria {
          operator
          value
        }
        originalStatementCriteria {
          operator
          value
        }
        merchantNameCriteria {
          operator
          value
        }
        amountCriteria {
          operator
          isExpense
          value
          valueRange {
            lower
            upper
          }
        }
        categoryIds
        accountIds
        categories {
          id
          name
          icon
        }
        accounts {
          id
          displayName
        }
        setMerchantAction {
          id
          name
        }
        setCategoryAction {
          id
          name
          icon
        }
        addTagsAction {
          id
          name
          color
        }
        sendNotificationAction
        setHideFromReportsAction
        reviewStatusAction
        lastAppliedAt
        recentApplicationCount
      }
    }
    """
)

PREVIEW_RULE_QUERY = gql(
    """
    query Common_PreviewTransactionRule($rule: TransactionRulePreviewInput!, $offset: Int) {
      transactionRulePreview(input: $rule) {
        totalCount
        results(offset: $offset, limit: 30) {
          newName
          newCategory {
            id
            name
          }
          newHideFromReports
          transaction {
            id
            date
            amount
            merchant {
              name
            }
            category {
              name
            }
            account {
              displayName
            }
          }
        }
      }
    }
    """
)

CREATE_RULE_QUERY = gql(
    """
    mutation Common_CreateTransactionRuleMutationV2($input: CreateTransactionRuleInput!) {
      createTransactionRuleV2(input: $input) {
        errors {
          message
          code
          fieldErrors {
            field
            messages
          }
        }
      }
    }
    """
)

UPDATE_RULE_QUERY = gql(
    """
    mutation Common_UpdateTransactionRuleMutationV2($input: UpdateTransactionRuleInput!) {
      updateTransactionRuleV2(input: $input) {
        errors {
          message
          code
          fieldErrors {
            field
            messages
          }
        }
      }
    }
    """
)

DELETE_RULE_QUERY = gql(
    """
    mutation Common_DeleteTransactionRule($id: ID!) {
      deleteTransactionRule(id: $id) {
        deleted
        errors {
          message
          code
          fieldErrors {
            field
            messages
          }
        }
      }
    }
    """
)


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


def _criterion_bits(prefix: str, criteria: list[dict[str, Any]]) -> list[str]:
    bits: list[str] = []
    for criterion in criteria:
        operator = criterion.get("operator") or "?"
        value = criterion.get("value") or "?"
        bits.append(f"{prefix}{operator}:{value}")
    return bits


def _amount_criteria_summary(amount_criteria: dict[str, Any] | None) -> str | None:
    if not amount_criteria:
        return None

    operator = amount_criteria.get("operator") or "?"
    is_expense = amount_criteria.get("isExpense")
    direction = "outflow" if is_expense is True else "inflow" if is_expense is False else "amount"

    if amount_criteria.get("valueRange"):
        lower = amount_criteria["valueRange"].get("lower")
        upper = amount_criteria["valueRange"].get("upper")
        return f"{direction}:{operator}:{lower}-{upper}"

    return f"{direction}:{operator}:{amount_criteria.get('value')}"


def _criteria_summary(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    if rule.get("merchantCriteriaUseOriginalStatement"):
        parts.append("use_original_statement")
    parts.extend(_criterion_bits("", rule.get("merchantCriteria") or []))
    parts.extend(_criterion_bits("stmt:", rule.get("originalStatementCriteria") or []))
    parts.extend(_criterion_bits("mname:", rule.get("merchantNameCriteria") or []))

    amount_summary = _amount_criteria_summary(rule.get("amountCriteria"))
    if amount_summary:
        parts.append(amount_summary)

    account_bits = [acct.get("displayName", "") for acct in rule.get("accounts") or [] if acct.get("displayName")]
    if account_bits:
        parts.append(f"account:{'|'.join(account_bits)}")

    category_bits = [cat.get("name", "") for cat in rule.get("categories") or [] if cat.get("name")]
    if category_bits:
        parts.append(f"match-category:{'|'.join(category_bits)}")

    return " ; ".join(parts) or "(no visible criteria)"


def _action_summary(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    if rule.get("setCategoryAction"):
        parts.append(f"category:{rule['setCategoryAction']['name']}")
    if rule.get("setMerchantAction"):
        parts.append(f"merchant:{rule['setMerchantAction']['name']}")
    if rule.get("reviewStatusAction"):
        parts.append(f"review:{rule['reviewStatusAction']}")
    if rule.get("setHideFromReportsAction"):
        parts.append("hide")
    if rule.get("sendNotificationAction"):
        parts.append("notify")
    if rule.get("addTagsAction"):
        parts.append(
            "tags:" + "|".join(tag.get("name", "") for tag in rule["addTagsAction"])
        )
    return " ; ".join(parts) or "(no action)"


def _build_rule_input(
    *,
    merchant: str,
    operator: str,
    category_id: str | None,
    account_id: str | None,
    review_status: str | None,
    hide_from_reports: bool,
    outflow_only: bool,
    inflow_only: bool,
) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "merchantCriteria": [{"operator": operator, "value": merchant}],
    }
    if category_id:
        rule["setCategoryAction"] = category_id
    if account_id:
        rule["accountIds"] = [account_id]
    if review_status:
        rule["reviewStatusAction"] = review_status
    if hide_from_reports:
        rule["setHideFromReportsAction"] = True
    if outflow_only and inflow_only:
        raise RuntimeError("Choose only one of outflow_only or inflow_only")
    if outflow_only:
        rule["amountCriteria"] = {"operator": "gt", "isExpense": True, "value": 0}
    elif inflow_only:
        rule["amountCriteria"] = {"operator": "gt", "isExpense": False, "value": 0}
    return rule


def _format_errors(errors: dict[str, Any] | None) -> str:
    if not errors:
        return ""
    parts: list[str] = []
    if errors.get("message"):
        parts.append(str(errors["message"]))
    for field_error in errors.get("fieldErrors") or []:
        field = field_error.get("field", "field")
        messages = ", ".join(field_error.get("messages") or [])
        parts.append(f"{field}: {messages}")
    return "; ".join(parts)


def _rule_matches_created_input(
    rule: dict[str, Any],
    *,
    merchant: str,
    operator: str,
    category_id: str | None,
    account_id: str | None,
    review_status: str | None,
    hide_from_reports: bool,
    outflow_only: bool,
    inflow_only: bool,
) -> bool:
    merchant_criteria = rule.get("merchantCriteria") or []
    if len(merchant_criteria) != 1:
        return False
    merchant_bit = merchant_criteria[0]
    if merchant_bit.get("operator") != operator or merchant_bit.get("value") != merchant:
        return False

    rule_category = (rule.get("setCategoryAction") or {}).get("id")
    if rule_category != category_id:
        return False

    rule_accounts = rule.get("accountIds") or []
    expected_accounts = [account_id] if account_id else []
    if rule_accounts != expected_accounts:
        return False

    if rule.get("reviewStatusAction") != review_status:
        return False

    if bool(rule.get("setHideFromReportsAction")) != hide_from_reports:
        return False

    amount_criteria = rule.get("amountCriteria")
    if outflow_only:
        return (
            isinstance(amount_criteria, dict)
            and amount_criteria.get("operator") == "gt"
            and amount_criteria.get("isExpense") is True
            and float(amount_criteria.get("value") or 0) == 0
        )
    if inflow_only:
        return (
            isinstance(amount_criteria, dict)
            and amount_criteria.get("operator") == "gt"
            and amount_criteria.get("isExpense") is False
            and float(amount_criteria.get("value") or 0) == 0
        )

    return amount_criteria in (None, {})


async def fetch_rules() -> list[dict[str, Any]]:
    mm = await get_client()
    data = await mm.gql_call("GetTransactionRules", GET_RULES_QUERY)
    return data.get("transactionRules", [])


async def fetch_rule(rule_id: str) -> dict[str, Any]:
    for rule in await fetch_rules():
        if rule["id"] == rule_id:
            return rule
    raise RuntimeError(f"Rule not found: {rule_id}")


async def cmd_list() -> None:
    rules = await fetch_rules()
    if not rules:
        print("No transaction rules found.")
        return

    print(f"\n{BOLD}Transaction Rules{RESET}  ({len(rules)} found)")
    print("-" * 120)
    print(
        f"  {'Order':>5}  {'ID':<18}  {'Criteria':<42}  {'Action':<28}  {'Recent':>6}  {'Last Applied':<20}"
    )
    print("-" * 120)
    for rule in sorted(rules, key=lambda item: item.get("order", 0)):
        criteria_summary = _criteria_summary(rule)
        action_summary = _action_summary(rule)
        print(
            f"  {rule.get('order', 0):>5}  {rule.get('id', ''):<18}  "
            f"{criteria_summary[:42]:<42}  "
            f"{action_summary[:28]:<28}  "
            f"{rule.get('recentApplicationCount', 0):>6}  "
            f"{str(rule.get('lastAppliedAt') or '—')[:20]:<20}"
        )
    print()


async def cmd_preview(
    merchant: str,
    operator: str,
    category_name_or_id: str | None,
    account_id: str | None,
    review_status: str | None,
    hide_from_reports: bool,
    outflow_only: bool,
    inflow_only: bool,
) -> None:
    mm = await get_client()
    category_id = None
    if category_name_or_id:
        categories = await _fetch_categories(mm)
        category_id = _resolve_category(categories, category_name_or_id)["id"]

    rule = _build_rule_input(
        merchant=merchant,
        operator=operator,
        category_id=category_id,
        account_id=account_id,
        review_status=review_status,
        hide_from_reports=hide_from_reports,
        outflow_only=outflow_only,
        inflow_only=inflow_only,
    )
    data = await mm.gql_call(
        "Common_PreviewTransactionRule",
        PREVIEW_RULE_QUERY,
        {"rule": rule, "offset": 0},
    )
    preview = data["transactionRulePreview"]
    results = preview.get("results", [])

    print(f"\n{BOLD}Rule Preview{RESET}")
    print(f"  Matches: {preview.get('totalCount', 0)}")
    print(f"  Input: {json.dumps(rule, indent=2)}")
    if not results:
        print(f"  {YELLOW}No sample matches returned.{RESET}\n")
        return

    print("-" * 110)
    print(
        f"  {'Date':<10}  {'Merchant':<28}  {'Current':<22}  {'New':<22}  {'Amount':>10}  {'Account':<14}"
    )
    print("-" * 110)
    for row in results[:10]:
        txn = row["transaction"]
        current_category = ((txn.get("category") or {}).get("name")) or "—"
        new_category = ((row.get("newCategory") or {}).get("name")) or current_category
        print(
            f"  {str(txn.get('date', ''))[:10]:<10}  "
            f"{((txn.get('merchant') or {}).get('name') or '')[:28]:<28}  "
            f"{current_category[:22]:<22}  "
            f"{new_category[:22]:<22}  "
            f"{_money(abs(float(txn.get('amount') or 0))):>10}  "
            f"{((txn.get('account') or {}).get('displayName') or '')[:14]:<14}"
        )
    print()


async def cmd_create(
    merchant: str,
    operator: str,
    category_name_or_id: str | None,
    account_id: str | None,
    review_status: str | None,
    hide_from_reports: bool,
    outflow_only: bool,
    inflow_only: bool,
    dry_run: bool,
) -> None:
    mm = await get_client()
    before_ids = {rule["id"] for rule in await fetch_rules()}
    category_id = None
    category_name = None
    if category_name_or_id:
        categories = await _fetch_categories(mm)
        category = _resolve_category(categories, category_name_or_id)
        category_id = category["id"]
        category_name = category["name"]

    rule = _build_rule_input(
        merchant=merchant,
        operator=operator,
        category_id=category_id,
        account_id=account_id,
        review_status=review_status,
        hide_from_reports=hide_from_reports,
        outflow_only=outflow_only,
        inflow_only=inflow_only,
    )

    if dry_run:
        await cmd_preview(
            merchant=merchant,
            operator=operator,
            category_name_or_id=category_name_or_id,
            account_id=account_id,
            review_status=review_status,
            hide_from_reports=hide_from_reports,
            outflow_only=outflow_only,
            inflow_only=inflow_only,
        )
        return

    result = await mm.gql_call(
        "Common_CreateTransactionRuleMutationV2",
        CREATE_RULE_QUERY,
        {"input": rule},
    )
    errors = result["createTransactionRuleV2"]["errors"]
    message = _format_errors(errors)
    if message:
        print(f"{RED}Create failed:{RESET} {message}")
        sys.exit(1)

    created_rule_id = None
    after_rules = await fetch_rules()
    for created_rule in after_rules:
        if created_rule["id"] in before_ids:
            continue
        if _rule_matches_created_input(
            created_rule,
            merchant=merchant,
            operator=operator,
            category_id=category_id,
            account_id=account_id,
            review_status=review_status,
            hide_from_reports=hide_from_reports,
            outflow_only=outflow_only,
            inflow_only=inflow_only,
        ):
            created_rule_id = created_rule["id"]
            break

    if created_rule_id is None:
        for created_rule in after_rules:
            if created_rule["id"] not in before_ids:
                created_rule_id = created_rule["id"]
                break

    print(f"{GREEN}Rule created.{RESET}")
    if created_rule_id:
        print(f"  Rule ID: {created_rule_id}")
    print(f"  Merchant criteria: {operator} '{merchant}'")
    if category_name:
        print(f"  Category action: {category_name}")
    if account_id:
        print(f"  Account scope: {account_id}")
    if review_status:
        print(f"  Review action: {review_status}")
    if hide_from_reports:
        print("  Hide from reports: true")


async def cmd_update(
    rule_id: str,
    merchant: str | None,
    operator: str | None,
    category_name_or_id: str | None,
    account_id: str | None,
    review_status: str | None,
    hide_from_reports: bool | None,
    outflow_only: bool,
    inflow_only: bool,
    dry_run: bool,
) -> None:
    existing = await fetch_rule(rule_id)
    mm = await get_client()

    input_payload: dict[str, Any] = {"id": rule_id}

    if merchant is not None or operator is not None:
        current_criteria = existing.get("merchantCriteria") or []
        if merchant is None and not current_criteria:
            raise RuntimeError("Cannot infer existing merchant criteria for this rule")
        current_operator = operator or (
            current_criteria[0].get("operator") if current_criteria else "contains"
        )
        current_value = merchant or (
            current_criteria[0].get("value") if current_criteria else None
        )
        input_payload["merchantCriteria"] = [
            {"operator": current_operator, "value": current_value}
        ]

    if category_name_or_id is not None:
        categories = await _fetch_categories(mm)
        category = _resolve_category(categories, category_name_or_id)
        input_payload["setCategoryAction"] = category["id"]

    if account_id is not None:
        input_payload["accountIds"] = [account_id]

    if review_status is not None:
        input_payload["reviewStatusAction"] = review_status

    if hide_from_reports is not None:
        input_payload["setHideFromReportsAction"] = hide_from_reports

    if outflow_only and inflow_only:
        raise RuntimeError("Choose only one of outflow_only or inflow_only")
    if outflow_only:
        input_payload["amountCriteria"] = {"operator": "gt", "isExpense": True, "value": 0}
    elif inflow_only:
        input_payload["amountCriteria"] = {"operator": "gt", "isExpense": False, "value": 0}

    if len(input_payload) == 1:
        raise RuntimeError("No changes requested")

    if dry_run:
        merged_rule = {
            "merchantCriteria": input_payload.get(
                "merchantCriteria",
                existing.get("merchantCriteria"),
            ),
            "setCategoryAction": input_payload.get(
                "setCategoryAction",
                (existing.get("setCategoryAction") or {}).get("id"),
            ),
            "accountIds": input_payload.get(
                "accountIds",
                existing.get("accountIds"),
            ),
            "reviewStatusAction": input_payload.get(
                "reviewStatusAction",
                existing.get("reviewStatusAction"),
            ),
            "setHideFromReportsAction": input_payload.get(
                "setHideFromReportsAction",
                existing.get("setHideFromReportsAction"),
            ),
        }
        data = await mm.gql_call(
            "Common_PreviewTransactionRule",
            PREVIEW_RULE_QUERY,
            {"rule": merged_rule, "offset": 0},
        )
        preview = data["transactionRulePreview"]
        print(f"\n{BOLD}Rule Update Preview{RESET}  ({rule_id})")
        print(f"  Matches: {preview.get('totalCount', 0)}")
        print(f"  Patch: {json.dumps(input_payload, indent=2)}")
        return

    result = await mm.gql_call(
        "Common_UpdateTransactionRuleMutationV2",
        UPDATE_RULE_QUERY,
        {"input": input_payload},
    )
    errors = result["updateTransactionRuleV2"]["errors"]
    message = _format_errors(errors)
    if message:
        print(f"{RED}Update failed:{RESET} {message}")
        sys.exit(1)
    print(f"{GREEN}Updated rule {rule_id}.{RESET}")
    print(f"  Patch: {json.dumps(input_payload, indent=2)}")


async def cmd_delete(rule_id: str) -> None:
    mm = await get_client()
    before_ids = {rule["id"] for rule in await fetch_rules()}
    result = await mm.gql_call(
        "Common_DeleteTransactionRule",
        DELETE_RULE_QUERY,
        {"id": rule_id},
    )
    payload = result["deleteTransactionRule"]
    message = _format_errors(payload.get("errors"))
    if message:
        print(f"{RED}Delete failed:{RESET} {message}")
        sys.exit(1)
    after_ids = {rule["id"] for rule in await fetch_rules()}
    if rule_id in after_ids or rule_id not in before_ids:
        print(f"{RED}Delete failed:{RESET} rule was not removed")
        sys.exit(1)
    print(f"{GREEN}Deleted rule {rule_id}.{RESET}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m monarch.rules",
        description="Manage Monarch transaction rules",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List existing transaction rules")

    def add_rule_args(p: argparse.ArgumentParser, *, include_dry_run: bool) -> None:
        p.add_argument("--merchant", required=True, help="Merchant pattern to match")
        p.add_argument(
            "--operator",
            default="contains",
            choices=["contains", "eq"],
            help="Merchant operator (default: contains)",
        )
        p.add_argument(
            "--category",
            dest="category_name_or_id",
            help="Category name or ID to set when rule matches",
        )
        p.add_argument(
            "--account-id",
            help="Optional account ID scope for the rule",
        )
        p.add_argument(
            "--review-status",
            choices=["reviewed", "needs_review"],
            help="Optional review status action",
        )
        p.add_argument(
            "--hide-from-reports",
            action="store_true",
            help="Hide matched transactions from reports",
        )
        p.add_argument(
            "--outflow-only",
            action="store_true",
            help="Only match outflow transactions",
        )
        p.add_argument(
            "--inflow-only",
            action="store_true",
            help="Only match inflow transactions",
        )
        if include_dry_run:
            p.add_argument(
                "--dry-run",
                action="store_true",
                help="Preview matches instead of creating the rule",
            )

    preview_p = sub.add_parser("preview", help="Preview a rule without creating it")
    add_rule_args(preview_p, include_dry_run=False)

    create_p = sub.add_parser("create", help="Create a transaction rule")
    add_rule_args(create_p, include_dry_run=True)

    update_p = sub.add_parser("update", help="Update an existing transaction rule")
    update_p.add_argument("rule_id", help="Rule ID to update")
    update_p.add_argument("--merchant", help="Updated merchant pattern")
    update_p.add_argument(
        "--operator",
        choices=["contains", "eq"],
        help="Updated merchant operator",
    )
    update_p.add_argument(
        "--category",
        dest="category_name_or_id",
        help="Updated category name or ID",
    )
    update_p.add_argument("--account-id", help="Updated account scope")
    update_p.add_argument(
        "--review-status",
        choices=["reviewed", "needs_review"],
        help="Updated review status action",
    )
    update_p.add_argument(
        "--hide-from-reports",
        dest="hide_from_reports",
        action="store_true",
        help="Set hide-from-reports action to true",
    )
    update_p.add_argument(
        "--show-in-reports",
        dest="hide_from_reports",
        action="store_false",
        help="Set hide-from-reports action to false",
    )
    update_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the patch without applying it",
    )
    update_p.set_defaults(hide_from_reports=None)

    delete_p = sub.add_parser("delete", help="Delete a transaction rule")
    delete_p.add_argument("rule_id", help="Rule ID to delete")

    return parser


async def async_main(args: argparse.Namespace) -> None:
    if args.command == "list":
        await cmd_list()
    elif args.command == "preview":
        await cmd_preview(
            merchant=args.merchant,
            operator=args.operator,
            category_name_or_id=args.category_name_or_id,
            account_id=args.account_id,
            review_status=args.review_status,
            hide_from_reports=args.hide_from_reports,
            outflow_only=args.outflow_only,
            inflow_only=args.inflow_only,
        )
    elif args.command == "create":
        await cmd_create(
            merchant=args.merchant,
            operator=args.operator,
            category_name_or_id=args.category_name_or_id,
            account_id=args.account_id,
            review_status=args.review_status,
            hide_from_reports=args.hide_from_reports,
            outflow_only=args.outflow_only,
            inflow_only=args.inflow_only,
            dry_run=args.dry_run,
        )
    elif args.command == "update":
        await cmd_update(
            rule_id=args.rule_id,
            merchant=args.merchant,
            operator=args.operator,
            category_name_or_id=args.category_name_or_id,
            account_id=args.account_id,
            review_status=args.review_status,
            hide_from_reports=args.hide_from_reports,
            outflow_only=args.outflow_only,
            inflow_only=args.inflow_only,
            dry_run=args.dry_run,
        )
    elif args.command == "delete":
        await cmd_delete(args.rule_id)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(async_main(args))
    except RuntimeError as exc:
        print(f"{RED}Error:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"{RED}Unexpected error:{RESET} {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
