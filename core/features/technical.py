"""
core.features.technical — Technical Analysis Feature Engineering
================================================================
Computes all technical analysis features for a single stock from
its 1-year daily OHLCV DataFrame (yfinance format).

Every function is callable standalone for a single symbol and will
never raise — errors return sensible defaults.

Dependencies:
    pip install pandas ta

Usage:
    >>> import yfinance as yf
    >>> df = yf.download("RELIANCE.NS", period="1y", progress=False)
    >>> features = compute_technical_features(df)
    >>> score    = compute_technical_score(features)
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Optional

import pandas as pd

# ── ta library indicators ────────────────────────────────────────────────────
import ta.momentum
import ta.trend
import ta.volatility

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value, default: float = 0.0) -> float:
    """Extract a scalar float from a pandas scalar / Series tail, handling NaN."""
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError, IndexError):
        return default


def _last(series: pd.Series, default: float = 0.0) -> float:
    """Return the last non-NaN value of a Series as a float."""
    if series is None or series.empty:
        return default
    return _safe_float(series.iloc[-1], default)


# ---------------------------------------------------------------------------
# Public API — Feature Computation
# ---------------------------------------------------------------------------

def compute_technical_features(
    df: pd.DataFrame,
    nifty_df: pd.DataFrame = None,
) -> Dict[str, object]:
    """Compute all technical features from a 1-year daily OHLCV DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
        Daily OHLCV data (columns: Open, High, Low, Close, Volume).
        Typically 1-year of data fetched via ``yfinance``.
    nifty_df : pd.DataFrame, optional
        NIFTY 50 daily DataFrame (same schema) for relative-strength
        calculation.  If *None*, ``rs_vs_nifty`` defaults to 1.0.

    Returns
    -------
    dict
        Feature name → value mapping.  All values are Python scalars
        (``float``, ``bool``, ``int``).  On catastrophic failure the
        dict is still returned with safe defaults.
    """

    # ── Defaults (returned on any unrecoverable error) ────────────────────
    defaults: Dict[str, object] = {
        "rsi_14": 50.0,
        "macd_histogram": 0.0,
        "macd_bullish": False,
        "adx": 20.0,
        "di_plus": 20.0,
        "di_minus": 20.0,
        "bb_pct": 0.5,
        "stoch_k": 50.0,
        "stoch_d": 50.0,
        "roc_5d": 0.0,
        "roc_10d": 0.0,
        "roc_20d": 0.0,
        "sma_50": 0.0,
        "sma_200": 0.0,
        "sma_ratio": 1.0,
        "golden_cross": False,
        "price_above_200sma": False,
        "atr_pct": 0.0,
        "volume_ratio": 1.0,
        "volume_surge": False,
        "rs_vs_nifty": 1.0,
        "near_52w_high": False,
        "breakout_52w": False,
        "hh_hl_count": 0,
        "price": 0.0,
        "high_52w": 0.0,
        "low_52w": 0.0,
        "pct_from_52w_high": 0.0,
        "pct_from_52w_low": 0.0,
    }

    # ── Validate input ────────────────────────────────────────────────────
    if df is None or df.empty or len(df) < 20:
        logger.warning("DataFrame is None/empty or has < 20 rows; returning defaults.")
        return defaults

    try:
        # Flatten MultiIndex columns (yfinance sometimes returns these)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]

        close: pd.Series = df["Close"].squeeze()
        high: pd.Series = df["High"].squeeze()
        low: pd.Series = df["Low"].squeeze()
        volume: pd.Series = df["Volume"].squeeze()
    except KeyError as exc:
        logger.error("Missing expected OHLCV column: %s", exc)
        return defaults

    features: Dict[str, object] = {}

    # ======================================================================
    # 1.  RSI (14-period)
    # ======================================================================
    try:
        rsi_ind = ta.momentum.RSIIndicator(close, window=14)
        features["rsi_14"] = round(_last(rsi_ind.rsi(), 50.0), 2)
    except Exception:
        logger.debug("RSI computation failed; using default.", exc_info=True)
        features["rsi_14"] = 50.0

    # ======================================================================
    # 2-3.  MACD histogram & bullish flag
    # ======================================================================
    try:
        macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = _last(macd_obj.macd(), 0.0)
        macd_signal = _last(macd_obj.macd_signal(), 0.0)
        features["macd_histogram"] = round(_last(macd_obj.macd_diff(), 0.0), 4)
        features["macd_bullish"] = bool(macd_line > macd_signal)
    except Exception:
        logger.debug("MACD computation failed; using defaults.", exc_info=True)
        features["macd_histogram"] = 0.0
        features["macd_bullish"] = False

    # ======================================================================
    # 4-6.  ADX, DI+, DI-
    # ======================================================================
    try:
        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        features["adx"] = round(_last(adx_ind.adx(), 20.0), 2)
        features["di_plus"] = round(_last(adx_ind.adx_pos(), 20.0), 2)
        features["di_minus"] = round(_last(adx_ind.adx_neg(), 20.0), 2)
    except Exception:
        logger.debug("ADX computation failed; using defaults.", exc_info=True)
        features["adx"] = 20.0
        features["di_plus"] = 20.0
        features["di_minus"] = 20.0

    # ======================================================================
    # 7.  Bollinger Band %B
    # ======================================================================
    try:
        bb_ind = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        features["bb_pct"] = round(_last(bb_ind.bollinger_pband(), 0.5), 4)
    except Exception:
        logger.debug("Bollinger Bands failed; using default.", exc_info=True)
        features["bb_pct"] = 0.5

    # ======================================================================
    # 8-9.  Stochastic Oscillator (%K, %D)
    # ======================================================================
    try:
        stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_d=3)
        features["stoch_k"] = round(_last(stoch.stoch(), 50.0), 2)
        features["stoch_d"] = round(_last(stoch.stoch_signal(), 50.0), 2)
    except Exception:
        logger.debug("Stochastic failed; using defaults.", exc_info=True)
        features["stoch_k"] = 50.0
        features["stoch_d"] = 50.0

    # ======================================================================
    # 10-12.  Rate of Change (5d, 10d, 20d)
    # ======================================================================
    try:
        cur_price = _last(close)
        if len(close) >= 5 and cur_price > 0:
            features["roc_5d"] = round(
                ((cur_price - _safe_float(close.iloc[-5])) / _safe_float(close.iloc[-5], 1)) * 100, 2
            )
        else:
            features["roc_5d"] = 0.0

        if len(close) >= 10 and cur_price > 0:
            features["roc_10d"] = round(
                ((cur_price - _safe_float(close.iloc[-10])) / _safe_float(close.iloc[-10], 1)) * 100, 2
            )
        else:
            features["roc_10d"] = 0.0

        if len(close) >= 20 and cur_price > 0:
            features["roc_20d"] = round(
                ((cur_price - _safe_float(close.iloc[-20])) / _safe_float(close.iloc[-20], 1)) * 100, 2
            )
        else:
            features["roc_20d"] = 0.0
    except Exception:
        logger.debug("ROC computation failed; using defaults.", exc_info=True)
        features.setdefault("roc_5d", 0.0)
        features.setdefault("roc_10d", 0.0)
        features.setdefault("roc_20d", 0.0)

    # ======================================================================
    # 13-16.  Moving Averages & Golden Cross
    # ======================================================================
    try:
        sma_50_val = _last(close.rolling(50).mean(), 0.0)
        sma_200_val = _last(close.rolling(200).mean(), 0.0)
        features["sma_50"] = round(sma_50_val, 2)
        features["sma_200"] = round(sma_200_val, 2)

        if sma_200_val > 0:
            features["sma_ratio"] = round(sma_50_val / sma_200_val, 4)
        else:
            features["sma_ratio"] = 1.0

        features["golden_cross"] = bool(sma_50_val > sma_200_val) and sma_200_val > 0
        features["price_above_200sma"] = bool(_last(close) > sma_200_val) and sma_200_val > 0
    except Exception:
        logger.debug("SMA computation failed; using defaults.", exc_info=True)
        features["sma_50"] = 0.0
        features["sma_200"] = 0.0
        features["sma_ratio"] = 1.0
        features["golden_cross"] = False
        features["price_above_200sma"] = False

    # ======================================================================
    # 17.  ATR as % of price (volatility)
    # ======================================================================
    try:
        atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=14)
        atr_val = _last(atr_ind.average_true_range(), 0.0)
        cur = _last(close)
        features["atr_pct"] = round((atr_val / cur) * 100, 2) if cur > 0 else 0.0
    except Exception:
        logger.debug("ATR computation failed; using default.", exc_info=True)
        features["atr_pct"] = 0.0

    # ======================================================================
    # 18-19.  Volume ratio & surge
    # ======================================================================
    try:
        cur_vol = _last(volume, 0.0)
        vol_avg_20 = _last(volume.rolling(20).mean(), 1.0)
        if vol_avg_20 > 0:
            features["volume_ratio"] = round(cur_vol / vol_avg_20, 2)
        else:
            features["volume_ratio"] = 1.0
        features["volume_surge"] = bool(features["volume_ratio"] >= 1.5)
    except Exception:
        logger.debug("Volume analysis failed; using defaults.", exc_info=True)
        features["volume_ratio"] = 1.0
        features["volume_surge"] = False

    # ======================================================================
    # 20.  Relative Strength vs NIFTY 50
    # ======================================================================
    try:
        if nifty_df is not None and not nifty_df.empty and len(nifty_df) >= 20:
            nifty_close = nifty_df["Close"].squeeze()
            stock_return = (_last(close) / _safe_float(close.iloc[0], 1)) - 1
            nifty_return = (_last(nifty_close) / _safe_float(nifty_close.iloc[0], 1)) - 1
            if nifty_return != 0:
                features["rs_vs_nifty"] = round(
                    (1 + stock_return) / (1 + nifty_return), 4
                )
            else:
                features["rs_vs_nifty"] = 1.0
        else:
            features["rs_vs_nifty"] = 1.0
    except Exception:
        logger.debug("RS vs NIFTY failed; using default.", exc_info=True)
        features["rs_vs_nifty"] = 1.0

    # ======================================================================
    # 21-24.  52-Week Stats
    # ======================================================================
    try:
        window_52w = min(252, len(close))
        high_52w = _safe_float(close.rolling(window_52w).max().iloc[-1], _safe_float(close.max()))
        low_52w = _safe_float(close.rolling(window_52w).min().iloc[-1], _safe_float(close.min()))

        # Use High column for true 52w high if available
        if len(high) >= window_52w:
            high_52w = max(high_52w, _safe_float(high.rolling(window_52w).max().iloc[-1], high_52w))

        cur = _last(close)
        features["price"] = round(cur, 2)
        features["high_52w"] = round(high_52w, 2)
        features["low_52w"] = round(low_52w, 2)

        if high_52w > 0:
            features["pct_from_52w_high"] = round(((high_52w - cur) / high_52w) * 100, 2)
        else:
            features["pct_from_52w_high"] = 0.0

        if low_52w > 0:
            features["pct_from_52w_low"] = round(((cur - low_52w) / low_52w) * 100, 2)
        else:
            features["pct_from_52w_low"] = 0.0

        # Near 52-week high: within 3%
        features["near_52w_high"] = bool(features["pct_from_52w_high"] <= 3.0) and high_52w > 0

        # Breakout: near 52W high AND volume ratio > 2.0
        vol_ratio = features.get("volume_ratio", 1.0)
        features["breakout_52w"] = bool(features["near_52w_high"] and vol_ratio > 2.0)
    except Exception:
        logger.debug("52-week stats failed; using defaults.", exc_info=True)
        cur = _last(close)
        features["price"] = round(cur, 2) if cur > 0 else 0.0
        features["high_52w"] = features["price"]
        features["low_52w"] = features["price"]
        features["pct_from_52w_high"] = 0.0
        features["pct_from_52w_low"] = 0.0
        features["near_52w_high"] = False
        features["breakout_52w"] = False

    # ======================================================================
    # 25.  Higher-Highs / Higher-Lows count (last 4 weeks, weekly)
    # ======================================================================
    try:
        features["hh_hl_count"] = _compute_hh_hl_count(close, weeks=4)
    except Exception:
        logger.debug("HH/HL count failed; using default.", exc_info=True)
        features["hh_hl_count"] = 0

    # ── Ensure every default key is present ───────────────────────────────
    for key, default_val in defaults.items():
        features.setdefault(key, default_val)

    return features


# ---------------------------------------------------------------------------
# Internal: HH/HL counting
# ---------------------------------------------------------------------------

def _compute_hh_hl_count(close: pd.Series, weeks: int = 4) -> int:
    """Count higher-highs + higher-lows over the last *weeks* weekly bars.

    We resample the daily close into weekly OHLC and then check consecutive
    weeks for higher weekly highs and higher weekly lows.

    Returns
    -------
    int
        Count of HH + HL events (0 to ``2 * (weeks - 1)``).
    """
    if close is None or len(close) < weeks * 5:
        return 0

    # Ensure the index is a DatetimeIndex for resampling
    if not isinstance(close.index, pd.DatetimeIndex):
        return 0

    weekly = close.resample("W").agg(["max", "min"])
    weekly = weekly.dropna()

    if len(weekly) < weeks + 1:
        # Not enough weekly bars
        return 0

    # Take the last (weeks + 1) bars so we can compare *weeks* transitions
    recent = weekly.iloc[-(weeks + 1):]

    hh_hl = 0
    for i in range(1, len(recent)):
        if recent["max"].iloc[i] > recent["max"].iloc[i - 1]:
            hh_hl += 1  # Higher high
        if recent["min"].iloc[i] > recent["min"].iloc[i - 1]:
            hh_hl += 1  # Higher low

    return int(hh_hl)


# ---------------------------------------------------------------------------
# Public API — Technical Sub-Score (0-100)
# ---------------------------------------------------------------------------

def compute_technical_score(features: dict) -> float:
    """Compute a 0-100 technical sub-score from pre-computed features.

    Scoring mirrors the logic in ``Analyzer.py``'s
    ``calculate_real_time_score`` for purely technical dimensions
    (RSI, MACD, ADX, Bollinger, Volume, Golden Cross, Stochastic,
    ROC, 52-week position) and adds bonus points for:

    * ``rs_vs_nifty > 1.2``: **+5** pts
    * ``breakout_52w``:       **+8** pts
    * ``hh_hl_count >= 3``:   **+5** pts

    Parameters
    ----------
    features : dict
        Output of :func:`compute_technical_features`.

    Returns
    -------
    float
        Clamped score in [0, 100].
    """
    if not features:
        return 50.0

    score = 50.0

    # ── RSI ───────────────────────────────────────────────────────────────
    rsi = features.get("rsi_14", 50.0)
    if rsi < 20:
        score += 25
    elif rsi < 30:
        score += 18
    elif rsi < 40:
        score += 10
    elif rsi < 50:
        score += 3
    elif rsi > 80:
        score -= 25
    elif rsi > 70:
        score -= 15
    elif rsi > 60:
        score -= 5

    # ── MACD + Histogram ──────────────────────────────────────────────────
    macd_hist = features.get("macd_histogram", 0.0)
    if features.get("macd_bullish"):
        score += 12 if macd_hist > 0 else 6
    else:
        score -= 12 if macd_hist < 0 else 6

    # ── ADX (Trend Strength) ─────────────────────────────────────────────
    adx = features.get("adx", 20.0)
    di_plus = features.get("di_plus", 20.0)
    di_minus = features.get("di_minus", 20.0)
    if adx > 30:
        score += 10 if di_plus > di_minus else -10
    elif adx > 20:
        score += 5 if di_plus > di_minus else -5

    # ── Bollinger Bands ──────────────────────────────────────────────────
    bb_pct = features.get("bb_pct", 0.5)
    if bb_pct < 0.2:
        score += 15
    elif bb_pct < 0.4:
        score += 8
    elif bb_pct > 0.8:
        score -= 15
    elif bb_pct > 0.6:
        score -= 8

    # ── Volume ───────────────────────────────────────────────────────────
    if features.get("volume_surge"):
        score += 8
    volume_ratio = features.get("volume_ratio", 1.0)
    if volume_ratio > 2.0:
        score += 5
    elif volume_ratio < 0.5:
        score -= 3

    # ── Golden Cross + Price Position ────────────────────────────────────
    if features.get("golden_cross"):
        score += 12
    if features.get("price_above_200sma"):
        score += 10
    else:
        score -= 8

    # ── Stochastic ───────────────────────────────────────────────────────
    stoch_k = features.get("stoch_k", 50.0)
    stoch_d = features.get("stoch_d", 50.0)
    if stoch_k < 20:
        score += 10
    elif stoch_k > 80:
        score -= 10
    if stoch_k > stoch_d:
        score += 5
    else:
        score -= 3

    # ── Rate of Change (Momentum) ────────────────────────────────────────
    roc_5d = features.get("roc_5d", 0.0)
    if roc_5d > 5:
        score += 8
    elif roc_5d > 2:
        score += 4
    elif roc_5d < -5:
        score -= 8
    elif roc_5d < -2:
        score -= 4

    # ── 52-Week Position ─────────────────────────────────────────────────
    pct_from_high = features.get("pct_from_52w_high", 0.0)
    pct_from_low = features.get("pct_from_52w_low", 0.0)
    if pct_from_high > 20:
        score += 5
    if pct_from_low > 50:
        score += 3

    # ── Volatility Penalty ───────────────────────────────────────────────
    atr_pct = features.get("atr_pct", 2.0)
    if atr_pct > 4:
        score -= 3

    # ==================================================================
    # BONUS: New signals not in original Analyzer.py
    # ==================================================================

    # Relative strength vs NIFTY 50
    rs = features.get("rs_vs_nifty", 1.0)
    if rs > 1.2:
        score += 5

    # 52-week breakout on heavy volume
    if features.get("breakout_52w"):
        score += 8

    # Consistent higher-highs / higher-lows pattern
    hh_hl = features.get("hh_hl_count", 0)
    if hh_hl >= 3:
        score += 5

    # ── Clamp to [0, 100] ────────────────────────────────────────────────
    return round(max(0.0, min(100.0, score)), 1)
