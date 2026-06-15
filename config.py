"""Central configuration for Polly."""

# --- Crypto Mean-Reversion Config ---
CRYPTO_CONFIG = {
    "symbol": "BTCUSDT",
    "interval": "1d",              # Daily candles → 720 days (~2 years) from Kraken
    "lookback_days": 720,
    "rolling_window": 20,          # 20-day rolling mean
    "ewma_span": 10,               # 10-day EWMA
    "deviation_threshold": 0.04,   # 4% deviation (daily moves are larger)
    "reversion_target": 0.02,      # 2% reversion target
    "stop_loss": 0.03,             # 3% stop loss
    "position_size_pct": 0.10,
    "initial_capital": 10_000,
}

# --- Polymarket Live Scanner Config ---
POLY_CONFIG = {
    "btc_symbol": "BTCUSDT",
    "interval": "1h",
    "initial_capital": 10_000,

    # Model parameters
    "ewma_span": 24,

    # Entry filter thresholds
    "min_price_deviation_pct": 0.03,
    "min_poly_divergence": 0.10,   # Lowered from 15% → 10% to catch more signals
    "min_volume_ratio": 1.3,       # Lowered from 1.5x → 1.3x

    # Position sizing (fractional Kelly)
    "kelly_fraction": 0.25,
    "max_position_pct": 0.20,
    "min_edge": 0.05,

    # Live scanner settings
    "scan_interval_seconds": 300,  # Check every 5 minutes
    "log_file": "results/live_signals.csv",
    "keywords": ["btc", "bitcoin"],  # Market title keywords to watch
}
