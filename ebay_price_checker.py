#!/usr/bin/env python3
"""
eBay Sold Price Checker
Searches eBay completed/sold listings for an item, filtered by condition, over the past 60 days.
Requires: pip install requests rich
"""

import os
import sys
import requests
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.prompt import Prompt
    from rich.panel import Panel
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# eBay Finding API endpoint
FINDING_API_URL = "https://svcs.ebay.com/services/search/FindingService/v1"

# Condition IDs and display names
CONDITIONS = {
    "1": ("1000", "New"),
    "2": ("1500", "New other (see details)"),
    "3": ("1750", "New with defects"),
    "4": ("2000", "Manufacturer refurbished"),
    "5": ("2500", "Seller refurbished"),
    "6": ("3000", "Used"),
    "7": ("7000", "For parts or not working"),
    "0": (None, "Any condition"),
}


def get_api_key() -> str:
    """Get eBay App ID from env or prompt user."""
    key = os.environ.get("EBAY_APP_ID", "").strip()
    if key:
        return key
    print("\neBay App ID not found in environment variable EBAY_APP_ID.")
    print("Get your free App ID at: https://developer.ebay.com")
    if RICH_AVAILABLE:
        key = Prompt.ask("Enter your eBay App ID (Client ID)")
    else:
        key = input("Enter your eBay App ID (Client ID): ").strip()
    return key


