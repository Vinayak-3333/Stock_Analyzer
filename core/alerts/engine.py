"""
Real-Time Alert Engine
=======================
Generates and dispatches alerts for:
  - 52W breakout + volume surge
  - Volume spike (>5x avg in opening session)
  - FII net buying > threshold
  - News impact (FinBERT score > 0.7 on major headline)
  - OI buildup (options positioning shift)
  - Score change (HOLD → BUY promotion)

Dispatches via:
  - Email (existing Gmail SMTP)
  - Telegram Bot (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars)
  - Kafka topic: alerts.generated
  - DuckDB: alert_history table
"""

import os
import uuid
import json
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

log = logging.getLogger("stockradar.alerts")

# ── Config (from env vars or .env file) ──────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
GMAIL_SENDER     = os.getenv("GMAIL_SENDER", "vinnu.smath333@gmail.com")
GMAIL_PASSWORD   = os.getenv("GMAIL_APP_PASSWORD", "qatd oybr htuk eygb")
RECIPIENT_EMAIL  = os.getenv("RECIPIENT_EMAIL", "vinnu.smath333@gmail.com")

# Alert thresholds
BREAKOUT_WKH_PCT    = 3.0    # Within 3% of 52W high
BREAKOUT_VOL_MULT   = 2.0    # Volume > 2x avg
VOLUME_SPIKE_MULT   = 5.0    # Volume spike alert threshold
FII_ALERT_CR        = 1000   # FII net > ₹1000 Cr triggers alert
NEWS_SCORE_THRESH   = 0.65   # FinBERT score threshold for news alert


# ── Alert type definitions ─────────────────────────────────────────────────────

ALERT_TYPES = {
    "breakout":       "52W Breakout",
    "volume_spike":   "Volume Spike",
    "fii_buying":     "FII Buying",
    "news_impact":    "News Alert",
    "oi_buildup":     "OI Buildup",
    "score_change":   "Signal Upgrade",
    "stop_loss_hit":  "Stop Loss Hit",
    "risk_flag":      "Risk Warning",
}


# ── Alert generation ──────────────────────────────────────────────────────────

def check_breakout(symbol: str, quote: dict, avg_volume: float) -> Optional[dict]:
    """Detect 52W breakout with volume confirmation."""
    near_wkh  = quote.get("nearWKH") or quote.get("pct_from_52w_high", 100)
    volume    = quote.get("totalTradedVolume") or quote.get("live_volume", 0)
    if near_wkh is None or volume is None:
        return None
    if float(near_wkh) < BREAKOUT_WKH_PCT and avg_volume > 0 and volume > avg_volume * BREAKOUT_VOL_MULT:
        return _make_alert(
            symbol=symbol,
            alert_type="breakout",
            message=(
                f"{symbol}: Near 52W high ({near_wkh:.1f}% away) with "
                f"volume surge {volume / avg_volume:.1f}x avg — BREAKOUT SIGNAL"
            ),
        )
    return None


def check_volume_spike(symbol: str, quote: dict, avg_volume: float) -> Optional[dict]:
    """Detect unusually large volume spike."""
    volume = quote.get("totalTradedVolume") or quote.get("live_volume", 0)
    if avg_volume > 0 and volume > avg_volume * VOLUME_SPIKE_MULT:
        return _make_alert(
            symbol=symbol,
            alert_type="volume_spike",
            message=(
                f"{symbol}: Volume SPIKE {volume / avg_volume:.1f}x average — "
                f"unusual activity detected"
            ),
        )
    return None


def check_fii_alert(fii_data: dict) -> Optional[dict]:
    """Alert on large FII net buying or selling."""
    fii_net = fii_data.get("fii_net") or 0
    if fii_net > FII_ALERT_CR:
        return _make_alert(
            symbol="MARKET",
            alert_type="fii_buying",
            message=f"FII NET BUYING: ₹{fii_net:,.0f} Cr today — strong institutional inflow",
        )
    elif fii_net < -FII_ALERT_CR:
        return _make_alert(
            symbol="MARKET",
            alert_type="fii_buying",
            message=f"FII NET SELLING: ₹{abs(fii_net):,.0f} Cr today — institutional outflow",
        )
    return None


def check_news_impact(symbol: str, articles: list[dict]) -> Optional[dict]:
    """Alert when a major news event has strong sentiment."""
    for art in articles:
        score = art.get("finbert_score") or art.get("raw_sentiment") or 0
        event = art.get("event_type")
        if abs(score) >= NEWS_SCORE_THRESH:
            direction = "POSITIVE" if score > 0 else "NEGATIVE"
            return _make_alert(
                symbol=symbol,
                alert_type="news_impact",
                message=(
                    f"{symbol}: {direction} news impact (score={score:.2f}) | "
                    f"Event: {event or 'General'} | "
                    f"{art.get('headline', '')[:100]}"
                ),
            )
    return None


