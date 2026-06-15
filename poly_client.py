"""
Authenticated Polymarket CLOB client.
Loads credentials from .env and provides market data + order management.

The CLOB API uses two auth levels:
  L1 — wallet signature (for order placement, requires private key)
  L2 — API key (for reading positions, orders, market data)

This module covers L2 (read + market data) via the API key.
For L1 order placement, the user must sign via their wallet/private key.
"""

import os
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_KEY    = os.getenv("POLY_API_KEY", "")
WALLET     = os.getenv("POLY_WALLET", "")
CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
DATA_HOST  = "https://data-api.polymarket.com"

HEADERS = {
    "POLY-API-KEY": API_KEY,
    "Content-Type": "application/json",
}


def _get(url: str, params: dict = None, auth: bool = False) -> dict | list:
    headers = HEADERS if auth else {}
    resp = requests.get(url, params=params, headers=headers, timeout=12)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

def get_markets(keyword: str = "", limit: int = 100, active: bool = True) -> list[dict]:
    """Search active Polymarket markets."""
    params = {"limit": limit, "active": str(active).lower()}
    if keyword:
        params["search"] = keyword
    return _get(f"{GAMMA_HOST}/markets", params=params)


def get_market_by_condition(condition_id: str) -> dict:
    """Fetch a single market by conditionId."""
    return _get(f"{GAMMA_HOST}/markets/{condition_id}")


def get_price_history(token_id: str, interval: str = "1d", fidelity: int = 60) -> list[dict]:
    """
    Fetch price history for a token from CLOB.
    interval: 1m, 1h, 6h, 1d, 1w, max
    fidelity: minutes per data point
    """
    data = _get(f"{CLOB_HOST}/prices-history", params={
        "market": token_id,
        "interval": interval,
        "fidelity": fidelity,
    }, auth=True)
    return data.get("history", [])


def get_order_book(token_id: str) -> dict:
    """Fetch live order book for a token."""
    return _get(f"{CLOB_HOST}/book", params={"token_id": token_id}, auth=True)


# ---------------------------------------------------------------------------
# Account data (requires API key)
# ---------------------------------------------------------------------------

def get_positions(wallet: str = WALLET) -> list[dict]:
    """Fetch current open positions for the wallet."""
    return _get(f"{DATA_HOST}/positions", params={"user": wallet, "limit": 100})


def get_portfolio_value(wallet: str = WALLET) -> dict:
    """Fetch portfolio value summary."""
    try:
        return _get(f"{DATA_HOST}/portfolio", params={"user": wallet})
    except Exception:
        return {}


def get_trade_history(wallet: str = WALLET, limit: int = 50) -> list[dict]:
    """Fetch historical trades for the wallet."""
    return _get(f"{DATA_HOST}/activity", params={"user": wallet, "limit": limit})


def get_open_orders(wallet: str = WALLET) -> list[dict]:
    """Fetch open orders for wallet. Returns empty list if endpoint unavailable."""
    try:
        data = _get(f"{CLOB_HOST}/orders", params={"maker_address": wallet}, auth=True)
        return data if isinstance(data, list) else data.get("data", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# CLI — run to verify credentials
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Polly — Polymarket Client Verification ===\n")
    print(f"Wallet:  {WALLET}")
    print(f"API Key: {API_KEY[:8]}...{API_KEY[-4:]}\n")

    print("Checking open positions...")
    positions = get_positions()
    if positions:
        for p in positions:
            print(f"  {p.get('market','')[:60]} | size: {p.get('size')} | value: ${p.get('currentValue','?')}")
    else:
        print("  No open positions.")

    print("\nChecking trade history...")
    trades = get_trade_history(limit=5)
    if trades:
        for t in trades:
            print(f"  {t.get('timestamp','')[:10]} | {t.get('market','')[:50]} | {t.get('side','?')} ${t.get('usdcSize','?')}")
    else:
        print("  No trade history found.")

    print("\nChecking open orders...")
    orders = get_open_orders()
    if orders:
        for o in orders:
            print(f"  {o.get('market','')[:50]} | {o.get('side','?')} @ {o.get('price','?')}")
    else:
        print("  No open orders.")

    print("\nFetching BTC markets...")
    markets = get_markets("bitcoin")
    btc = [m for m in markets if any(x in m.get("question","").lower() for x in ["btc","bitcoin"])]
    for m in btc[:5]:
        print(f"  {m.get('question','')[:70]} | vol: ${float(m.get('volume') or 0):,.0f}")

    print("\nDone.")
