"""
Multi-Factor Scoring Engine
============================
Implements the weighted scoring model:

  Fundamentals       30%
  Technical Momentum 25%
  Institutional      15%
  Sentiment          10%
  Sector Strength    10%
  Risk Metrics       10%

Each factor returns a normalised score 0–100.
Final score = weighted sum * regime multiplier.
"""

from __future__ import annotations
import math
import logging
from typing import Optional

log = logging.getLogger("stockradar.scoring")

# ── Factor Weights ─────────────────────────────────────────────────────────────
WEIGHTS = {
    "fundamental":   0.30,
    "technical":     0.25,
    "institutional": 0.15,
    "sentiment":     0.10,
    "sector":        0.10,
    "risk":          0.10,
}

# Regime multipliers applied to final score
REGIME_MULTIPLIER = {0: 0.85, 1: 1.00, 2: 1.10}   # Bear / Neutral / Bull


# ── Helper ─────────────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0, hi: float = 100) -> float:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 50.0
    return max(lo, min(hi, v))


def _safe(v, default=0):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return default
    return v


# ── 1. Fundamental Score (0–100) ───────────────────────────────────────────────

def fundamental_score(fund: dict) -> tuple[float, list[str]]:
    """
    Score based on: ROE, ROCE, EPS growth, Revenue growth,
    Debt/Equity, FCF yield, P/E vs sector, promoter holding.
    Returns (score 0-100, list of reason strings).
    """
    score = 50.0
    reasons = []

    roe           = _safe(fund.get("roe"))
    roce          = _safe(fund.get("roce"))
    eps_growth    = _safe(fund.get("eps_growth_1y"))
    rev_growth    = _safe(fund.get("revenue_growth_1y"))
    de_ratio      = _safe(fund.get("debt_to_equity"), 100)
    fcf_yield     = _safe(fund.get("fcf_yield"))
    pe_ratio      = _safe(fund.get("pe_ratio"), 30)
    promoter_hold = _safe(fund.get("promoter_holding"), 50)

    # ROE
    if roe > 25:     score += 15; reasons.append(f"+15: ROE={roe:.1f}% (excellent)")
    elif roe > 18:   score += 8;  reasons.append(f"+8: ROE={roe:.1f}% (good)")
    elif roe > 12:   score += 3
    elif roe < 8:    score -= 8;  reasons.append(f"-8: ROE={roe:.1f}% (weak)")

    # EPS growth
    if eps_growth > 25:   score += 12; reasons.append(f"+12: EPS growth={eps_growth:.1f}%")
    elif eps_growth > 15: score += 7
    elif eps_growth > 5:  score += 3
    elif eps_growth < -10: score -= 10; reasons.append(f"-10: EPS decline={eps_growth:.1f}%")
    elif eps_growth < 0:  score -= 5

    # Revenue growth
    if rev_growth > 20:   score += 8; reasons.append(f"+8: Revenue growth={rev_growth:.1f}%")
    elif rev_growth > 10: score += 4
    elif rev_growth < -5: score -= 6
    elif rev_growth < 0:  score -= 3

    # Debt/Equity (lower is better)
    if de_ratio < 20:     score += 8; reasons.append(f"+8: Low D/E={de_ratio:.1f}% (clean balance sheet)")
    elif de_ratio < 50:   score += 3
    elif de_ratio > 150:  score -= 10; reasons.append(f"-10: High D/E={de_ratio:.1f}% (debt risk)")
    elif de_ratio > 100:  score -= 5

    # FCF yield
    if fcf_yield and fcf_yield > 5:   score += 5; reasons.append(f"+5: FCF yield={fcf_yield:.1f}%")
    elif fcf_yield and fcf_yield > 2: score += 2
    elif fcf_yield and fcf_yield < 0: score -= 5

    # P/E (relative scoring — high P/E reduces score unless growth justifies it)
    if pe_ratio > 0:
        if pe_ratio < 15:    score += 5
        elif pe_ratio < 25:  score += 2
        elif pe_ratio > 60:  score -= 8; reasons.append(f"-8: P/E={pe_ratio:.0f} (expensive)")
        elif pe_ratio > 40:  score -= 4

    # Promoter holding
    if promoter_hold > 60:    score += 5; reasons.append(f"+5: Promoter holding={promoter_hold:.1f}%")
    elif promoter_hold > 50:  score += 2
    elif promoter_hold < 25:  score -= 5

    return _clamp(score), reasons


# ── 2. Technical Score (0–100) ─────────────────────────────────────────────────

