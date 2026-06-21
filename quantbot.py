"""
QuantBot — Main Orchestrator
Runs both pipelines in a single loop with file logging, circuit breakers,
exposure limits, and macOS alerts on signal fire.

Run:
  python quantbot.py          # live loop (Ctrl+C to stop)
  python quantbot.py --once   # single cycle
"""

import argparse
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from config import CRYPTO_CONFIG, POLY_CONFIG
from data_fetcher import fetch_binance_ohlcv
from polymarket_scanner import (
    compute_model_prob,
    compute_price_deviation,
    compute_volume_ratio,
    evaluate_signal,
    fetch_active_btc_markets,
    format_trade_alert,
    log_signal,
    _notify,
)

load_dotenv(Path(__file__).parent / ".env")

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(RESULTS_DIR / "quantbot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """
    Halts trading when consecutive losses exceed the threshold or daily
    drawdown exceeds the limit. Resets at the start of each session.
    """

    def __init__(self, max_consecutive_losses: int = 3, max_daily_drawdown_pct: float = 0.05):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.consecutive_losses = 0
        self.session_start_capital = None
        self.tripped = False
        self.trip_reason = ""

    def reset(self, capital: float):
        self.session_start_capital = capital
        self.consecutive_losses = 0
        self.tripped = False
        self.trip_reason = ""

    def record_win(self):
        self.consecutive_losses = 0

    def record_loss(self, current_capital: float):
        self.consecutive_losses += 1

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.tripped = True
            self.trip_reason = f"{self.consecutive_losses} consecutive losses"

        if self.session_start_capital:
            drawdown = (self.session_start_capital - current_capital) / self.session_start_capital
            if drawdown >= self.max_daily_drawdown_pct:
                self.tripped = True
                self.trip_reason = f"daily drawdown {drawdown*100:.1f}% exceeded {self.max_daily_drawdown_pct*100:.0f}% limit"

    def check(self) -> bool:
        if self.tripped:
            logger.warning(f"⛔ Circuit breaker OPEN — {self.trip_reason}. Trading halted.")
        return self.tripped


# ---------------------------------------------------------------------------
# QuantBot
# ---------------------------------------------------------------------------

class QuantBot:
    def __init__(self):
        self.crypto_cfg = CRYPTO_CONFIG
        self.poly_cfg = POLY_CONFIG
        self.initial_capital = float(os.getenv("INITIAL_CAPITAL", POLY_CONFIG["initial_capital"]))
        self.current_capital = self.initial_capital

        self.circuit_breaker = CircuitBreaker(
            max_consecutive_losses=3,
            max_daily_drawdown_pct=0.05,
        )
        self.circuit_breaker.reset(self.current_capital)

        # Exposure tracking
        self.open_positions = 0
        self.max_open_positions = 3

        # BTC data cache
        self._btc_df = None
        self._btc_fetched_at = None
        self._btc_cache_ttl = 1800  # 30 minutes

        logger.info(f"QuantBot initialized. Capital: ${self.current_capital:,.2f}")

    # -----------------------------------------------------------------------
    # Data
    # -----------------------------------------------------------------------

    def _get_btc_data(self):
        now = datetime.now(timezone.utc).timestamp()
        if self._btc_df is None or (now - (self._btc_fetched_at or 0)) > self._btc_cache_ttl:
            logger.info("Refreshing BTC data...")
            self._btc_df = fetch_binance_ohlcv("BTCUSDT", "1h", lookback_days=30)
            self._btc_fetched_at = now
        return self._btc_df

    # -----------------------------------------------------------------------
    # Crypto pipeline (signal only — no live execution without exchange key)
    # -----------------------------------------------------------------------

    def run_crypto_pipeline(self):
        logger.info("--- Crypto Mean-Reversion Pipeline ---")
        try:
            df = self._get_btc_data()
            price = df["close"].iloc[-1]
            cfg = self.crypto_cfg
            w = cfg["rolling_window"]
            span = cfg["ewma_span"]

            rolling_mean = df["close"].rolling(w).mean().iloc[-1]
            ewma = df["close"].ewm(span=span, adjust=False).mean().iloc[-1]
            fv = (rolling_mean + ewma) / 2
            deviation = (price - fv) / fv

            logger.info(f"BTC: ${price:,.2f} | Fair value: ${fv:,.2f} | Deviation: {deviation:.4f}")

            if abs(deviation) >= cfg["deviation_threshold"]:
                direction = "LONG" if deviation < 0 else "SHORT"
                logger.info(f"  → Crypto signal: {direction} | deviation {deviation:.3f} exceeds threshold {cfg['deviation_threshold']}")
            else:
                logger.info(f"  → No crypto signal (threshold: ±{cfg['deviation_threshold']})")

        except Exception as e:
            logger.error(f"Crypto pipeline error: {e}")

    # -----------------------------------------------------------------------
    # Polymarket pipeline
    # -----------------------------------------------------------------------

    def run_polymarket_pipeline(self):
        logger.info("--- Polymarket Divergence Pipeline ---")

        if self.circuit_breaker.check():
            return

        if self.open_positions >= self.max_open_positions:
            logger.warning(f"Max open positions ({self.max_open_positions}) reached. Skipping.")
            return

        try:
            markets = fetch_active_btc_markets()
            logger.info(f"Found {len(markets)} active BTC markets")
            if not markets:
                return

            df = self._get_btc_data()
            cfg = self.poly_cfg
            model_prob = compute_model_prob(df, cfg["ewma_span"])
            price_dev  = compute_price_deviation(df, cfg["ewma_span"])
            vol_ratio  = compute_volume_ratio(df)

            logger.info(f"BTC: ${df['close'].iloc[-1]:,.0f} | Model prob: {model_prob:.3f} | Dev: {price_dev:.3f} | Vol ratio: {vol_ratio:.2f}")

            for market in markets:
                signal = evaluate_signal(market, model_prob, price_dev, vol_ratio)
                if signal is None:
                    continue

                log_signal(signal, Path(cfg["log_file"]))

                if signal["signal"]:
                    alert = format_trade_alert(signal, capital=self.current_capital)
                    logger.info(f"\n{alert}")
                    _notify(signal)
                    # Live execution hook — requires POLY_PRIVATE_KEY in .env
                    self._execute_polymarket_trade(signal)
                else:
                    logger.info(f"  {signal['question'][:55]} → edge:{signal['edge']:.3f} [no signal]")

        except Exception as e:
            logger.error(f"Polymarket pipeline error: {e}")

    def _execute_polymarket_trade(self, signal: dict):
        """
        Placeholder for live L1 order placement.
        Requires POLY_PRIVATE_KEY in .env and py-clob-client configured.
        Currently logs intent only — uncomment execution when ready.
        """
        private_key = os.getenv("POLY_PRIVATE_KEY", "")
        if not private_key:
            logger.info("  [Execution] POLY_PRIVATE_KEY not set — signal logged, no order placed.")
            return

        # TODO: Wire py-clob-client L1 order here once private key is provided
        # from py_clob_client.client import ClobClient
        # client = ClobClient(host=CLOB_HOST, chain_id=137, key=private_key, ...)
        # order = client.create_market_order(...)
        # client.post_order(order)
        logger.info(f"  [Execution] Would place order: {signal['direction'].upper()} {signal['question'][:50]}")

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def run_once(self):
        self.run_crypto_pipeline()
        self.run_polymarket_pipeline()

    def start(self):
        interval = self.poly_cfg.get("scan_interval_seconds", 300)
        logger.info(f"QuantBot live loop started. Scanning every {interval // 60} min. Ctrl+C to stop.")
        try:
            while True:
                self.run_once()
                logger.info(f"Cycle complete. Capital: ${self.current_capital:,.2f} | Next scan in {interval // 60} min.")
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("QuantBot stopped.")
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polly — QuantBot Orchestrator")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    bot = QuantBot()
    if args.once:
        bot.run_once()
    else:
        bot.start()
