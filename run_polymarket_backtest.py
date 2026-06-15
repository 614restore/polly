"""
Polymarket Divergence Backtest
BTC state estimate vs Polymarket binary contract implied probability.
3-condition entry filter, fractional-Kelly sizing, PnL vs actual outcomes.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from config import POLY_CONFIG
from data_fetcher import fetch_binance_ohlcv

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Model: BTC State Estimator
# ---------------------------------------------------------------------------

def compute_btc_state(df: pd.DataFrame, span: int = 24) -> pd.Series:
    """Return a 0–1 probability that BTC is in an 'up' state."""
    ewma = df["close"].ewm(span=span, adjust=False).mean()
    deviation = (df["close"] - ewma) / ewma

    # Sigmoid transform deviation into probability
    prob = 1 / (1 + np.exp(-deviation * 30))
    return prob


def compute_volume_ratio(df: pd.DataFrame, window: int = 24) -> pd.Series:
    return df["volume"] / df["volume"].rolling(window).mean()


# ---------------------------------------------------------------------------
# Synthetic Polymarket Data
# Replace this with real Polymarket API data for live use.
# ---------------------------------------------------------------------------

def generate_synthetic_polymarket(df: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """
    Simulate Polymarket implied probabilities for 'BTC up next 24h' contracts.
    Synthetic data adds noise + lag to the true BTC direction, mimicking crowd sentiment.
    """
    rng = np.random.default_rng(seed)
    true_direction = (df["close"].pct_change(24).shift(-24) > 0).astype(float)

    # Crowd implied probability: correlated with truth but noisy
    noise = rng.normal(0, 0.12, len(df))
    implied_prob = (true_direction * 0.7 + 0.15 + noise).clip(0.05, 0.95)

    # Actual outcome: did BTC go up in next 24h?
    actual_outcome = true_direction.shift(-24)  # forward-looking (for scoring only)

    poly_df = pd.DataFrame({
        "implied_prob": implied_prob,
        "actual_outcome": actual_outcome,
    }, index=df.index)

    return poly_df


def load_polymarket_data(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Hook to load real Polymarket data.
    Currently falls back to synthetic. Wire in real data here.

    Real data sources:
      - Polymarket CLOB API: https://clob.polymarket.com
      - Gamma Markets API:   https://gamma-api.polymarket.com
    """
    if cfg["use_synthetic_data"]:
        print("Using synthetic Polymarket data (set use_synthetic_data=False for real data).")
        return generate_synthetic_polymarket(df, cfg["synthetic_seed"])

    # TODO: implement real Polymarket fetch
    raise NotImplementedError("Real Polymarket data fetch not yet implemented.")


# ---------------------------------------------------------------------------
# Entry Filter: 3 conditions must ALL be true
# ---------------------------------------------------------------------------

def compute_signals(df: pd.DataFrame, poly_df: pd.DataFrame, model_prob: pd.Series,
                    vol_ratio: pd.Series, cfg: dict) -> pd.DataFrame:
    combined = df.join(poly_df).copy()
    combined["model_prob"] = model_prob
    combined["vol_ratio"] = vol_ratio
    combined["divergence"] = model_prob - poly_df["implied_prob"]

    ewma = df["close"].ewm(span=24, adjust=False).mean()
    combined["price_deviation"] = ((df["close"] - ewma) / ewma).abs()

    # 3-condition filter
    combined["cond_deviation"] = combined["price_deviation"] >= cfg["min_price_deviation_pct"]
    combined["cond_divergence"] = combined["divergence"].abs() >= cfg["min_poly_divergence"]
    combined["cond_volume"] = combined["vol_ratio"] >= cfg["min_volume_ratio"]
    combined["signal"] = combined["cond_deviation"] & combined["cond_divergence"] & combined["cond_volume"]

    return combined


# ---------------------------------------------------------------------------
# Fractional Kelly Sizing
# ---------------------------------------------------------------------------

def kelly_position(edge: float, win_prob: float, kelly_fraction: float,
                   capital: float, max_pct: float) -> float:
    """Fractional Kelly bet size in dollars."""
    if win_prob <= 0 or win_prob >= 1 or edge <= 0:
        return 0.0
    b = win_prob / (1 - win_prob)  # odds ratio
    k = (b * win_prob - (1 - win_prob)) / b
    fractional_k = max(0.0, k * kelly_fraction)
    return min(capital * fractional_k, capital * max_pct)


# ---------------------------------------------------------------------------
# Backtest Loop
# ---------------------------------------------------------------------------