def check_score_upgrade(symbol: str, old_signal: str, new_signal: str, score: float) -> Optional[dict]:
    """Alert when a stock's signal improves."""
    upgrade_map = {("HOLD", "WATCH"), ("HOLD", "BUY"), ("WATCH", "BUY"), ("SELL", "WATCH"), ("SELL", "BUY")}
    if (old_signal, new_signal) in upgrade_map:
        return _make_alert(
            symbol=symbol,
            alert_type="score_change",
            message=f"{symbol}: Signal UPGRADED {old_signal} -> {new_signal} (score={score:.0f})",
        )
    return None


def _make_alert(symbol: str, alert_type: str, message: str) -> dict:
    return {
        "id":           str(uuid.uuid4()),
        "symbol":       symbol,
        "alert_type":   alert_type,
        "message":      message,
        "triggered_at": datetime.now().isoformat(),
        "sent_email":   False,
        "sent_telegram": False,
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

def dispatch_alert(alert: dict, send_email: bool = True, send_telegram: bool = True):
    """Send alert via all configured channels and save to lake."""
    log.info("ALERT [%s] %s: %s", alert["alert_type"], alert["symbol"], alert["message"])

    if send_telegram and TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        alert["sent_telegram"] = _send_telegram(alert["message"])

    if send_email:
        alert["sent_email"] = _send_email_alert(alert)

    # Publish to Kafka (optional)
    _publish_to_kafka(alert)

    # Save to lake
    _save_alert_to_lake(alert)


def dispatch_alerts_batch(alerts: list[dict], **kwargs):
    """Dispatch multiple alerts."""
    for alert in alerts:
        try:
            dispatch_alert(alert, **kwargs)
        except Exception as e:
            log.error("Alert dispatch failed: %s", e)


def _send_telegram(message: str) -> bool:
    """Send Telegram message via bot."""
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text":    f"StockRadar IN\n{message}",
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(url, data=payload,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as e:
        log.debug("Telegram send failed: %s", e)
        return False


def _send_email_alert(alert: dict) -> bool:
    """Send single alert as email."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[StockRadar Alert] {ALERT_TYPES.get(alert['alert_type'], alert['alert_type'])}: {alert['symbol']}"
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = RECIPIENT_EMAIL
        body = f"""
        <html><body style="font-family:Arial;background:#0d1117;color:#e6edf3;padding:20px">
        <h2 style="color:#58a6ff">StockRadar IN — Alert</h2>
        <p style="font-size:16px;background:#161b22;padding:15px;border-radius:8px">
            {alert['message']}
        </p>
        <p style="color:#8b949e;font-size:12px">
            Type: {alert['alert_type']} | Time: {alert['triggered_at']}
        </p>
        </body></html>
        """
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_SENDER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_SENDER, RECIPIENT_EMAIL, msg.as_string())
        return True
    except Exception as e:
        log.debug("Email alert failed: %s", e)
        return False


def _publish_to_kafka(alert: dict):
    try:
        from kafka import KafkaProducer
        p = KafkaProducer(
            bootstrap_servers="localhost:9092",
            value_serializer=lambda v: json.dumps(v, default=str).encode(),
        )
        p.send("alerts.generated", alert)
        p.flush()
    except Exception:
        pass   # Kafka optional


def _save_alert_to_lake(alert: dict):
    from core.lake.manager import get_lake
    try:
        conn = get_lake()
        conn.execute("""
            INSERT OR IGNORE INTO alert_history
                (id, symbol, alert_type, message, triggered_at, sent_email, sent_telegram)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            alert["id"], alert["symbol"], alert["alert_type"],
            alert["message"], alert["triggered_at"],
            alert.get("sent_email", False), alert.get("sent_telegram", False),
        ])
        conn.commit()
    except Exception as e:
        log.debug("Alert lake save failed: %s", e)


def get_recent_alerts(hours: int = 24, limit: int = 50) -> list[dict]:
    """Retrieve recent alerts for dashboard display."""
    from core.lake.manager import get_lake
    try:
        conn = get_lake()
        rows = conn.execute("""
            SELECT id, symbol, alert_type, message, triggered_at, sent_email, sent_telegram
            FROM alert_history
            WHERE triggered_at >= NOW() - INTERVAL (?) HOUR
            ORDER BY triggered_at DESC
            LIMIT ?
        """, [hours, limit]).fetchall()
        cols = ["id", "symbol", "alert_type", "message", "triggered_at", "sent_email", "sent_telegram"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        log.debug("Alert history query failed: %s", e)
        return []
