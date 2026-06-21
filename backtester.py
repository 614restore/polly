"""
MicroTransactionBacktester
OOP backtesting class for high-frequency mean-reversion simulation.

Run:  python backtester.py
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

from config import CRYPTO_CONFIG
from data_fetcher import fetch_binance_ohlcv

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


class MicroTransactionBacktester:
    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or CRYPTO_CONFIG["initial_capital"]

    def run_crypto_backtest(self, cfg: dict) -> tuple[pd.DataFrame, float]:
        """Simulate high-frequency crypto mean-reversion with circuit breaker."""
        df = fetch_binance_ohlcv(cfg["symbol"], cfg["interval"], cfg["lookback_days"])

        df["rolling_mean"] = df["close"].rolling(cfg["rolling_window"]).mean()
        df["ewma"]         = df["close"].ewm(span=cfg["ewma_span"], adjust=False).mean()
        df["fair_value"]   = (df["rolling_mean"] + df["ewma"]) / 2
        df["deviation"]    = (df["close"] - df["fair_value"]) / df["fair_value"]
        df.dropna(inplace=True)

        capital = self.initial_capital
        trades = []
        in_trade = False
        entry_price = entry_direction = entry_ts = None

        threshold = cfg["deviation_threshold"]
        target    = cfg["reversion_target"]
        stop      = cfg["stop_loss"]
        size_pct  = cfg["position_size_pct"]

        # Circuit breaker state
        consecutive_losses = 0
        max_losses = 3
        circuit_open = False

        for ts, row in df.iterrows():
            if circuit_open:
                break

            if not in_trade:
                if abs(row["deviation"]) >= threshold:
                    entry_price     = row["close"]
                    entry_direction = "long" if row["deviation"] < 0 else "short"
                    entry_ts        = ts
                    in_trade        = True
            else:
                if entry_direction == "long":
                    pnl_pct = (row["close"] - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - row["close"]) / entry_price

                hit_target = pnl_pct >= target
                hit_stop   = pnl_pct <= -stop

                if hit_target or hit_stop:
                    trade_pnl = capital * size_pct * pnl_pct
                    capital  += trade_pnl
                    result    = "win" if hit_target else "loss"

                    if result == "loss":
                        consecutive_losses += 1
                        if consecutive_losses >= max_losses:
                            circuit_open = True
                    else:
                        consecutive_losses = 0

                    trades.append({
                        "entry_time":  entry_ts,
                        "exit_time":   ts,
                        "direction":   entry_direction,
                        "entry_price": entry_price,
                        "exit_price":  row["close"],
                        "pnl_pct":     round(pnl_pct * 100, 3),
                        "pnl_usd":     round(trade_pnl, 2),
                        "capital":     round(capital, 2),
                        "result":      result,
                    })
                    in_trade = False

        if circuit_open:
            print(f"  ⛔ Circuit breaker tripped after {max_losses} consecutive losses.")

        return pd.DataFrame(trades), capital

    def print_summary(self, df: pd.DataFrame, final_capital: float):
        if df.empty:
            print("No trades executed.")
            return
        wins     = df[df["result"] == "win"]
        win_rate = len(wins) / len(df) * 100
        total_pnl = df["pnl_usd"].sum()
        max_dd   = (df["capital"].cummax() - df["capital"]).max()

        print("\n" + "=" * 50)
        print("  MICRO-TRANSACTION BACKTEST RESULTS")
        print("=" * 50)
        print(f"  Trades:        {len(df)}")
        print(f"  Win Rate:      {win_rate:.1f}%")
        print(f"  Total PnL:     ${total_pnl:,.2f}")
        print(f"  Final Capital: ${final_capital:,.2f}")
        print(f"  Return:        {(final_capital - self.initial_capital) / self.initial_capital * 100:.2f}%")
        print(f"  Max Drawdown:  ${max_dd:,.2f}")
        print("=" * 50 + "\n")

    def plot_results(self, df: pd.DataFrame, title: str = "QuantBot — Equity Curve", filename: str = "backtest_results.png"):
        if df.empty:
            return
        equity = [self.initial_capital] + df["capital"].tolist()
        plt.figure(figsize=(12, 5))
        plt.plot(equity, linewidth=1.5, color="steelblue")
        plt.title(title)
        plt.xlabel("Trade #")
        plt.ylabel("Capital ($)")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        path = RESULTS_DIR / filename
        plt.savefig(path, dpi=150)
        print(f"Chart saved to {path}")
        plt.close()


if __name__ == "__main__":
    tester = MicroTransactionBacktester()
    print("Running MicroTransaction Backtest...")
    results, final_cap = tester.run_crypto_backtest(CRYPTO_CONFIG)
    tester.print_summary(results, final_cap)
    tester.plot_results(results)

    if not results.empty:
        out = RESULTS_DIR / "microtx_trades.csv"
        results.to_csv(out, index=False)
        print(f"Trade log saved to {out}")
