"""Central configuration for Polly backtests."""

# --- Crypto Mean-Reversion Config ---
CRYPTO_CONFIG = {
    "symbol": "BTCUSDT",
    "interval": "1h",
    "lookback_days": 180,
    "rolling_window": 24,        # hours for rolling mean
    "ewma_span": 12,             # EWMA span in hours
    "deviation_threshold": 0.02, # 2% deviation from fair value to trigger entry
    "reversion_target": 0.005,   # 0.5% reversion profit target
    "stop_loss": 0.015,          # 1.5% stop loss
    "position_size_pct": 0.10,   # 10% of capital per trade
    "initial_capital": 10_000,
}

# --- Polymarket Divergence Config ---
POLY_CONFIG = {
    "btc_symbol": "BTCUSDT",
    "interval": "1h",
    "lookback_days": 180,
    "initial_capital": 10_000,

    # 3-condition entry filter
    "min_price_deviation_pct": 0.03,   # BTC must deviate 3%+ from EWMA
    "min_poly_divergence": 0.15,       # Polymarket implied prob must diverge 15%+ from model
    "min_volume_ratio": 1.5,           # Volume must be 1.5x the rolling average

    # Position sizing (fractional Kelly)
    "kelly_fraction": 0.25,            # Quarter-Kelly for safety
    "max_position_pct": 0.20,          # Cap at 20% of capital per bet
    "min_edge": 0.05,                  # Minimum edge required to enter

    # Synthetic data seed (replace with real Polymarket API data)
    "use_synthetic_data": True,
    "synthetic_seed": 42,
}