def search_sold_items(
    app_id: str,
    keywords: str,
    condition_id: Optional[str],
    days: int = 60,
    max_results: int = 100,
) -> list[dict]:
    """Search eBay for sold/completed listings using the Finding API."""
    # Date range: past N days
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": keywords,
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "EndTimeFrom",
        "itemFilter(1).value": start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "itemFilter(2).name": "EndTimeTo",
        "itemFilter(2).value": end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "sortOrder": "EndTimeSoonest",
        "paginationInput.entriesPerPage": min(max_results, 100),
        "paginationInput.pageNumber": 1,
        "outputSelector(0)": "SellerInfo",
        "outputSelector(1)": "PictureURLLarge",
    }

    filter_index = 3
    if condition_id:
        params[f"itemFilter({filter_index}).name"] = "Condition"
        params[f"itemFilter({filter_index}).value"] = condition_id
        filter_index += 1

    try:
        response = requests.get(FINDING_API_URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error contacting eBay API: {e}")
        sys.exit(1)

    data = response.json()

    try:
        result = data["findCompletedItemsResponse"][0]
        ack = result["ack"][0]
        if ack != "Success":
            error_msg = result.get("errorMessage", [{}])[0].get("error", [{}])[0].get("message", ["Unknown error"])[0]
            print(f"eBay API error: {error_msg}")
            sys.exit(1)

        total_entries = int(result["paginationOutput"][0]["totalEntries"][0])
        if total_entries == 0:
            return []

        items = result.get("searchResult", [{}])[0].get("item", [])
        return items

    except (KeyError, IndexError) as e:
        print(f"Unexpected API response format: {e}")
        print("Raw response:", response.text[:500])
        sys.exit(1)


def parse_item(item: dict) -> dict:
    """Extract relevant fields from a raw eBay API item."""
    price = float(item["sellingStatus"][0]["currentPrice"][0]["__value__"])
    currency = item["sellingStatus"][0]["currentPrice"][0]["@currencyId"]
    title = item["title"][0]
    item_id = item["itemId"][0]
    end_time = item["listingInfo"][0]["endTime"][0]
    url = item["viewItemURL"][0]
    condition = item.get("condition", [{}])[0].get("conditionDisplayName", ["Unknown"])[0]

    # Parse date
    sold_date = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    return {
        "title": title,
        "price": price,
        "currency": currency,
        "condition": condition,
        "sold_date": sold_date,
        "url": url,
        "item_id": item_id,
    }


def print_results_rich(items: list[dict], keywords: str, condition_name: str, days: int):
    """Display results using rich library."""
    console = Console()

    prices = [i["price"] for i in items]
    avg_price = statistics.mean(prices)
    median_price = statistics.median(prices)
    min_price = min(prices)
    max_price = max(prices)

    console.print()
    console.print(Panel(
        f"[bold]Search:[/bold] {keywords}\n"
        f"[bold]Condition:[/bold] {condition_name}  |  "
        f"[bold]Period:[/bold] Last {days} days  |  "
        f"[bold]Results:[/bold] {len(items)} sold listings",
        title="[bold green]eBay Sold Price Checker[/bold green]",
        border_style="green",
    ))

    # Stats panel
    stats = (
        f"  [green]Avg:[/green]    ${avg_price:>8.2f}\n"
        f"  [blue]Median:[/blue] ${median_price:>8.2f}\n"
        f"  [red]Min:[/red]    ${min_price:>8.2f}\n"
        f"  [yellow]Max:[/yellow]    ${max_price:>8.2f}"
    )
    console.print(Panel(stats, title="[bold]Price Summary[/bold]", border_style="blue"))

    # Results table
    table = Table(show_header=True, header_style="bold magenta", border_style="dim")
    table.add_column("#", style="dim", width=4)
    table.add_column("Sold Date", width=12)
    table.add_column("Price", justify="right", width=10)
    table.add_column("Condition", width=20)
    table.add_column("Title", max_width=55)

    for i, item in enumerate(items, 1):
        date_str = item["sold_date"].strftime("%Y-%m-%d")
        price_str = f"${item['price']:,.2f}"
        table.add_row(
            str(i),
            date_str,
            f"[green]{price_str}[/green]",
            item["condition"],
            item["title"],
        )

    console.print(table)
    console.print(f"\n[dim]URLs for first 5 results:[/dim]")
    for item in items[:5]:
        console.print(f"  [link={item['url']}]{item['url']}[/link]")
    console.print()


def print_results_plain(items: list[dict], keywords: str, condition_name: str, days: int):
    """Plain text fallback display."""
    prices = [i["price"] for i in items]
    print(f"\n{'='*70}")
    print(f"eBay Sold Price Checker")
    print(f"Search: {keywords} | Condition: {condition_name} | Last {days} days")
    print(f"Found {len(items)} sold listings")
    print(f"{'='*70}")
    print(f"Avg:    ${statistics.mean(prices):.2f}")
    print(f"Median: ${statistics.median(prices):.2f}")
    print(f"Min:    ${min(prices):.2f}")
    print(f"Max:    ${max(prices):.2f}")
    print(f"{'-'*70}")
    print(f"{'#':<4} {'Date':<12} {'Price':>10}  {'Condition':<20}  Title")
    print(f"{'-'*70}")
    for i, item in enumerate(items, 1):
        date_str = item["sold_date"].strftime("%Y-%m-%d")
        print(f"{i:<4} {date_str:<12} ${item['price']:>9,.2f}  {item['condition']:<20}  {item['title'][:50]}")
    print()


def select_condition() -> tuple[Optional[str], str]:
    """Interactively select an item condition."""
    print("\nItem Conditions:")
    for key, (cid, name) in CONDITIONS.items():
        print(f"  {key}) {name}")

    if RICH_AVAILABLE:
        choice = Prompt.ask("Select condition", choices=list(CONDITIONS.keys()), default="0")
    else:
        choice = input("Select condition (0-7) [default: 0 for any]: ").strip() or "0"
        while choice not in CONDITIONS:
            choice = input("Invalid choice. Select condition (0-7): ").strip() or "0"

    condition_id, condition_name = CONDITIONS[choice]
    return condition_id, condition_name


def main():
    if RICH_AVAILABLE:
        console = Console()
        console.print("\n[bold green]eBay Sold Price Checker[/bold green] — finds sold listings from the past 60 days\n")
    else:
        print("\neBay Sold Price Checker — finds sold listings from the past 60 days\n")

    app_id = get_api_key()

    while True:
        if RICH_AVAILABLE:
            keywords = Prompt.ask("\nEnter item to search (or 'quit' to exit)")
        else:
            keywords = input("\nEnter item to search (or 'quit' to exit): ").strip()

        if keywords.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        if not keywords:
            print("Please enter a search term.")
            continue

        condition_id, condition_name = select_condition()

        print(f"\nSearching eBay for '{keywords}' (condition: {condition_name})...")

        raw_items = search_sold_items(app_id, keywords, condition_id, days=60, max_results=100)

        if not raw_items:
            print(f"No sold listings found for '{keywords}' in the past 60 days with the selected condition.")
            continue

        items = [parse_item(i) for i in raw_items]
        # Sort by sold date, newest first
        items.sort(key=lambda x: x["sold_date"], reverse=True)

        if RICH_AVAILABLE:
            print_results_rich(items, keywords, condition_name, days=60)
        else:
            print_results_plain(items, keywords, condition_name, days=60)

        if not RICH_AVAILABLE:
            print("\nTip: Install 'rich' for a better display: pip install rich")


if __name__ == "__main__":
    main()
