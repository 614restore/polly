"""Fetch OHLCV data from Kraken public API (no keys required, US-accessible)."""

import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# Kraken interval codes (minutes)
KRAKEN_INTERVALS = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}

# Kraken returns max 720 candles per call
KRAKEN_MAX_CANDLES = 720


def fetch_binance_ohlcv(symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
    """Fetch historical OHLCV from Kraken (drop-in replacement, symbol auto-mapped)."""
    # Map common symbol names to Kraken pairs
    symbol_map = {
        "BTCUSDT": "XBTUSD",
        "ETHUSDT": "ETHUSD",
        "SOLUSDT": "SOLUSD",
        "BTCUSD": "XBTUSD",
    }
    kraken_pair = symbol_map.get(symbol.upper(), symbol)
    interval_min = KRAKEN_INTERVALS.get(interval, 60)

    start_ts = int((datetime.utcnow() - timedelta(days=lookback_days)).timestamp())
    url = "https://api.kraken.com/0/public/OHLC"

    all_candles = []
    since = start_ts

    print(f"Fetching {kraken_pair} {interval} data for last {lookback_days} days (Kraken)...")
    while True:
        params = {"pair": kraken_pair, "interval": interval_min, "since": since}
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            raise RuntimeError(f"Kraken API error: {data['error']}")

        result = data["result"]
        pair_key = [k for k in result if k != "last"][0]
        candles = result[pair_key]

        if not candles:
            break

        all_candles.extend(candles)
        last_ts = candles[-1][0]

        # Kraken "last" is the cursor for pagination
        next_since = int(result["last"])
        if next_since <= since or len(candles) < KRAKEN_MAX_CANDLES:
            break
        since = next_since
        time.sleep(0.5)  # respect rate limits

    if not all_candles:
        raise RuntimeError("No data returned from Kraken.")

    df = pd.DataFrame(all_candles, columns=[
        "timestamp", "open", "high", "low", "close", "vwap", "volume", "trades"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    print(f"  Fetched {len(df)} candles.")
    return df