def technical_score(tech: dict) -> tuple[float, list[str]]:
    """
    Score based on: RSI, MACD, ADX, BB%, Stoch, ROC, SMA ratio,
    volume ratio, RS vs NIFTY, 52W breakout, HH/HL count.
    """
    score = 50.0
    reasons = []

    rsi           = _safe(tech.get("rsi"), 50)
    macd_hist     = _safe(tech.get("macd_hist"))
    adx           = _safe(tech.get("adx"), 20)
    bb_pct        = _safe(tech.get("bb_pct"), 0.5)
    stoch_k       = _safe(tech.get("stoch_k"), 50)
    roc_5d        = _safe(tech.get("roc_5d"))
    sma_ratio     = _safe(tech.get("sma_ratio_50_200"), 1.0)
    vol_ratio     = _safe(tech.get("volume_ratio"), 1.0)
    rs_nifty      = _safe(tech.get("rs_vs_nifty"), 100)
    is_breakout   = tech.get("is_52w_breakout", False)
    hh_hl         = _safe(tech.get("hh_hl_count"), 0)
    atr_pct       = _safe(tech.get("atr_pct"), 2)

    # RSI
    if rsi < 30:     score += 12; reasons.append(f"+12: RSI={rsi:.0f} (oversold)")
    elif rsi < 45:   score += 6
    elif rsi > 75:   score -= 12; reasons.append(f"-12: RSI={rsi:.0f} (overbought)")
    elif rsi > 60:   score -= 5

    # MACD histogram
    if macd_hist > 0.5:    score += 8; reasons.append(f"+8: MACD bullish crossover")
    elif macd_hist > 0:    score += 4
    elif macd_hist < -0.5: score -= 8; reasons.append(f"-8: MACD bearish")
    elif macd_hist < 0:    score -= 4

    # ADX (trend strength)
    if adx > 30 and sma_ratio > 1:   score += 10; reasons.append(f"+10: Strong uptrend (ADX={adx:.0f})")
    elif adx > 30 and sma_ratio < 1: score -= 10; reasons.append(f"-10: Strong downtrend (ADX={adx:.0f})")
    elif adx > 20 and sma_ratio > 1: score += 5
    elif adx > 20 and sma_ratio < 1: score -= 5

    # Bollinger Bands
    if bb_pct < 0.15:    score += 12; reasons.append(f"+12: Near lower BB (oversold squeeze)")
    elif bb_pct < 0.35:  score += 6
    elif bb_pct > 0.85:  score -= 12; reasons.append(f"-12: Near upper BB (overbought)")
    elif bb_pct > 0.65:  score -= 6

    # Stochastic
    if stoch_k < 20:    score += 8
    elif stoch_k > 80:  score -= 8

    # Rate of Change (momentum)
    if roc_5d > 5:      score += 8; reasons.append(f"+8: 5d momentum={roc_5d:.1f}%")
    elif roc_5d > 2:    score += 4
    elif roc_5d < -5:   score -= 8
    elif roc_5d < -2:   score -= 4

    # Golden/Death cross (SMA 50 vs 200)
    if sma_ratio > 1.02:   score += 8; reasons.append(f"+8: Golden cross (50>200 SMA)")
    elif sma_ratio > 1.0:  score += 3
    elif sma_ratio < 0.98: score -= 8
    elif sma_ratio < 1.0:  score -= 3

    # Volume
    if vol_ratio > 3:     score += 10; reasons.append(f"+10: Volume surge {vol_ratio:.1f}x avg")
    elif vol_ratio > 2:   score += 6
    elif vol_ratio > 1.5: score += 3
    elif vol_ratio < 0.5: score -= 5

    # Relative strength vs NIFTY
    if rs_nifty > 130:    score += 10; reasons.append(f"+10: RS={rs_nifty:.0f} (outperforming NIFTY 30%+)")
    elif rs_nifty > 110:  score += 5
    elif rs_nifty < 80:   score -= 8
    elif rs_nifty < 90:   score -= 4

    # 52-week breakout
    if is_breakout:
        score += 15; reasons.append("+15: Near 52W high + volume surge (breakout signal)")

    # Higher Highs / Higher Lows (weekly)
    if hh_hl >= 3:    score += 6; reasons.append(f"+6: {hh_hl} consecutive HH-HL (strong uptrend)")
    elif hh_hl >= 1:  score += 3

    # High volatility penalty
    if atr_pct > 5:   score -= 5
    elif atr_pct > 3: score -= 2

    return _clamp(score), reasons


# ── 3. Institutional Score (0–100) ─────────────────────────────────────────────

