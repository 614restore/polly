"""
Polymarket Live Scanner
Watches for active BTC/crypto price markets on Polymarket, computes model-implied
probability vs market price, and logs divergence signals when thresholds are met.

Run:  python polymarket_scanner.py
      python polymarket_scanner.py --once   (single scan, no loop)
"""

import argparse
import csv
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from config import POLY_CONFIG
from data_fetcher import fetch_binance_ohlcv

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"


# ---------------------------------------------------------------------------
# Polymarket market fetcher
# ---------------------------------------------------------------------------

def fetch_active_btc_markets() -> list[dict]:
    """Fetch active Polymarket markets that match BTC/bitcoin keywords."""
    keywords = POLY_CONFIG["keywords"]
    found = []

    for kw in keywords:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={"limit": 100, "active": "true", "search": kw},
                timeout=10,
            )
            resp.raise_for_status()
            markets = resp.json()
            for m in markets:
                q = m.get("question", "").lower()
                if any(k in q for k in keywords) and not m.get("closed"):
                    found.append(m)
        except Exception as e:
            print(f"  Warning: Gamma API error for '{kw}': {e}")

    # Deduplicate by conditionId
    seen = set()
    unique = []
    for m in found:
        cid = m.get("conditionId", "")
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(m)

    return unique


def fetch_market_price(token_id: str) -> float | None:
    """Fetch current YES token price from CLOB (0–1 implied probability)."""
    try:
        resp = requests.get(
            f"{CLOB_API}/last-trade-price",
            params={"token_id": token_id},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        price = data.get("price")
        return float(price) if price is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# BTC state model
# ---------------------------------------------------------------------------

_btc_cache: tuple[datetime, pd.DataFrame] | None = None
_cache_ttl_minutes = 30


def get_btc_data() -> pd.DataFrame:
    """Return cached BTC OHLCV, refreshing every 30 minutes."""
    global _btc_cache
    now = datetime.now(timezone.utc)
    if _btc_cache is None or (now - _btc_cache[0]).seconds > _cache_ttl_minutes * 60:
        df = fetch_binance_ohlcv("BTCUSDT", "1h", lookback_days=30)
        _btc_cache = (now, df)
    return _btc_cache[1]


def compute_model_prob(df: pd.DataFrame, span: int = 24) -> float:
    """Return current 0–1 model probability that BTC is in an up state."""
    ewma = df["close"].ewm(span=span, adjust=False).mean()
    deviation = (df["close"].iloc[-1] - ewma.iloc[-1]) / ewma.iloc[-1]
    prob = float(1 / (1 + np.exp(-deviation * 30)))
    return prob


def compute_price_deviation(df: pd.DataFrame, span: int = 24) -> float:
    """Return absolute % deviation of current price from EWMA."""
    ewma = df["close"].ewm(span=span, adjust=False).mean()
    dev = abs((df["close"].iloc[-1] - ewma.iloc[-1]) / ewma.iloc[-1])
    return float(dev)


def compute_volume_ratio(df: pd.DataFrame, window: int = 24) -> float:
    """Return latest volume vs rolling average."""
    ratio = df["volume"].iloc[-1] / df["volume"].rolling(window).mean().iloc[-1]
    return float(ratio)


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------

def evaluate_signal(market: dict, model_prob: float, price_dev: float, vol_ratio: float) -> dict | None:
    """
    Check if a market presents a divergence signal worth logging.
    Returns a signal dict or None.
    """
    cfg = POLY_CONFIG
    tokens = market.get("tokens", [])
    if not tokens:
        return None

    # Find YES token (first token, or one labeled "Yes"/"above"/"over")
    yes_token = None
    for t in tokens:
        outcome = t.get("outcome", "").lower()
        if any(x in outcome for x in ["yes", "above", "over", "higher", "bull"]):
            yes_token = t
            break
    if yes_token is None:
        yes_token = tokens[0]

    token_id = yes_token.get("token_id") or yes_token.get("tokenId")
    if not token_id:
        # Fall back to price embedded in market data
        implied_prob = float(yes_token.get("price", 0.5))
    else:
        live_price = fetch_market_price(str(token_id))
        implied_prob = live_price if live_price is not None else float(yes_token.get("price", 0.5))

    divergence = model_prob - implied_prob
    edge = abs(divergence)

    # Apply 3-condition filter
    cond1 = price_dev >= cfg["min_price_deviation_pct"]
    cond2 = edge >= cfg["min_poly_divergence"]
    cond3 = vol_ratio >= cfg["min_volume_ratio"]

    direction = "up" if divergence > 0 else "down"
    signal_fires = cond1 and cond2 and cond3 and edge >= cfg["min_edge"]

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": market.get("question", "")[:80],
        "condition_id": market.get("conditionId", "")[:20],
        "volume": round(float(market.get("volume") or 0), 2),
        "model_prob": round(model_prob, 4),
        "implied_prob": round(implied_prob, 4),
        "divergence": round(divergence, 4),
        "edge": round(edge, 4),
        "price_deviation": round(price_dev, 4),
        "volume_ratio": round(vol_ratio, 4),
        "direction": direction,
        "cond_price_dev": cond1,
        "cond_divergence": cond2,
        "cond_volume": cond3,
        "signal": signal_fires,
    }


# ---------------------------------------------------------------------------
# macOS notification
# ---------------------------------------------------------------------------

def _notify(result: dict):
    """Send a macOS notification banner when a signal fires."""
    import subprocess
    direction = result["direction"].upper()
    edge = result["edge"]
    question = result["question"][:60].replace('"', "'")
    subtitle = f"BET {direction} | Edge {edge:.3f}"
    script = (
        f'display notification "{question}" '
        f'with title "⚡ Polly Signal Fired" '
        f'subtitle "{subtitle}" '
        f'sound name "Glass"'
    )
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5)
    except Exception:
        pass  # Never let notification failure crash the scanner


