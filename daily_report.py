"""
Daily Summary Email
Reads results/quantbot.log and results/live_signals.csv from the last 24 hours
and sends a summary email via Gmail SMTP.

Run manually:   python daily_report.py
Scheduled:      add to crontab — see README for setup instructions
"""

import csv
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

RESULTS_DIR = Path("results")
LOG_FILE     = RESULTS_DIR / "quantbot.log"
SIGNALS_FILE = RESULTS_DIR / "live_signals.csv"

GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_PASS = os.getenv("GMAIL_PASS", "")   # Gmail App Password
TO_EMAIL   = os.getenv("REPORT_EMAIL", GMAIL_USER)


# ---------------------------------------------------------------------------
# Parse log
# ---------------------------------------------------------------------------

def parse_log_last_24h() -> list[str]:
    if not LOG_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    lines = []
    for line in LOG_FILE.read_text().splitlines():
        try:
            # Format: 2026-06-21 18:58:15,885 [INFO] ...
            ts_str = line[:23]
            ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                lines.append(line)
        except Exception:
            continue
    return lines


def parse_signals_last_24h() -> list[dict]:
    if not SIGNALS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = []
    with open(SIGNALS_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    rows.append(row)
            except Exception:
                continue
    return rows


# ---------------------------------------------------------------------------
# Build report
# ---------------------------------------------------------------------------

def build_report() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    log_lines  = parse_log_last_24h()
    all_signals = parse_signals_last_24h()
    fired       = [s for s in all_signals if s.get("signal") == "True"]
    scans       = len([l for l in log_lines if "Cycle complete" in l])

    # Latest BTC price from log
    btc_price = "N/A"
    for line in reversed(log_lines):
        if "BTC:" in line and "Fair value" in line:
            try:
                btc_price = line.split("BTC: $")[1].split(" ")[0]
            except Exception:
                pass
            break

    # Latest model prob
    model_prob = "N/A"
    for line in reversed(log_lines):
        if "Model prob:" in line:
            try:
                model_prob = line.split("Model prob: ")[1].split(" ")[0]
            except Exception:
                pass
            break

    subject = f"Polly Daily Report — {now.strftime('%b %d, %Y')} | {'⚡ SIGNAL FIRED' if fired else 'No signals'}"

    html = f"""
<html><body style="font-family: monospace; background: #0d0d0d; color: #e0e0e0; padding: 20px;">
<h2 style="color: #f0a500;">⚡ Polly QuantBot — Daily Summary</h2>
<p style="color: #888;">{now.strftime('%A, %B %d %Y at %H:%M UTC')}</p>

<table style="border-collapse: collapse; width: 100%; max-width: 500px;">
  <tr><td style="padding: 6px; color: #888;">BTC Price</td>
      <td style="padding: 6px; color: #fff;">${btc_price}</td></tr>
  <tr><td style="padding: 6px; color: #888;">Model Prob (up)</td>
      <td style="padding: 6px; color: #fff;">{model_prob}</td></tr>
  <tr><td style="padding: 6px; color: #888;">Scans completed</td>
      <td style="padding: 6px; color: #fff;">{scans}</td></tr>
  <tr><td style="padding: 6px; color: #888;">Markets checked</td>
      <td style="padding: 6px; color: #fff;">{len(all_signals)}</td></tr>
  <tr><td style="padding: 6px; color: #888;">Signals fired</td>
      <td style="padding: 6px; color: {'#00ff88' if fired else '#ff4444'};">
        {len(fired)} {'🔥' if fired else '—'}</td></tr>
</table>
"""

    if fired:
        html += "<h3 style='color: #00ff88; margin-top: 20px;'>⚡ Signals</h3>"
        for s in fired:
            direction = s.get("direction", "?").upper()
            html += f"""
<div style="background: #1a1a1a; border-left: 3px solid #f0a500; padding: 12px; margin: 8px 0;">
  <div style="color: #f0a500; font-weight: bold;">{s.get('question','')[:70]}</div>
  <div style="color: #ccc; margin-top: 6px;">
    BET {direction} &nbsp;|&nbsp;
    Model: {s.get('model_prob','?')} &nbsp;|&nbsp;
    Market: {s.get('implied_prob','?')} &nbsp;|&nbsp;
    Edge: {s.get('edge','?')}
  </div>
  <div style="color: #888; font-size: 12px; margin-top: 4px;">{s.get('timestamp','')[:19]} UTC</div>
</div>"""
    else:
        html += "<p style='color: #888; margin-top: 20px;'>No signals fired in the last 24 hours. Bot is watching.</p>"

    # Errors from log
    errors = [l for l in log_lines if "[ERROR]" in l]
    if errors:
        html += "<h3 style='color: #ff4444; margin-top: 20px;'>⚠️ Errors</h3>"
        for e in errors[-5:]:
            html += f"<div style='color: #ff4444; font-size: 12px;'>{e}</div>"

    html += """
<hr style="border-color: #333; margin-top: 24px;">
<p style="color: #555; font-size: 11px;">
  Polly QuantBot &nbsp;·&nbsp; Research only — not financial advice &nbsp;·&nbsp;
  <a href="https://github.com/614restore/polly" style="color: #555;">github.com/614restore/polly</a>
</p>
</body></html>"""

    return subject, html


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

def send_report():
    if not GMAIL_USER or not GMAIL_PASS:
        print("GMAIL_USER or GMAIL_PASS not set in .env — printing report instead.\n")
        subject, html = build_report()
        print(f"Subject: {subject}\n")
        # Strip HTML for terminal preview
        import re
        print(re.sub(r"<[^>]+>", "", html))
        return

    subject, html = build_report()
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
        print(f"Report sent to {TO_EMAIL}")
    except Exception as e:
        print(f"Failed to send email: {e}")
        sys.exit(1)


if __name__ == "__main__":
    send_report()