def institutional_score(inst: dict) -> tuple[float, list[str]]:
    """
    Score based on: FII 3-day flow, DII flow, delivery %, OI buildup, PCR.
    """
    score = 50.0
    reasons = []

    fii_3d_net    = _safe(inst.get("fii_3d_net"))      # crores
    dii_3d_net    = _safe(inst.get("dii_3d_net"))
    fii_trend     = _safe(inst.get("fii_trend"))        # +1 / 0 / -1
    delivery_pct  = _safe(inst.get("delivery_pct_5d"), 40)
    delivery_spike= inst.get("delivery_spike", False)
    pcr           = _safe(inst.get("pcr"), 1.0)
    oi_buildup    = inst.get("oi_buildup", False)
    max_pain_dist = _safe(inst.get("max_pain_dist"))   # % distance from max pain

    # FII flow (market-level, shared across all stocks)
    if fii_3d_net > 3000:    score += 15; reasons.append(f"+15: FII net buying ₹{fii_3d_net:,.0f} Cr (3d)")
    elif fii_3d_net > 1000:  score += 8
    elif fii_3d_net > 0:     score += 3
    elif fii_3d_net < -3000: score -= 15; reasons.append(f"-15: FII net selling ₹{abs(fii_3d_net):,.0f} Cr")
    elif fii_3d_net < -1000: score -= 8
    elif fii_3d_net < 0:     score -= 3

    # DII often acts as counterbalance to FII — DII buying during FII selloff = support
    if dii_3d_net > 2000 and fii_3d_net < 0:
        score += 8; reasons.append(f"+8: DII absorbing FII selling (₹{dii_3d_net:,.0f} Cr)")
    elif dii_3d_net > 1000: score += 4
    elif dii_3d_net < -1000: score -= 5

    # Stock-level delivery % (quality of price move)
    if delivery_pct > 70:    score += 12; reasons.append(f"+12: Delivery %={delivery_pct:.0f}% (institutional accumulation)")
    elif delivery_pct > 55:  score += 6
    elif delivery_pct > 40:  score += 2
    elif delivery_pct < 25:  score -= 8; reasons.append(f"-8: Low delivery {delivery_pct:.0f}% (speculative move)")
    elif delivery_pct < 35:  score -= 4

    # Delivery spike (unusually high today vs avg)
    if delivery_spike:
        score += 8; reasons.append("+8: Delivery spike today (unusual institutional interest)")

    # PCR (Put/Call Ratio) — >1.2 bullish, <0.7 bearish
    if pcr > 1.4:    score += 10; reasons.append(f"+10: PCR={pcr:.2f} (very bullish options positioning)")
    elif pcr > 1.2:  score += 5
    elif pcr < 0.6:  score -= 10; reasons.append(f"-10: PCR={pcr:.2f} (bearish options positioning)")
    elif pcr < 0.8:  score -= 5

    # OI buildup (rising OI + rising price = bullish new longs)
    if oi_buildup:
        score += 8; reasons.append("+8: OI buildup with price rise (new long positions)")

    # Max pain proximity (price tends to gravitate toward max pain near expiry)
    if max_pain_dist and abs(max_pain_dist) < 3:
        score += 3   # near max pain — likely to stay in range

    return _clamp(score), reasons


# ── 4. Sentiment Score (0–100) ─────────────────────────────────────────────────

def sentiment_score(sent: dict) -> tuple[float, list[str]]:
    """Score based on FinBERT/VADER news score, event type, GDELT tone."""
    score = 50.0
    reasons = []

    news_avg    = _safe(sent.get("avg_score"))         # -1 to +1
    event_type  = sent.get("event_type")
    article_cnt = _safe(sent.get("article_count"))
    gdelt_tone  = _safe(sent.get("gdelt_tone"))

    # News sentiment
    if news_avg > 0.5:    score += 20; reasons.append(f"+20: Very positive news (score={news_avg:.2f})")
    elif news_avg > 0.25: score += 12; reasons.append(f"+12: Positive news coverage")
    elif news_avg > 0.1:  score += 6
    elif news_avg < -0.5: score -= 20; reasons.append(f"-20: Very negative news (score={news_avg:.2f})")
    elif news_avg < -0.25:score -= 12; reasons.append(f"-12: Negative news coverage")
    elif news_avg < -0.1: score -= 6

    # Event type boosts / penalties
    if event_type == "earnings":
        if news_avg > 0.2: score += 8; reasons.append("+8: Positive earnings event")
        elif news_avg < -0.2: score -= 10; reasons.append("-10: Negative earnings event")
    elif event_type == "ma":
        score += 8; reasons.append("+8: M&A activity detected")
    elif event_type == "regulatory":
        score -= 12; reasons.append("-12: Regulatory/legal risk detected")
    elif event_type == "fraud":
        score -= 20; reasons.append("-20: FRAUD / GOVERNANCE RISK detected")
    elif event_type == "policy":
        if news_avg > 0: score += 5
        else:            score -= 5

    # Coverage buzz (many articles = high attention)
    if article_cnt > 20:    score += 5
    elif article_cnt > 10:  score += 2
    elif article_cnt == 0:  score -= 3   # no news = uncertain

    # GDELT global tone
    if gdelt_tone:
        if gdelt_tone > 5:   score += 5
        elif gdelt_tone < -5: score -= 5

    return _clamp(score), reasons