# ---------------------------------------------------------------------------
# Trade alert formatter
# ---------------------------------------------------------------------------

def format_trade_alert(result: dict, capital: float = 10_000) -> str:
    cfg = POLY_CONFIG
    direction = result["direction"]
    model_p   = result["model_prob"]
    implied_p = result["implied_prob"]
    edge      = result["edge"]

    # Fractional Kelly sizing
    win_prob = model_p if direction == "up" else 1 - model_p
    b = win_prob / max(1 - win_prob, 1e-9)
    k = max(0.0, (b * win_prob - (1 - win_prob)) / b)
    bet_pct   = min(k * cfg["kelly_fraction"], cfg["max_position_pct"])
    bet_size  = round(capital * bet_pct, 2)

    # What to buy: betting YES if direction==up, NO if direction==down
    buy_side  = "YES" if direction == "up" else "NO"
    buy_price = implied_p if direction == "up" else round(1 - implied_p, 4)
    max_price = round(buy_price * 1.02, 4)  # 2% slippage tolerance

    lines = [
        "",
        "┌─────────────────────────────────────────────────────┐",
        "│                ⚡  SIGNAL FIRED  ⚡                  │",
        "├─────────────────────────────────────────────────────┤",
        f"│  Market   : {result['question'][:50]:<50} │" if len(result['question']) <= 50
            else f"│  Market   : {result['question'][:50]:<50} │",
        f"│  Market(2): {result['question'][50:100]:<50} │" if len(result['question']) > 50 else None,
        "├─────────────────────────────────────────────────────┤",
        f"│  ACTION   : BUY {buy_side:<4}  @ ${buy_price:.4f}  (max ${max_price:.4f})     │",
        f"│  BET SIZE : ${bet_size:,.2f}  ({bet_pct*100:.1f}% of capital)           │",
        f"│  SHARES   : ~{bet_size / max(buy_price, 0.01):,.0f} contracts                          │",
        "├─────────────────────────────────────────────────────┤",
        f"│  Model prob : {model_p:.3f}   Market implied: {implied_p:.3f}          │",
        f"│  Edge       : {edge:.3f}   Kelly fraction: {cfg['kelly_fraction']}               │",
        f"│  BTC dev    : {result['price_deviation']:.3f}   Vol ratio    : {result['volume_ratio']:.2f}              │",
        "├─────────────────────────────────────────────────────┤",
        f"│  PAYOUT   : Win = +${bet_size:,.2f}  |  Loss = -${bet_size:,.2f}   │",
        f"│  Timestamp: {result['timestamp'][:19]:<38}  │",
        "└─────────────────────────────────────────────────────┘",
    ]
    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------
# CSV logger
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "timestamp", "question", "condition_id", "volume",
    "model_prob", "implied_prob", "divergence", "edge",
    "price_deviation", "volume_ratio", "direction",
    "cond_price_dev", "cond_divergence", "cond_volume", "signal",
]


def log_signal(row: dict, log_path: Path):
    write_header = not log_path.exists()
    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in FIELDNAMES})


# ---------------------------------------------------------------------------
# Single scan
# ---------------------------------------------------------------------------

def run_scan(verbose: bool = True) -> list[dict]:
    log_path = Path(POLY_CONFIG["log_file"])
    cfg = POLY_CONFIG

    if verbose:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scanning Polymarket for BTC markets...")

    markets = fetch_active_btc_markets()
    if verbose:
        print(f"  Found {len(markets)} active BTC/bitcoin markets")

    if not markets:
        if verbose:
            print("  No markets found. Will retry next cycle.")
        return []

    try:
        df = get_btc_data()
        model_prob = compute_model_prob(df, cfg["ewma_span"])
        price_dev  = compute_price_deviation(df, cfg["ewma_span"])
        vol_ratio  = compute_volume_ratio(df)
    except Exception as e:
        print(f"  Error fetching BTC data: {e}")
        return []

    if verbose:
        btc_price = df["close"].iloc[-1]
        print(f"  BTC: ${btc_price:,.0f} | Model prob (up): {model_prob:.3f} | Price dev: {price_dev:.3f} | Vol ratio: {vol_ratio:.2f}")

    fired = []
    for market in markets:
        result = evaluate_signal(market, model_prob, price_dev, vol_ratio)
        if result is None:
            continue

        log_signal(result, log_path)

        if result["signal"]:
            fired.append(result)
            print(format_trade_alert(result, capital=cfg["initial_capital"]))
            _notify(result)
        elif verbose:
            print(f"  {result['question'][:60]} → model:{result['model_prob']:.3f} mkt:{result['implied_prob']:.3f} edge:{result['edge']:.3f} [{'SIGNAL' if result['signal'] else 'no signal'}]")

    if verbose and not fired:
        print("  No signals this scan. All readings logged to", log_path)

    return fired


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polly — Polymarket Live Scanner")
    parser.add_argument("--once", action="store_true", help="Run one scan and exit")
    args = parser.parse_args()

    if args.once:
        run_scan(verbose=True)
    else:
        interval = POLY_CONFIG["scan_interval_seconds"]
        print(f"Starting live scanner. Scanning every {interval // 60} minutes. Ctrl+C to stop.")
        print(f"Signals logged to: {POLY_CONFIG['log_file']}")
        try:
            while True:
                run_scan(verbose=True)
                print(f"  Next scan in {interval // 60} minutes...")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nScanner stopped.")
