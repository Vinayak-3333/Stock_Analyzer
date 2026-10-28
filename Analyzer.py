"""
StockRadar IN — Email builder + Gmail sender
============================================
What remains of the original monolithic analyser. The analysis engine that used
to live here was superseded by the modular `core/` pipeline
(`core.pipeline.run_modular_analysis`) and removed once nothing referenced it;
see git history if you need it back.

`backend/api.py` imports exactly three things from this module —
`build_email_body`, `build_subject` and `send_gmail` — and that is the whole of
its remaining job: turn a results list into the alert email and post it through
Gmail SMTP.

Credentials come from the environment / project-root `.env`; never hardcode them.
Gmail App Password: myaccount.google.com -> Security -> App Passwords.
"""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parent / ".env"), override=False)
except Exception:
    pass

log = logging.getLogger(__name__)

GMAIL_SENDER       = os.environ.get("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")   # 16-char Gmail App Password
RECIPIENT_EMAIL     = os.environ.get("RECIPIENT_EMAIL", GMAIL_SENDER)
EMAIL_SUBJECT_PREFIX = "[StockRadar IN]"

# Only stocks scoring at least this are listed in the BUY/WATCH sections.
MIN_SCORE = 60

# Max stocks per email (ranked by score).
MAX_STOCKS_PER_EMAIL = 12

# If True, only include stocks with confirmed promoter buying.
REQUIRE_PROMOTER_BUY = False

# ─────────────────────────────────────────────
# EMAIL BUILDER
# ─────────────────────────────────────────────

