# Polly — Quantitative Backtest Engine

Two research pipelines for crypto mean-reversion and Polymarket divergence trading.

---

## Quick Start

```bash
cd polly
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run crypto mean-reversion backtest
python run_backtest.py

# Run Polymarket divergence backtest
python run_polymarket_backtest.py
```

Results land in `results/` as CSVs and equity curve plots.

---

## Pipeline 1 — Crypto Mean-Reversion (`run_backtest.py`)

**Fair value** = average of rolling mean, VWAP, and EWMA of BTC price.

**Entry:** Price deviates ≥ `deviation_threshold` (default 2%) from fair value.  
**Exit:** Price reverts `reversion_target` (0.5% profit) OR hits `stop_loss` (1.5%).

### Config (`config.py → CRYPTO_CONFIG`)

| Key | Default | Meaning |
|-----|---------|---------|
| `rolling_window` | 24 | Hours for rolling mean |
| `ewma_span` | 12 | EWMA span in hours |
| `deviation_threshold` | 0.02 | Entry deviation from fair value |
| `reversion_target` | 0.005 | Profit exit target |
| `stop_loss` | 0.015 | Loss exit |
| `position_size_pct` | 0.10 | % of capital per trade |

### Output Columns (`meanrev_trades.csv`)

| Column | Meaning |
|--------|---------|
| `entry_time` / `exit_time` | Trade timestamps |
| `direction` | `long` or `short` |
| `pnl_pct` | % PnL on the trade |
| `pnl_usd` | Dollar PnL |
| `result` | `target` (won) or `stop` (loss) |

---

## Pipeline 2 — Polymarket Divergence (`run_polymarket_backtest.py`)

**Model:** Sigmoid-transformed EWMA deviation → 0–1 probability BTC is in an "up" state.

**Entry requires ALL 3 conditions:**
1. BTC price deviation from EWMA ≥ `min_price_deviation_pct` (3%)
2. Model prob vs market implied prob gap ≥ `min_poly_divergence` (15%)
3. Volume ≥ `min_volume_ratio` × rolling average (1.5×)

This filter intentionally rejects ~99%+ of candles to only bet when all signals align.

**Sizing:** Fractional Kelly (quarter-Kelly by default). Capped at 20% of capital per bet.

### Why Not "Double the Account" Fast?

At small edges (5–10%), even optimal Kelly compounds slowly. A run of losses can be brutal without fractional sizing. Quarter-Kelly is the right call here until edge is validated with real data.

### Wiring in Real Polymarket Data

In `run_polymarket_backtest.py`, replace the `load_polymarket_data` function body with a call to:
- **Polymarket CLOB API:** `https://clob.polymarket.com` — market data and order books
- **Gamma Markets API:** `https://gamma-api.polymarket.com` — historical resolution data

Set `use_synthetic_data: False` in `config.py` after wiring in real data.

---

## Data Source

BTC OHLCV data fetched from Binance public API — no API key required.

---

*Research only. No live trading, no accounts, no keys.*
