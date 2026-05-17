"""
Local policy overrides for deterministic Monarch maintenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "monarch-policies.json"


@dataclass
class MerchantOverride:
    merchant: str
    category: str
    account: str | None = None
    notes: str | None = None


class PolicyConfigError(ValueError):
    """Raised when repo-side policy overrides are malformed."""


def load_merchant_overrides() -> list[MerchantOverride]:
    if not POLICY_PATH.exists():
        return []

    try:
        payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyConfigError(
            f"Invalid JSON in {POLICY_PATH.name}: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc
    if not isinstance(payload, dict):
        raise PolicyConfigError(f"{POLICY_PATH.name} must contain a top-level JSON object")

    rows = payload.get("merchant_overrides", [])
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise PolicyConfigError(f"{POLICY_PATH.name} field `merchant_overrides` must be a list")

    overrides: list[MerchantOverride] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise PolicyConfigError(
                f"{POLICY_PATH.name} override #{index} must be a JSON object"
            )
        merchant = str(row.get("merchant") or "").strip()
        category = str(row.get("category") or "").strip()
        account = str(row.get("account") or "").strip() or None
        notes = str(row.get("notes") or "").strip() or None
        if not merchant or not category:
            raise PolicyConfigError(
                f"{POLICY_PATH.name} override #{index} must include non-empty "
                "`merchant` and `category` values"
            )
        overrides.append(
            MerchantOverride(
                merchant=merchant,
                category=category,
                account=account,
                notes=notes,
            )
        )
    return overrides


def match_merchant_override(
    merchant: str,
    account: str,
    overrides: list[MerchantOverride],
) -> MerchantOverride | None:
    normalized_merchant = merchant.casefold().strip()
    normalized_account = account.casefold().strip()
    for row in overrides:
        if row.merchant.casefold().strip() != normalized_merchant:
            continue
        if row.account and row.account.casefold().strip() != normalized_account:
            continue
        return row
    return None