def build_email_body(results, analysis_time):
    buys   = [r for r in results if r["signal"] == "BUY"   and r["score"] >= MIN_SCORE]
    sells  = [r for r in results if r["signal"] == "SELL"]
    watch  = [r for r in results if r["signal"] == "WATCH" and r["score"] >= MIN_SCORE]
    holds  = [r for r in results if r["signal"] == "HOLD"]

    if REQUIRE_PROMOTER_BUY:
        buys = [r for r in buys if r["promoter_action"] == "BUY"]

    all_featured = (buys + sells + watch)[:MAX_STOCKS_PER_EMAIL]
    
    # Sanitize NaN values in results
    for r in results:
        for key in ["high_52w", "low_52w", "pct_from_52w_high", "pct_from_52w_low"]:
            if isinstance(r.get(key), float) and (r[key] != r[key]):  # NaN check
                if key in ["high_52w", "low_52w"]:
                    r[key] = r["price"]
                else:
                    r[key] = 0

    def fmt_row(r):
        prom_txt = f"Promoter {r['promoter_action'].lower()}" if r["promoter_action"] != "NEUTRAL" else "—"

        # Indicator flags
        macd_txt = "MACD↑" if r["macd_bullish"] else "MACD↓"
        gc_txt   = "GC" if r["golden_cross"] else "DCx"
        vol_txt  = f"Vol:{r['volume_ratio']:.1f}x" if r["vol_surge"] else ""
        adx_txt  = f"ADX:{r['adx']:.0f}" if r["adx"] > 20 else ""
        roc_txt  = f"ROC5:{r['roc_5d']:+.1f}%" if abs(r["roc_5d"]) > 2 else ""

        # News sentiment icon
        ns = r.get("news_sentiment", "NEUTRAL")
        news_icon = "\U0001f4f0✅" if ns == "POSITIVE" else ("⚠️" if ns == "NEGATIVE" else "")

        # Intraday tag
        intra = r.get("intraday_change", 0)
        intra_txt = f"Today:{intra:+.1f}%" if intra != 0 else ""

        flags     = " | ".join(filter(None, [macd_txt, gc_txt, adx_txt, vol_txt, roc_txt]))
        bounds_txt = f"BB:{r['bb_pct']:.1f}" if r["bb_pct"] is not None else ""

        return (
            f"  {r['symbol']:<10} ₹{r['price']:>8.2f}  "
            f"Score:{r['score']:>5.1f}  RSI:{r['rsi']:>5.1f}  "
            f"ATR:{r['atr_pct']:>4.2f}%  {bounds_txt:<10}  "
            f"{intra_txt:<12}  {news_icon}  {prom_txt}"
        )

    lines = []
    lines.append("=" * 120)
    lines.append(f"  StockRadar IN — Real-Time Dynamic Analysis")
    lines.append(f"  {analysis_time}  |  {len(results)} stocks analyzed  |  {len(buys)} BUY | {len(sells)} SELL | {len(watch)} WATCH")
    lines.append("=" * 120)
    lines.append("")

    if buys:
        lines.append(f"🟢  BUY SIGNALS ({len(buys)} stocks)")
        lines.append("─" * 120)
        lines.append(f"  {'Symbol':<10} {'Price':>10}  {'Score':>7}  {'RSI':>7}  {'Volatility':>12}  {'Bands':>10}  Promoter")
        for r in buys[:MAX_STOCKS_PER_EMAIL]:
            lines.append(fmt_row(r))
        lines.append("")

    if sells:
        lines.append(f"🔴  SELL / EXIT SIGNALS ({len(sells)} stocks)")
        lines.append("─" * 120)
        for r in sells:
            lines.append(fmt_row(r))
        lines.append("")

    if watch:
        lines.append(f"🔵  WATCH LIST ({len(watch)} stocks)")
        lines.append("─" * 120)
        for r in watch[:5]:
            lines.append(fmt_row(r))
        lines.append("")

    lines.append("─" * 120)
    lines.append("📊 DETAILED ANALYSIS — Top Buy Signals")
    lines.append("─" * 120)
    for r in buys[:3]:
        lines.append(f"\n{r['symbol']} — ₹{r['price']:.2f} (Score: {r['score']:.1f}/100)")
        lines.append(f"  Trend: {'↑ Uptrend' if r['golden_cross'] else '↓ Downtrend'} | Strength (ADX): {r['adx']:.0f} | Momentum: {r['roc_5d']:+.1f}% (5d)")
        lines.append(f"  Technical: RSI={r['rsi']:.1f} | MACD={'Bullish ↑' if r['macd_bullish'] else 'Bearish ↓'} | Stoch={r['stoch_k']:.0f}")
        lines.append(f"  Volatility: ATR={r['atr_pct']:.2f}% | Bollinger Bands Pos: {r['bb_pct']:.1%}")
        lines.append(f"  Volume: {r['volume_ratio']:.1f}x avg | 52W: {r['pct_from_52w_low']:+.1f}% from low, {r['pct_from_52w_high']:-.1f}% from high")

        # ── Intraday momentum (live today) ──
        intra = r.get('intraday_change', 0)
        gap   = r.get('open_gap', 0)
        if intra != 0 or gap != 0:
            lines.append(f"  Intraday: {intra:+.2f}% today | Gap open: {gap:+.2f}% vs prev close")
        else:
            lines.append(f"  Intraday: Market closed / no data")

        # ── News sentiment ──
        ns_label = r.get('news_sentiment', 'NEUTRAL')
        ns_score = r.get('news_score', 0)
        ns_icon  = '✅ POSITIVE' if ns_label == 'POSITIVE' else ('❌ NEGATIVE' if ns_label == 'NEGATIVE' else '◌ NEUTRAL')
        lines.append(f"  News Sentiment: {ns_icon} (raw score: {ns_score:+.0f})")
        for headline in r.get('top_news', []):
            lines.append(f"    ‣ {headline[:110]}")

        # ── Fundamentals ──
        pe   = r.get('pe_ratio')
        rg   = r.get('revenue_growth')
        eg   = r.get('eps_growth')
        ar   = r.get('analyst_rating')
        fund_parts = []
        if pe  is not None: fund_parts.append(f"P/E={pe:.1f}")
        if rg  is not None: fund_parts.append(f"Rev Growth={rg:+.1f}%")
        if eg  is not None: fund_parts.append(f"EPS Growth={eg:+.1f}%")
        if ar  is not None:
            ar_label = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}.get(round(ar), f"{ar:.1f}")
            fund_parts.append(f"Analyst={ar_label}")
        if fund_parts:
            lines.append(f"  Fundamentals: {' | '.join(fund_parts)}")

        if r['promoter_action'] != "NEUTRAL":
            lines.append(f"  Promoter: {r['promoter_action']} — {r['promoter_detail']}")
    
    lines.append("52-WEEK RANGE CONTEXT")
    lines.append("─" * 120)
    for r in buys[:5]:
        high_52w = r["high_52w"] if r["high_52w"] > 0 else r["price"]
        low_52w = r["low_52w"] if r["low_52w"] > 0 else r["price"]
        pct_high = r["pct_from_52w_high"] if not (isinstance(r["pct_from_52w_high"], float) and r["pct_from_52w_high"] != r["pct_from_52w_high"]) else 0
        pct_low = r["pct_from_52w_low"] if not (isinstance(r["pct_from_52w_low"], float) and r["pct_from_52w_low"] != r["pct_from_52w_low"]) else 0
        
        lines.append(
            f"  {r['symbol']:<10}  Low: ₹{low_52w:>8.2f} (+{pct_low:>6.1f}%)  "
            f"High: ₹{high_52w:>8.2f}  ({pct_high:>6.1f}% below peak)"
        )
    
    lines.append("")
    lines.append("─" * 120)
    lines.append("LEGEND: Score (0-100) | RSI (oversold <30, overbought >70) | ADX (trend strength >30)")
    lines.append("ATR (volatility %) | BB Position (0=lower, 1=upper) | Vol Ratio (vs 20-day avg)")
    lines.append("⚠️  NOT SEBI investment advice. This is algorithmic analysis only. Trade at your own risk.")
    lines.append(f"Generated by StockRadar IN at {analysis_time} IST")
    lines.append("─" * 120)

    return "\n".join(lines)


def build_subject(results):
    buys  = sum(1 for r in results if r["signal"] == "BUY"  and r["score"] >= MIN_SCORE)
    sells = sum(1 for r in results if r["signal"] == "SELL")
    date_str = datetime.now().strftime("%a %d %b %Y")
    time_str = datetime.now().strftime("%H:%M")
    market_trend = "📈" if buys > sells else "📉"
    return f"{EMAIL_SUBJECT_PREFIX} {time_str} {market_trend} {buys} Buy | {sells} Sell — {date_str}"


# ─────────────────────────────────────────────
# GMAIL SENDER
# ─────────────────────────────────────────────

def send_gmail(subject, body) -> bool:
    """Sends a plain-text email via Gmail SMTP with App Password.

    Returns True only if Gmail accepted the message. Failures are logged, not
    raised, so a mail problem never fails the analysis run that triggered it.
    """
    if not GMAIL_SENDER or not GMAIL_APP_PASSWORD:
        log.warning("Email not sent — GMAIL_SENDER / GMAIL_APP_PASSWORD not set")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_SENDER, RECIPIENT_EMAIL, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        log.error("Email not sent — Gmail auth failed, check the App Password")
        return False
    except Exception as e:
        log.error("Email not sent — %s: %s", type(e).__name__, e)
        return False
    log.info("Email sent to %s", RECIPIENT_EMAIL)
    return True
