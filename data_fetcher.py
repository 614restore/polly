"""Fetch OHLCV data via yfinance (Yahoo Finance — fast, no keys, US-accessible)."""

import pandas as pd
import yfinance as yf

SYMBOL_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "BTCUSD":  "BTC-USD",
    "BTC":     "BTC-USD",
}

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "1h",  # yfinance has no 4h; use 1h
    "1d": "1d",
}

# yfinance max lookback per interval
MAX_LOOKBACK = {
    "1m": 7, "5m": 60, "15m": 60, "30m": 60,
    "1h": 730, "1d": 3650,
}


def fetch_binance_ohlcv(symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
    """Fetch OHLCV from Yahoo Finance. Fast, reliable, no rate limits."""
    yf_symbol  = SYMBOL_MAP.get(symbol.upper(), symbol)
    yf_interval = INTERVAL_MAP.get(interval, "1h")
    max_days   = MAX_LOOKBACK.get(yf_interval, 730)
    days       = min(lookback_days, max_days)

    print(f"Fetching {yf_symbol} {yf_interval} data for last {days} days (Yahoo Finance)...", flush=True)

    df = yf.download(
        yf_symbol,
        period=f"{days}d",
        interval=yf_interval,
        progress=False,
        auto_adjust=True,
    )

    if df.empty:
        raise RuntimeError(f"No data returned for {yf_symbol}")

    # Flatten MultiIndex columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                             "Close": "close", "Volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df.index.name = "timestamp"
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.dropna(inplace=True)

    print(f"  Fetched {len(df)} candles. Latest close: ${df['close'].iloc[-1]:,.2f}", flush=True)
    return df