# ── 5. Sector Score (0–100) ────────────────────────────────────────────────────

def sector_score(market: dict, symbol_sector: str = "") -> tuple[float, list[str]]:
    """
    Score based on: sector momentum, market breadth, crude/USD impact.
    """
    score = 50.0
    reasons = []

    sector_momentum = _safe(market.get("sector_momentum"))
    breadth_pct     = _safe(market.get("breadth_pct"), 50)   # % NIFTY stocks above 200 SMA
    crude_regime    = _safe(market.get("crude_regime"))
    usdinr_regime   = _safe(market.get("usdinr_regime"))
    sector_return   = _safe(market.get("sector_1m_return"))   # sector vs NIFTY

    # Sector momentum (how sector is doing vs market)
    if sector_momentum > 3:    score += 15; reasons.append(f"+15: Sector momentum +{sector_momentum:.1f}%")
    elif sector_momentum > 1:  score += 7
    elif sector_momentum > 0:  score += 3
    elif sector_momentum < -3: score -= 15; reasons.append(f"-15: Sector weakening {sector_momentum:.1f}%")
    elif sector_momentum < -1: score -= 7
    elif sector_momentum < 0:  score -= 3

    # Market breadth
    if breadth_pct > 70:    score += 8; reasons.append(f"+8: Strong breadth — {breadth_pct:.0f}% above 200SMA")
    elif breadth_pct > 55:  score += 3
    elif breadth_pct < 30:  score -= 10; reasons.append(f"-10: Weak breadth — only {breadth_pct:.0f}% above 200SMA")
    elif breadth_pct < 45:  score -= 5

    # Crude oil impact (IT/Pharma benefit from low crude; Auto/FMCG hurt by high crude)
    sector_upper = symbol_sector.upper()
    if crude_regime == 1:   # high crude (>90)
        if any(s in sector_upper for s in ["IT", "PHARMA", "SOFTWARE", "CONSULT"]):
            score += 5; reasons.append("+5: IT/Pharma less affected by crude")
        elif any(s in sector_upper for s in ["AUTO", "FMCG", "AVIATION", "LOGISTICS"]):
            score -= 8; reasons.append("-8: Sector hurt by high crude oil")
    elif crude_regime == -1:  # low crude (<70)
        if any(s in sector_upper for s in ["AUTO", "FMCG", "LOGISTICS"]):
            score += 5

    # USD/INR impact — strong rupee helps importers
    if usdinr_regime == 1:   # weak rupee (>85)
        if any(s in sector_upper for s in ["IT", "SOFTWARE", "PHARMA", "EXPORT"]):
            score += 5; reasons.append("+5: Weak rupee boosts IT/Pharma exports")
        elif any(s in sector_upper for s in ["OIL", "IMPORT", "REFIN"]):
            score -= 5

    return _clamp(score), reasons


# ── 6. Risk Score (0–100, higher = less risk) ──────────────────────────────────

