"""
CLI for managing Monarch transaction categories.

Usage:
    uv run python -m monarch.categories list [--group GROUP]
    uv run python -m monarch.categories create --group GROUP --name NAME [--icon ICON]
    uv run python -m monarch.categories update CATEGORY_NAME_OR_ID [--name NAME] [--icon ICON] [--group GROUP]
    uv run python -m monarch.categories delete CATEGORY_ID
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from typing import Any

from gql import gql

from monarch.client import get_client
from monarch.update import _fetch_categories, _resolve_category

BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

GET_CATEGORIES_WITH_ICONS_QUERY = gql(
    """
    query GetCategoriesWithIcons {
      categories {
        id
        order
        name
        icon
        isSystemCategory
        isDisabled
        group {
          id
          name
          type
        }
      }
    }
    """
)

UPDATE_CATEGORY_MUTATION = gql(
    """
    mutation Web_UpdateCategory($input: UpdateCategoryInput!) {
      updateCategory(input: $input) {
        errors {
          ...PayloadErrorFields
          __typename
        }
        category {
          id
          name
          icon
          group {
            id
            name
            __typename
          }
          __typename
        }
        __typename
      }
    }

    fragment PayloadErrorFields on PayloadError {
      fieldErrors {
        field
        messages
        __typename
      }
      message
      code
      __typename
    }
    """
)


def _current_month_start() -> str:
    return datetime.today().replace(day=1).strftime("%Y-%m-%d")


async def fetch_categories_with_icons() -> list[dict[str, Any]]:
    mm = await get_client()
    try:
        data = await mm.gql_call("GetCategoriesWithIcons", GET_CATEGORIES_WITH_ICONS_QUERY)
        return data.get("categories", [])
    except Exception:
        # Fall back to the library query if the richer shape changes.
        data = await mm.get_transaction_categories()
        return data.get("categories", [])


async def fetch_category_groups() -> list[dict[str, Any]]:
    mm = await get_client()
    data = await mm.get_transaction_category_groups()
    return data.get("categoryGroups", [])


def _resolve_group(groups: list[dict[str, Any]], name_or_id: str) -> dict[str, Any]:
    normalized = name_or_id.strip().casefold()

    for group in groups:
        if group.get("id") == name_or_id:
            return group

    exact = [g for g in groups if (g.get("name") or "").casefold() == normalized]
    if exact:
        return exact[0]

    partial = [g for g in groups if normalized in (g.get("name") or "").casefold()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        names = ", ".join(sorted(g.get("name", g.get("id", "")) for g in partial))
        raise RuntimeError(f"Ambiguous group '{name_or_id}'. Matches: {names}")

    raise RuntimeError(f"No category group found for '{name_or_id}'")


async def cmd_list(group_name_or_id: str | None) -> None:
    categories = await fetch_categories_with_icons()

    if group_name_or_id:
        groups = await fetch_category_groups()
        group = _resolve_group(groups, group_name_or_id)
        categories = [
            c for c in categories if ((c.get("group") or {}).get("id") == group["id"])
        ]

    categories.sort(key=lambda c: (((c.get("group") or {}).get("name") or ""), c.get("order", 0), c.get("name", "")))

    current_group = None
    for category in categories:
        group = category.get("group") or {}
        group_name = group.get("name", "—")
        if group_name != current_group:
            if current_group is not None:
                print()
            print(f"{BOLD}{group_name}{RESET}")
            current_group = group_name

        icon = category.get("icon") or "•"
        system_flag = "system" if category.get("isSystemCategory") else "custom"
        disabled_flag = " disabled" if category.get("isDisabled") else ""
        print(
            f"  {icon}  {category.get('name', '—')}  "
            f"{CYAN}{category.get('id', '—')}{RESET}  [{system_flag}{disabled_flag}]"
        )


async def cmd_create(
    *,
    group_name_or_id: str,
    name: str,
    icon: str,
    budget: float | None,
    start_date: str,
    apply_to_future: bool,
) -> None:
    mm = await get_client()
    categories = await _fetch_categories(mm)

    existing = None
    for category in categories:
        if (category.get("name") or "").casefold() == name.casefold():
            existing = category
            break

    if existing:
        print(
            f"{YELLOW}Category already exists:{RESET} {existing['name']} "
            f"({existing['id']})"
        )
        category_id = existing["id"]
    else:
        groups = await fetch_category_groups()
        group = _resolve_group(groups, group_name_or_id)
        response = await mm.create_transaction_category(
            group_id=group["id"],
            transaction_category_name=name,
            icon=icon,
            rollover_start_month=datetime.strptime(start_date, "%Y-%m-%d"),
        )
        payload = response.get("createCategory", {})
        errors = payload.get("errors") or []
        if errors:
            raise RuntimeError(str(errors))

        refreshed = await _fetch_categories(mm)
        created = _resolve_category(refreshed, name)
        category_id = created["id"]
        print(
            f"{GREEN}Created category:{RESET} {name} {icon} "
            f"({category_id}) in {group['name']}"
        )

    if budget is not None:
        await mm.set_budget_amount(
            amount=budget,
            category_id=category_id,
            start_date=start_date,
            apply_to_future=apply_to_future,
        )
        future_note = " and future months" if apply_to_future else ""
        print(
            f"{GREEN}Set budget:{RESET} ${budget:,.2f} starting {start_date}{future_note}"
        )


async def cmd_delete(category_name_or_id: str) -> None:
    mm = await get_client()
    categories = await _fetch_categories(mm)
    category = _resolve_category(categories, category_name_or_id)
    await mm.delete_transaction_category(category["id"])
    print(f"{GREEN}Deleted category:{RESET} {category['name']} ({category['id']})")


async def cmd_update(
    *,
    category_name_or_id: str,
    name: str | None,
    icon: str | None,
    group_name_or_id: str | None,
) -> None:
    if name is None and icon is None and group_name_or_id is None:
        raise RuntimeError("Nothing to update. Provide --name, --icon, or --group.")

    mm = await get_client()
    categories = await _fetch_categories(mm)
    category = _resolve_category(categories, category_name_or_id)

    input_payload: dict[str, Any] = {"id": category["id"]}
    if name is not None:
        input_payload["name"] = name
    if icon is not None:
        input_payload["icon"] = icon
    if group_name_or_id is not None:
        groups = await fetch_category_groups()
        group = _resolve_group(groups, group_name_or_id)
        input_payload["group"] = group["id"]

    response = await mm.gql_call(
        "Web_UpdateCategory",
        UPDATE_CATEGORY_MUTATION,
        variables={"input": input_payload},
    )
    payload = response.get("updateCategory", {})
    errors = payload.get("errors")
    if errors:
        raise RuntimeError(str(errors))

    updated = payload.get("category") or {}
    updated_name = updated.get("name") or category.get("name") or "—"
    updated_icon = updated.get("icon") or icon or category.get("icon") or "•"
    updated_group = ((updated.get("group") or {}).get("name")) or (
        (category.get("group") or {}).get("name")
    ) or "—"
    print(
        f"{GREEN}Updated category:{RESET} {updated_name} {updated_icon} "
        f"({category['id']}) in {updated_group}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Monarch categories")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List categories")
    list_p.add_argument("--group", help="Filter to a specific category group")

    create_p = sub.add_parser("create", help="Create a category")
    create_p.add_argument("--group", required=True, help="Category group name or ID")
    create_p.add_argument("--name", required=True, help="Category name")
    create_p.add_argument("--icon", default="❓", help="Emoji icon")
    create_p.add_argument(
        "--budget",
        type=float,
        help="Optional monthly budget to set immediately",
    )
    create_p.add_argument(
        "--start-date",
        default=_current_month_start(),
        metavar="YYYY-MM-DD",
        help="Budget start date (default: first day of current month)",
    )
    create_p.add_argument(
        "--future",
        action="store_true",
        help="Apply the budget to future months too",
    )

    update_p = sub.add_parser("update", help="Update an existing category")
    update_p.add_argument("category", help="Category name or ID")
    update_p.add_argument("--name", help="Updated category name")
    update_p.add_argument("--icon", help="Updated emoji icon")
    update_p.add_argument("--group", help="Updated category group name or ID")

    delete_p = sub.add_parser("delete", help="Delete a category")
    delete_p.add_argument("category", help="Category name or ID")

    return parser


async def amain(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            await cmd_list(args.group)
        elif args.command == "create":
            await cmd_create(
                group_name_or_id=args.group,
                name=args.name,
                icon=args.icon,
                budget=args.budget,
                start_date=args.start_date,
                apply_to_future=args.future,
            )
        elif args.command == "update":
            await cmd_update(
                category_name_or_id=args.category,
                name=args.name,
                icon=args.icon,
                group_name_or_id=args.group,
            )
        elif args.command == "delete":
            await cmd_delete(args.category)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
