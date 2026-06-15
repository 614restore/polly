"""Fetch OHLCV data from Binance public API (no keys required)."""

import requests
import pandas as pd
from datetime import datetime, timedelta


def fetch_binance_ohlcv(symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
    """Fetch historical OHLCV from Binance public API."""
    end_ms = int(datetime.utcnow().timestamp() * 1000)
    start_ms = int((datetime.utcnow() - timedelta(days=lookback_days)).timestamp() * 1000)

    url = "https://api.binance.com/api/v3/klines"
    all_candles = []
    current_start = start_ms

    interval_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000,
        "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
    }
    chunk_ms = interval_ms.get(interval, 3_600_000) * 1000  # 1000 candles per request

    print(f"Fetching {symbol} {interval} data for last {lookback_days} days...")
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": min(current_start + chunk_ms, end_ms),
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        candles = resp.json()
        if not candles:
            break
        all_candles.extend(candles)
        current_start = candles[-1][0] + interval_ms.get(interval, 3_600_000)

    df = pd.DataFrame(all_candles, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df[["open", "high", "low", "close", "volume"]]
    print(f"  Fetched {len(df)} candles.")
    return df