def run_polymarket_backtest(cfg: dict):
    df = fetch_binance_ohlcv(cfg["btc_symbol"], cfg["interval"], cfg["lookback_days"])
    poly_df = load_polymarket_data(df, cfg)
    model_prob = compute_btc_state(df)
    vol_ratio = compute_volume_ratio(df)

    combined = compute_signals(df, poly_df, model_prob, vol_ratio, cfg)
    combined.dropna(inplace=True)

    capital = cfg["initial_capital"]
    trades = []
    total_signals = combined["signal"].sum()
    total_rows = len(combined)

    print(f"\nTotal candles evaluated: {total_rows}")
    print(f"Signals triggered:       {total_signals} ({total_signals / total_rows * 100:.2f}% entry rate)")

    for ts, row in combined.iterrows():
        if not row["signal"]:
            continue

        divergence = row["divergence"]
        model_p = row["model_prob"]
        implied_p = row["implied_prob"]
        outcome = row["actual_outcome"]

        if pd.isna(outcome):
            continue

        # Determine bet direction: bet WITH model when divergence is large
        if divergence > 0:
            # Model says higher prob than market — bet BTC goes up
            bet_win_prob = model_p
            edge = divergence
            direction = "up"
        else:
            # Model says lower prob than market — bet BTC goes down
            bet_win_prob = 1 - model_p
            edge = abs(divergence)
            direction = "down"

        if edge < cfg["min_edge"]:
            continue

        bet_size = kelly_position(edge, bet_win_prob, cfg["kelly_fraction"],
                                  capital, cfg["max_position_pct"])
        if bet_size <= 0:
            continue

        # Payout: binary market pays 1:1 (win doubles bet, loss loses bet)
        won = (direction == "up" and outcome == 1) or (direction == "down" and outcome == 0)
        pnl = bet_size if won else -bet_size
        capital += pnl

        trades.append({
            "timestamp": ts,
            "direction": direction,
            "model_prob": round(model_p, 4),
            "implied_prob": round(implied_p, 4),
            "divergence": round(divergence, 4),
            "edge": round(edge, 4),
            "bet_size": round(bet_size, 2),
            "won": won,
            "pnl": round(pnl, 2),
            "capital_after": round(capital, 2),
        })

    return pd.DataFrame(trades), capital


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_poly_summary(results_df: pd.DataFrame, final_capital: float, initial: float):
    if results_df.empty:
        print("No trades executed.")
        return

    wins = results_df["won"].sum()
    total = len(results_df)
    win_rate = wins / total * 100
    total_pnl = results_df["pnl"].sum()
    max_dd = (results_df["capital_after"].cummax() - results_df["capital_after"]).max()
    avg_edge = results_df["edge"].mean()

    print("\n" + "=" * 55)
    print("  POLYMARKET DIVERGENCE BACKTEST RESULTS")
    print("=" * 55)
    print(f"  Bets Placed:   {total}")
    print(f"  Win Rate:      {win_rate:.1f}%")
    print(f"  Avg Edge:      {avg_edge:.3f}")
    print(f"  Total PnL:     ${total_pnl:,.2f}")
    print(f"  Final Capital: ${final_capital:,.2f}")
    print(f"  Return:        {((final_capital - initial) / initial * 100):.2f}%")
    print(f"  Max Drawdown:  ${max_dd:,.2f}")
    print("=" * 55)
    print()
    print("  NOTE: 'Double the account' targets require sustained")
    print("  edges >> 5%. Fractional Kelly protects against ruin")
    print("  but small edges compound slowly. Manage expectations.")
    print("=" * 55 + "\n")


def plot_poly_equity(results_df: pd.DataFrame, initial: float):
    if results_df.empty:
        return
    equity = [initial] + results_df["capital_after"].tolist()
    plt.figure(figsize=(12, 5))
    plt.plot(equity, linewidth=1.5, color="darkorange")
    plt.title("Polymarket Divergence — Equity Curve")
    plt.xlabel("Bet #")
    plt.ylabel("Capital ($)")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    path = RESULTS_DIR / "equity_curve_polymarket.png"
    plt.savefig(path, dpi=150)
    print(f"Equity curve saved to {path}")
    plt.close()


if __name__ == "__main__":
    cfg = POLY_CONFIG
    results_df, final_capital = run_polymarket_backtest(cfg)

    print_poly_summary(results_df, final_capital, cfg["initial_capital"])

    if not results_df.empty:
        csv_path = RESULTS_DIR / "polymarket_trades.csv"
        results_df.to_csv(csv_path, index=False)
        print(f"Trade log saved to {csv_path}")

    plot_poly_equity(results_df, cfg["initial_capital"])
