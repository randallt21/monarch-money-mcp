#!/usr/bin/env python3
"""MonarchMoney MCP Server - Provides access to Monarch Money financial data via MCP protocol."""

import os
import asyncio
import json
import sys
from typing import Any, Dict, Optional, List
from datetime import datetime, date

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities
from mcp.types import Tool, TextContent
from monarchmoney import MonarchMoney

from monarch.client import get_client


def convert_dates_to_strings(obj: Any) -> Any:
    """
    Recursively convert all date/datetime objects to ISO format strings.
    
    This ensures that the data can be serialized by any JSON encoder,
    not just our custom one. This is necessary because the MCP framework
    may attempt to serialize the response before we can use our custom encoder.
    """
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {key: convert_dates_to_strings(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_dates_to_strings(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_dates_to_strings(item) for item in obj)
    else:
        return obj

# Initialize the MCP server
server = Server("monarch-money")

# Global variable to store the MonarchMoney client
mm_client: Optional[MonarchMoney] = None


def _interactive_stdio_detected() -> bool:
    return sys.stdin.isatty() or sys.stdout.isatty()


def _allow_interactive_stdio() -> bool:
    return os.environ.get("MONARCH_ALLOW_INTERACTIVE_SERVER", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


async def initialize_client():
    """Initialize the shared MonarchMoney client."""
    global mm_client
    mm_client = await get_client()
    print("Monarch client initialized")


# Tool definitions
@server.list_tools()
async def list_tools() -> List[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="get_accounts",
            description="Retrieve all linked financial accounts",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        ),
        Tool(
            name="get_transactions",
            description="Fetch transactions with optional filtering",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of transactions to return",
                        "default": 100
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Number of transactions to skip",
                        "default": 0
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format"
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Filter by specific account ID"
                    },
                    "category_id": {
                        "type": "string",
                        "description": "Filter by specific category ID"
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional text search filter"
                    }
                },
                "additionalProperties": False
            }
        ),
        Tool(
            name="get_budgets",
            description="Retrieve budget information",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format"
                    }
                },
                "additionalProperties": False
            }
        ),
        Tool(
            name="get_cashflow",
            description="Analyze cashflow data",
            inputSchema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format"
                    }
                },
                "additionalProperties": False
            }
        ),
        Tool(
            name="get_transaction_categories",
            description="List all transaction categories",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False
            }
        ),
        Tool(
            name="create_transaction",
            description="Create a new transaction",
            inputSchema={
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Transaction amount (negative for expenses)"
                    },
                    "merchant_name": {
                        "type": "string",
                        "description": "Merchant or payee name"
                    },
                    "category_id": {
                        "type": "string",
                        "description": "Category ID for the transaction"
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Account ID for the transaction"
                    },
                    "date": {
                        "type": "string",
                        "description": "Transaction date in YYYY-MM-DD format"
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes for the transaction"
                    }
                },
                "required": ["amount", "merchant_name", "category_id", "account_id", "date"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="update_transaction",
            description="Update an existing transaction",
            inputSchema={
                "type": "object",
                "properties": {
                    "transaction_id": {
                        "type": "string",
                        "description": "ID of the transaction to update"
                    },
                    "amount": {
                        "type": "number",
                        "description": "New transaction amount"
                    },
                    "description": {
                        "type": "string",
                        "description": "New transaction description"
                    },
                    "merchant_name": {
                        "type": "string",
                        "description": "New merchant name"
                    },
                    "category_id": {
                        "type": "string",
                        "description": "New category ID"
                    },
                    "date": {
                        "type": "string",
                        "description": "New transaction date in YYYY-MM-DD format"
                    },
                    "notes": {
                        "type": "string",
                        "description": "New notes for the transaction"
                    },
                    "needs_review": {
                        "type": "boolean",
                        "description": "Set or clear review status"
                    },
                    "hide_from_reports": {
                        "type": "boolean",
                        "description": "Hide or unhide the transaction from reports"
                    }
                },
                "required": ["transaction_id"],
                "additionalProperties": False
            }
        ),
        Tool(
            name="refresh_accounts",
            description="Request a refresh of account data from financial institutions",
            inputSchema={
                "type": "object",
                "properties": {
                    "account_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional account IDs to refresh. Defaults to all active accounts."
                    }
                },
                "additionalProperties": False
            }
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    """Execute a tool and return the results."""
    if not mm_client:
        return [TextContent(type="text", text="Error: MonarchMoney client not initialized")]
    
    try:
        if name == "get_accounts":
            accounts = await mm_client.get_accounts()
            # Convert date objects to strings before serialization
            accounts = convert_dates_to_strings(accounts)
            return [TextContent(type="text", text=json.dumps(accounts, indent=2))]
        
        elif name == "get_transactions":
            # Build filter parameters
            filters = {}
            if "start_date" in arguments:
                datetime.strptime(arguments["start_date"], "%Y-%m-%d")
                filters["start_date"] = arguments["start_date"]
            if "end_date" in arguments:
                datetime.strptime(arguments["end_date"], "%Y-%m-%d")
                filters["end_date"] = arguments["end_date"]
            if "account_id" in arguments:
                filters["account_ids"] = [arguments["account_id"]]
            if "category_id" in arguments:
                filters["category_ids"] = [arguments["category_id"]]
            if "search" in arguments:
                filters["search"] = arguments["search"]
            
            transactions = await mm_client.get_transactions(
                limit=arguments.get("limit", 100),
                offset=arguments.get("offset", 0),
                **filters
            )
            # Convert date objects to strings before serialization
            transactions = convert_dates_to_strings(transactions)
            return [TextContent(type="text", text=json.dumps(transactions, indent=2))]
        
        elif name == "get_budgets":
            kwargs = {}
            if "start_date" in arguments:
                datetime.strptime(arguments["start_date"], "%Y-%m-%d")
                kwargs["start_date"] = arguments["start_date"]
            if "end_date" in arguments:
                datetime.strptime(arguments["end_date"], "%Y-%m-%d")
                kwargs["end_date"] = arguments["end_date"]
            
            try:
                budgets = await mm_client.get_budgets(**kwargs)
                # Convert date objects to strings before serialization
                budgets = convert_dates_to_strings(budgets)
                return [TextContent(type="text", text=json.dumps(budgets, indent=2))]
            except Exception as e:
                # Handle the case where no budgets exist
                if "Something went wrong while processing: None" in str(e):
                    return [TextContent(type="text", text=json.dumps({
                        "budgets": [],
                        "message": "No budgets configured in your Monarch Money account"
                    }, indent=2))]
                else:
                    # Re-raise other errors
                    raise
        
        elif name == "get_cashflow":
            kwargs = {}
            if "start_date" in arguments:
                datetime.strptime(arguments["start_date"], "%Y-%m-%d")
                kwargs["start_date"] = arguments["start_date"]
            if "end_date" in arguments:
                datetime.strptime(arguments["end_date"], "%Y-%m-%d")
                kwargs["end_date"] = arguments["end_date"]
            
            cashflow = await mm_client.get_cashflow(**kwargs)
            # Convert date objects to strings before serialization
            cashflow = convert_dates_to_strings(cashflow)
            return [TextContent(type="text", text=json.dumps(cashflow, indent=2))]
        
        elif name == "get_transaction_categories":
            categories = await mm_client.get_transaction_categories()
            # Convert date objects to strings before serialization
            categories = convert_dates_to_strings(categories)
            return [TextContent(type="text", text=json.dumps(categories, indent=2))]
        
        elif name == "create_transaction":
            datetime.strptime(arguments["date"], "%Y-%m-%d")
            
            result = await mm_client.create_transaction(
                amount=arguments["amount"],
                merchant_name=arguments["merchant_name"],
                category_id=arguments["category_id"],
                account_id=arguments["account_id"],
                date=arguments["date"],
                notes=arguments.get("notes")
            )
            # Convert date objects to strings before serialization
            result = convert_dates_to_strings(result)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "update_transaction":
            # Build update parameters
            updates = {"transaction_id": arguments["transaction_id"]}
            if "amount" in arguments:
                updates["amount"] = arguments["amount"]
            if "description" in arguments:
                updates["merchant_name"] = arguments["description"]
            if "merchant_name" in arguments:
                updates["merchant_name"] = arguments["merchant_name"]
            if "category_id" in arguments:
                updates["category_id"] = arguments["category_id"]
            if "date" in arguments:
                datetime.strptime(arguments["date"], "%Y-%m-%d")
                updates["date"] = arguments["date"]
            if "notes" in arguments:
                updates["notes"] = arguments["notes"]
            if "needs_review" in arguments:
                updates["needs_review"] = arguments["needs_review"]
            if "hide_from_reports" in arguments:
                updates["hide_from_reports"] = arguments["hide_from_reports"]
            
            result = await mm_client.update_transaction(**updates)
            # Convert date objects to strings before serialization
            result = convert_dates_to_strings(result)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        elif name == "refresh_accounts":
            account_ids = arguments.get("account_ids")
            if not account_ids:
                accounts = await mm_client.get_accounts()
                account_ids = [
                    acct["id"]
                    for acct in accounts.get("accounts", [])
                    if acct.get("isActive", True)
                ]
            result = {
                "success": await mm_client.request_accounts_refresh(account_ids),
                "account_ids": account_ids,
            }
            # Convert date objects to strings before serialization
            result = convert_dates_to_strings(result)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        
        else:
            return [TextContent(type="text", text=f"Error: Unknown tool '{name}'")]
    
    except Exception as e:
        return [TextContent(type="text", text=f"Error executing {name}: {str(e)}")]


async def main():
    """Main entry point for the server."""
    if _interactive_stdio_detected() and not _allow_interactive_stdio():
        print(
            "Refusing to start Monarch MCP server in an interactive terminal. "
            "Configure it in your MCP client so it is spawned over stdio pipes, "
            "or set MONARCH_ALLOW_INTERACTIVE_SERVER=1 to override.",
            file=sys.stderr,
        )
        return 2

    # Initialize the MonarchMoney client
    try:
        await initialize_client()
    except Exception as e:
        print(f"Failed to initialize MonarchMoney client: {e}")
        return 1
    
    # Run the MCP server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, 
            write_stream,
            InitializationOptions(
                server_name="monarch-money",
                server_version="1.0.0",
                capabilities=ServerCapabilities(
                    tools={}
                )
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
