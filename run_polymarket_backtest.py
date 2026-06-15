"""
Polymarket PnL Replayer
Reads the live_signals.csv log produced by polymarket_scanner.py and
computes PnL/win-rate against any resolved outcomes you've recorded.

Usage:
  python run_polymarket_backtest.py                    # replay all logged signals
  python run_polymarket_backtest.py --signals-only     # print signals without PnL calc
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from config import POLY_CONFIG

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)
LOG_FILE = Path(POLY_CONFIG["log_file"])


def load_signals(signals_only: bool = False) -> pd.DataFrame:
    if not LOG_FILE.exists():
        print(f"No signal log found at {LOG_FILE}.")
        print("Run polymarket_scanner.py first to collect live signals.")
        return pd.DataFrame()

    df = pd.read_csv(LOG_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    if signals_only:
        df = df[df["signal"] == True]

    print(f"Loaded {len(df)} log rows, {df['signal'].sum()} signal fires.")
    return df


def replay_pnl(df: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """
    Replay PnL for rows that have an 'actual_outcome' column filled in.
    actual_outcome: 1 = YES resolved, 0 = NO resolved, NaN = pending.

    To record outcomes: open results/live_signals.csv and add a column
    'actual_outcome' with 1 or 0 after each market resolves.
    """
    cfg = POLY_CONFIG
    capital = cfg["initial_capital"]

    if "actual_outcome" not in df.columns:
        print("No 'actual_outcome' column found — cannot replay PnL.")
        print("Add it to live_signals.csv as markets resolve (1=YES, 0=NO).")
        return pd.DataFrame(), capital

    df = df[df["signal"] == True].copy()
    df = df.dropna(subset=["actual_outcome"])

    if df.empty:
        print("No resolved signals to replay yet.")
        return pd.DataFrame(), capital

    trades = []
    for _, row in df.iterrows():
        direction = row["direction"]
        outcome = int(row["actual_outcome"])
        edge = row["edge"]
        model_p = row["model_prob"]

        # Fractional Kelly sizing
        win_prob = model_p if direction == "up" else 1 - model_p
        b = win_prob / max(1 - win_prob, 1e-6)
        k = max(0.0, (b * win_prob - (1 - win_prob)) / b)
        bet_size = min(capital * k * cfg["kelly_fraction"], capital * cfg["max_position_pct"])

        won = (direction == "up" and outcome == 1) or (direction == "down" and outcome == 0)
        pnl = bet_size if won else -bet_size
        capital += pnl

        trades.append({
            "timestamp": row["timestamp"],
            "question": row["question"],
            "direction": direction,
            "model_prob": row["model_prob"],
            "implied_prob": row["implied_prob"],
            "edge": edge,
            "bet_size": round(bet_size, 2),
            "won": won,
            "actual_outcome": outcome,
            "pnl": round(pnl, 2),
            "capital_after": round(capital, 2),
        })

    return pd.DataFrame(trades), capital


def print_summary(results_df: pd.DataFrame, final_capital: float):
    if results_df.empty:
        return
    initial = POLY_CONFIG["initial_capital"]
    wins = results_df["won"].sum()
    total = len(results_df)
    print("\n" + "=" * 55)
    print("  POLYMARKET SIGNAL REPLAY RESULTS")
    print("=" * 55)
    print(f"  Resolved bets: {total}")
    print(f"  Win rate:      {wins / total * 100:.1f}%")
    print(f"  Total PnL:     ${results_df['pnl'].sum():,.2f}")
    print(f"  Final capital: ${final_capital:,.2f}")
    print(f"  Return:        {(final_capital - initial) / initial * 100:.2f}%")
    max_dd = (results_df["capital_after"].cummax() - results_df["capital_after"]).max()
    print(f"  Max drawdown:  ${max_dd:,.2f}")
    print("=" * 55 + "\n")


def plot_equity(results_df: pd.DataFrame):
    if results_df.empty:
        return
    initial = POLY_CONFIG["initial_capital"]
    equity = [initial] + results_df["capital_after"].tolist()
    plt.figure(figsize=(12, 5))
    plt.plot(equity, linewidth=1.5, color="darkorange")
    plt.title("Polymarket Divergence — Replay Equity Curve")
    plt.xlabel("Bet #")
    plt.ylabel("Capital ($)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    path = RESULTS_DIR / "equity_curve_polymarket.png"
    plt.savefig(path, dpi=150)
    print(f"Equity curve saved to {path}")
    plt.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals-only", action="store_true")
    args = parser.parse_args()

    df = load_signals(signals_only=args.signals_only)
    if df.empty:
        exit(0)

    if args.signals_only:
        print(df[["timestamp", "question", "direction", "model_prob", "implied_prob", "edge"]].to_string(index=False))
    else:
        results_df, final_capital = replay_pnl(df)
        print_summary(results_df, final_capital)
        if not results_df.empty:
            out = RESULTS_DIR / "polymarket_trades.csv"
            results_df.to_csv(out, index=False)
            print(f"Trade log saved to {out}")
            plot_equity(results_df)