def risk_score(stock: dict, fund: dict) -> tuple[float, list[str]]:
    """
    Score based on: liquidity, pledged shares, market cap, governance flags.
    High risk score = safer stock (counter-intuitive naming fixed below).
    """
    score = 70.0   # Start generous — risk penalty-only
    reasons = []
    disqualified = False

    avg_volume    = _safe(stock.get("avg_volume"), 100000)
    pledged_pct   = _safe(fund.get("pledged_pct"), 0)
    market_cap_cr = _safe(fund.get("market_cap_cr"), 1000)
    atr_pct       = _safe(stock.get("atr_pct"), 2)
    event_type    = stock.get("event_type")

    # Liquidity filter
    if avg_volume < 50000:
        score -= 30; disqualified = True
        reasons.append(f"-30: ILLIQUID (avg volume={avg_volume:,.0f} — operator risk)")
    elif avg_volume < 200000:
        score -= 15; reasons.append(f"-15: Low liquidity (avg volume={avg_volume:,.0f})")
    elif avg_volume > 1000000:
        score += 10; reasons.append(f"+10: High liquidity")

    # Pledged shares (promoter pledging = hidden risk)
    if pledged_pct > 50:
        score -= 25; disqualified = True
        reasons.append(f"-25: HIGH PLEDGING {pledged_pct:.0f}% (governance red flag)")
    elif pledged_pct > 30:
        score -= 15; reasons.append(f"-15: Significant pledging {pledged_pct:.0f}%")
    elif pledged_pct > 15:
        score -= 5
    elif pledged_pct == 0:
        score += 5; reasons.append("+5: No pledged shares")

    # Market cap (small caps = operator risk)
    if market_cap_cr < 500:
        score -= 20; disqualified = True
        reasons.append(f"-20: Micro/small cap ₹{market_cap_cr:.0f} Cr (operator risk)")
    elif market_cap_cr < 2000:
        score -= 8
    elif market_cap_cr > 20000:
        score += 8; reasons.append(f"+8: Large cap ₹{market_cap_cr:,.0f} Cr")
    elif market_cap_cr > 5000:
        score += 4

    # Fraud / regulatory event
    if event_type in ("fraud", "regulatory"):
        score -= 20; disqualified = True
        reasons.append(f"-20: Governance risk: {event_type}")

    # Volatility
    if atr_pct > 6:
        score -= 10; reasons.append(f"-10: Very high volatility ATR={atr_pct:.1f}%")
    elif atr_pct > 4:
        score -= 5
    elif atr_pct < 1.5:
        score += 5; reasons.append(f"+5: Low volatility")

    result_score = _clamp(score)
    return result_score, reasons, disqualified


# ── Final Blended Score ────────────────────────────────────────────────────────

def calculate_final_score(
    fundamental: dict,
    technical:   dict,
    institutional: dict,
    sentiment:   dict,
    market:      dict,
    stock_meta:  dict,
    regime:      int = 1,         # 0=Bear, 1=Neutral, 2=Bull
    ml_prob:     Optional[float] = None,  # XGBoost probability (if available)
) -> dict:
    """
    Calculate the full blended multi-factor score.
    Returns dict with: final_score, signal, factor_scores, all_reasons, is_disqualified.
    """
    fund_data = {**fundamental, **stock_meta}

    f_score, f_reasons = fundamental_score(fundamental)
    t_score, t_reasons = technical_score(technical)
    i_score, i_reasons = institutional_score(institutional)
    s_score, s_reasons = sentiment_score(sentiment)
    sec_score, sec_reasons = sector_score(market, stock_meta.get("industry", ""))
    r_score, r_reasons, disqualified = risk_score(stock_meta, fundamental)

    # Weighted sum
    weighted = (
        f_score   * WEIGHTS["fundamental"]   +
        t_score   * WEIGHTS["technical"]     +
        i_score   * WEIGHTS["institutional"] +
        s_score   * WEIGHTS["sentiment"]     +
        sec_score * WEIGHTS["sector"]        +
        r_score   * WEIGHTS["risk"]
    )

    # Regime multiplier
    multiplier = REGIME_MULTIPLIER.get(regime, 1.0)
    weighted *= multiplier

    # Blend with ML model probability (if available)
    if ml_prob is not None:
        ml_score = ml_prob * 100
        final = weighted * 0.65 + ml_score * 0.35
    else:
        final = weighted

    # Disqualified stocks cap at SELL
    if disqualified:
        final = min(final, 30.0)

    final = round(_clamp(final), 1)

    # Signal
    if final >= 75:    signal = "BUY"
    elif final >= 60:  signal = "WATCH"
    elif final >= 40:  signal = "HOLD"
    else:              signal = "SELL"

    all_reasons = f_reasons + t_reasons + i_reasons + s_reasons + sec_reasons + r_reasons
    # Keep top 5 reasons by absolute impact
    all_reasons = sorted(all_reasons,
                         key=lambda x: abs(float(re.search(r"[+-]?\d+", x).group())),
                         reverse=True)[:5] if all_reasons else []

    return {
        "final_score":  final,
        "signal":       signal,
        "is_disqualified": disqualified,
        "factor_scores": {
            "fundamental":   round(f_score, 1),
            "technical":     round(t_score, 1),
            "institutional": round(i_score, 1),
            "sentiment":     round(s_score, 1),
            "sector":        round(sec_score, 1),
            "risk":          round(r_score, 1),
        },
        "top_reasons": all_reasons,
        "regime":       regime,
        "regime_multiplier": multiplier,
        "ml_probability": ml_prob,
    }


import re  # needed for reason sorting — import here to avoid top-level circular issues
