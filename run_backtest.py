"""
Crypto Mean-Reversion Backtest
Fair value: blend of rolling mean, VWAP, and EWMA.
Entry: price deviates >= threshold from fair value.
Exit: price reverts to target OR stop loss hit.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from config import CRYPTO_CONFIG
from data_fetcher import fetch_binance_ohlcv

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


def compute_fair_value(df: pd.DataFrame, window: int, ewma_span: int) -> pd.Series:
    rolling_mean = df["close"].rolling(window).mean()
    cumvol = df["volume"].cumsum()
    cumvwap = (df["close"] * df["volume"]).cumsum() / cumvol
    ewma = df["close"].ewm(span=ewma_span, adjust=False).mean()
    return (rolling_mean + cumvwap + ewma) / 3


def run_backtest(cfg: dict) -> pd.DataFrame:
    df = fetch_binance_ohlcv(cfg["symbol"], cfg["interval"], cfg["lookback_days"])
    df["fair_value"] = compute_fair_value(df, cfg["rolling_window"], cfg["ewma_span"])
    df["deviation"] = (df["close"] - df["fair_value"]) / df["fair_value"]
    df.dropna(inplace=True)

    capital = cfg["initial_capital"]
    trades = []
    in_trade = False
    entry_price = entry_direction = None
    entry_idx = None

    threshold = cfg["deviation_threshold"]
    target = cfg["reversion_target"]
    stop = cfg["stop_loss"]
    size_pct = cfg["position_size_pct"]

    for i, (ts, row) in enumerate(df.iterrows()):
        if not in_trade:
            if row["deviation"] <= -threshold:
                # Price below fair value — go long
                entry_price = row["close"]
                entry_direction = "long"
                entry_idx = ts
                in_trade = True
            elif row["deviation"] >= threshold:
                # Price above fair value — go short
                entry_price = row["close"]
                entry_direction = "short"
                entry_idx = ts
                in_trade = True
        else:
            price = row["close"]
            if entry_direction == "long":
                pnl_pct = (price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - price) / entry_price

            hit_target = pnl_pct >= target
            hit_stop = pnl_pct <= -stop

            if hit_target or hit_stop:
                trade_pnl = capital * size_pct * pnl_pct
                capital += trade_pnl
                trades.append({
                    "entry_time": entry_idx,
                    "exit_time": ts,
                    "direction": entry_direction,
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl_pct": round(pnl_pct * 100, 3),
                    "pnl_usd": round(trade_pnl, 2),
                    "capital_after": round(capital, 2),
                    "result": "target" if hit_target else "stop",
                })
                in_trade = False

    results_df = pd.DataFrame(trades)
    return results_df, capital


def print_summary(results_df: pd.DataFrame, final_capital: float, initial: float):
    if results_df.empty:
        print("No trades executed.")
        return

    wins = results_df[results_df["result"] == "target"]
    losses = results_df[results_df["result"] == "stop"]
    total_pnl = results_df["pnl_usd"].sum()
    win_rate = len(wins) / len(results_df) * 100
    max_dd = (results_df["capital_after"].cummax() - results_df["capital_after"]).max()

    print("\n" + "=" * 50)
    print("  CRYPTO MEAN-REVERSION BACKTEST RESULTS")
    print("=" * 50)
    print(f"  Trades:        {len(results_df)}")
    print(f"  Win Rate:      {win_rate:.1f}%")
    print(f"  Total PnL:     ${total_pnl:,.2f}")
    print(f"  Final Capital: ${final_capital:,.2f}")
    print(f"  Return:        {((final_capital - initial) / initial * 100):.2f}%")
    print(f"  Max Drawdown:  ${max_dd:,.2f}")
    print("=" * 50 + "\n")


def plot_equity_curve(results_df: pd.DataFrame, initial: float):
    if results_df.empty:
        return
    equity = [initial] + results_df["capital_after"].tolist()
    plt.figure(figsize=(12, 5))
    plt.plot(equity, linewidth=1.5, color="steelblue")
    plt.title("Crypto Mean-Reversion — Equity Curve")
    plt.xlabel("Trade #")
    plt.ylabel("Capital ($)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    path = RESULTS_DIR / "equity_curve_meanrev.png"
    plt.savefig(path, dpi=150)
    print(f"Equity curve saved to {path}")
    plt.close()


if __name__ == "__main__":
    cfg = CRYPTO_CONFIG
    results_df, final_capital = run_backtest(cfg)

    print_summary(results_df, final_capital, cfg["initial_capital"])

    if not results_df.empty:
        csv_path = RESULTS_DIR / "meanrev_trades.csv"
        results_df.to_csv(csv_path, index=False)
        print(f"Trade log saved to {csv_path}")

    plot_equity_curve(results_df, cfg["initial_capital"])
