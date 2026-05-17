"""
Automatic rule creation from proven transaction history patterns.

A merchant qualifies for a rule when it has ≥3 history observations where the
dominant category appears at least 3 times (so single-sample noise can't create
a rule). Transfer-typed categories are excluded. Existing rules are checked for
duplicates before any new rule is created.

Rules always set reviewStatusAction="reviewed" so future matches never enter the
review queue — this is what makes ongoing categorization truly hands-off.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from monarch.client import get_client
from monarch.rules import (
    CREATE_RULE_QUERY,
    _build_rule_input,
    _format_errors,
    _rule_matches_created_input,
)
from monarch.transactions import transaction_account_name, transaction_merchant_name

MIN_DOMINANT_COUNT = 3


def _find_rule_candidates(
    history_transactions: list[dict[str, Any]],
    existing_rules: list[dict[str, Any]],
    category_lookup: dict[str, str],
    transfer_category_ids: set[str],
) -> list[dict[str, Any]]:
    """
    Return candidate merchants that meet the history threshold for rule creation.

    Each candidate dict has keys: merchant, account, category_id, category_name,
    history_count, dominant_count, confidence.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for txn in history_transactions:
        merchant = transaction_merchant_name(txn)
        account = transaction_account_name(txn)
        if not merchant:
            continue
        cat = txn.get("category") or {}
        cat_id = cat.get("id")
        cat_name = cat.get("name")
        if not cat_id or cat_name in (None, "Uncategorized"):
            continue
        key = (merchant, account)
        grouped.setdefault(key, []).append(txn)

    candidates: list[dict[str, Any]] = []
    for (merchant, account), txns in grouped.items():
        counts: Counter[str] = Counter()
        cat_names: dict[str, str] = {}
        for txn in txns:
            cat = txn.get("category") or {}
            cid = cat.get("id")
            cname = cat.get("name")
            if cid and cname and cname != "Uncategorized":
                counts[cid] += 1
                cat_names[cid] = cname

        if not counts:
            continue

        top_id, top_count = counts.most_common(1)[0]

        # Require at least 3 dominant-category occurrences.
        if top_count < MIN_DOMINANT_COUNT:
            continue

        # Never create rules for transfer categories.
        if top_id in transfer_category_ids:
            continue

        total = sum(counts.values())
        confidence = top_count / total

        # Dedup: skip if a matching rule already exists.
        already_exists = any(
            _rule_matches_created_input(
                rule,
                merchant=merchant,
                operator="contains",
                category_id=top_id,
                account_id=None,
                review_status="reviewed",
                hide_from_reports=False,
                outflow_only=False,
                inflow_only=False,
            )
            for rule in existing_rules
        )
        if already_exists:
            continue

        candidates.append(
            {
                "merchant": merchant,
                "account": account,
                "category_id": top_id,
                "category_name": cat_names[top_id],
                "history_count": total,
                "dominant_count": top_count,
                "confidence": confidence,
            }
        )

    # Dedup by (merchant, category_id) — we create unscoped rules so two candidates
    # for the same merchant+category from different accounts would be redundant.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for cand in candidates:
        key = (cand["merchant"], cand["category_id"])
        if key not in seen:
            seen.add(key)
            deduped.append(cand)

    return deduped


async def auto_create_rules(
    history_transactions: list[dict[str, Any]],
    existing_rules: list[dict[str, Any]],
    category_lookup: dict[str, str],
    transfer_category_ids: set[str],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """
    Identify and optionally create Monarch rules from proven history patterns.

    Returns a list of created (or would-be-created, in dry_run) rule records.
    Each record has: merchant, account, category_name, created (bool), dry_run (bool).
    """
    candidates = _find_rule_candidates(
        history_transactions, existing_rules, category_lookup, transfer_category_ids
    )

    if not candidates:
        return []

    created_rules: list[dict[str, Any]] = []

    if dry_run:
        for cand in candidates:
            created_rules.append(
                {
                    "merchant": cand["merchant"],
                    "account": cand["account"],
                    "category_name": cand["category_name"],
                    "dominant_count": cand["dominant_count"],
                    "confidence": cand["confidence"],
                    "created": False,
                    "dry_run": True,
                }
            )
        return created_rules

    mm = await get_client()
    for cand in candidates:
        rule_input = _build_rule_input(
            merchant=cand["merchant"],
            operator="contains",
            category_id=cand["category_id"],
            account_id=None,
            review_status="reviewed",
            hide_from_reports=False,
            outflow_only=False,
            inflow_only=False,
        )
        try:
            result = await mm.gql_call(
                "Common_CreateTransactionRuleMutationV2",
                CREATE_RULE_QUERY,
                {"input": rule_input},
            )
            errors = result["createTransactionRuleV2"]["errors"]
            error_msg = _format_errors(errors)
            if error_msg:
                created_rules.append(
                    {
                        "merchant": cand["merchant"],
                        "account": cand["account"],
                        "category_name": cand["category_name"],
                        "dominant_count": cand["dominant_count"],
                        "confidence": cand["confidence"],
                        "created": False,
                        "error": error_msg,
                    }
                )
            else:
                created_rules.append(
                    {
                        "merchant": cand["merchant"],
                        "account": cand["account"],
                        "category_name": cand["category_name"],
                        "dominant_count": cand["dominant_count"],
                        "confidence": cand["confidence"],
                        "created": True,
                        "dry_run": False,
                    }
                )
        except Exception as exc:
            created_rules.append(
                {
                    "merchant": cand["merchant"],
                    "account": cand["account"],
                    "category_name": cand["category_name"],
                    "dominant_count": cand["dominant_count"],
                    "confidence": cand["confidence"],
                    "created": False,
                    "error": str(exc),
                }
            )

    return created_rules
