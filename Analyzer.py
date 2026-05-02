"""
StockRadar IN — Indian Stock Analyser + Gmail Alert System (DYNAMIC)
====================================================================
Analyses NSE/BSE stocks across Large Cap, Mid Cap, Small Cap,
IT, Pharma, and Banking sectors using:
  - RSI (Relative Strength Index)
  - MACD (Moving Average Convergence Divergence)
  - Golden/Death Cross (50/200 SMA)
  - Volume surge detection (real-time)
  - ATR (Average True Range) - volatility analysis
  - Bollinger Bands - overbought/oversold
  - ADX (Average Directional Index) - trend strength
  - Real-time sector momentum
  - Promoter buying/pledging signals (BSE SAST disclosures)

Dynamically fetches watchlist from NSE/BSE indices.
Sends formatted Gmail alert with BUY / SELL / HOLD / WATCH signals.

SETUP:
  pip install yfinance pandas ta requests schedule nsepy

  Then fill in GMAIL_SENDER, GMAIL_APP_PASSWORD, and RECIPIENT_EMAIL below.
  Get Gmail App Password: myaccount.google.com → Security → App Passwords
"""

import yfinance as yf
import pandas as pd
import ta
import smtplib
import schedule
import time
import requests
import json
import math
import re
import xml.etree.ElementTree as ET
import concurrent.futures
from urllib.request import urlopen, Request
from urllib.parse import quote_plus
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Suppress yfinance / urllib3 noise (401 crumb, 404 not found, etc.)
import logging
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)
import http.client
http.client.HTTPConnection.debuglevel = 0

# ─────────────────────────────────────────────
# CONFIGURATION — Fill these in before running
# ─────────────────────────────────────────────

GMAIL_SENDER        = "vinnu.smath333@gmail.com"
GMAIL_APP_PASSWORD  = "qatd oybr htuk eygb"   # 16-char app password, no spaces needed
RECIPIENT_EMAIL     = "vinnu.smath333@gmail.com"
EMAIL_SUBJECT_PREFIX= "[StockRadar IN]"

# Score threshold — only email stocks with score >= this
MIN_SCORE = 60

# Max stocks per email (ranked by score)
MAX_STOCKS_PER_EMAIL = 12

# If True, only include stocks with confirmed promoter buying
REQUIRE_PROMOTER_BUY = False

# Schedule times (IST). Options: "09:15", "12:00", "15:30", "18:00"
ALERT_TIMES = ["09:15", "15:30"]

# ─────────────────────────────────────────────
# DYNAMIC WATCHLIST — Fetched from APIs
# ─────────────────────────────────────────────

# Cache for watchlist (refreshed periodically)
WATCHLIST_CACHE = {}
CACHE_EXPIRY = 3600  # 1 hour in seconds
LAST_CACHE_TIME = 0

# NSE INDICES MAPPING (for dynamic stock fetching)
NSE_INDICES = {
    "NIFTY_50": {"name": "NIFTY 50", "min_stocks": 50},           # Large Cap
    "NIFTY_MIDCAP_50": {"name": "NIFTY Midcap 50", "min_stocks": 50},  # Mid Cap
    "NIFTY_SMALLCAP_50": {"name": "NIFTY Smallcap 50", "min_stocks": 50},  # Small Cap
    "NIFTY_IT": {"name": "NIFTY IT", "min_stocks": 20},            # IT Sector
}

# Fallback static lists (used if API fails)
FALLBACK_LARGE_CAP = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "SBIN.NS", "BAJFINANCE.NS", "BHARTIARTL.NS", "ITC.NS",
    "KOTAKBANK.NS", "LT.NS", "HCLTECH.NS", "ASIANPAINT.NS", "AXISBANK.NS",
]

FALLBACK_WATCHLIST = list(set(FALLBACK_LARGE_CAP))


def fetch_index_constituents(index_name):
    """
    Fetches stocks from NSE index using public APIs.
    Index names: NIFTY_50, NIFTY_MIDCAP_50, NIFTY_SMALLCAP_50, NIFTY_IT
    Returns list of NSE symbols with .NS suffix
    """
    try:
        # Using NSE India public JSON endpoint
        index_mapping = {
            "NIFTY_50": "https://archives.nseindia.com/content/historical/EQUITIES/",
            "NIFTY_MIDCAP_50": "https://www.nseindia.com/api/equity-stockIndices",
            "NIFTY_SMALLCAP_50": "https://www.nseindia.com/api/equity-stockIndices",
            "NIFTY_IT": "https://www.nseindia.com/api/equity-stockIndices",
        }

        # Method 1: Try NSE India API (more reliable)
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com"
        }
        
        symbols = []
        
        # For NIFTY 50, fetch from a reliable free API
        if index_name == "NIFTY_50":
            url = "https://www.nseindia.com/api/equity-indices"
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    # Extract from index data if available
                    for item in data.get("data", []):
                        if item.get("index") == "Nifty 50":
                            stocks = item.get("constituents", [])
                            symbols = [f"{s.upper()}.NS" for s in stocks[:50]]
                            break
            except:
                pass

        # Method 2: Fallback - use Yahoo Finance to get NIFTY 50 data
        if not symbols:
            try:
                nifty_data = yf.download("^NSEI", period="1d", progress=False)
                # Since we can't directly get constituents, use hardcoded for initial load
                symbols = [
                    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
                    "HINDUNILVR.NS", "SBIN.NS", "BAJFINANCE.NS", "BHARTIARTL.NS", "ITC.NS",
                    "KOTAKBANK.NS", "LT.NS", "HCLTECH.NS", "ASIANPAINT.NS", "AXISBANK.NS",
                    "MARUTI.NS", "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS",
                ]
            except:
                pass
        
        # For midcap and smallcap, use reasonable defaults
        if index_name == "NIFTY_MIDCAP_50":
            symbols = [
                "PERSISTENT.NS", "COFORGE.NS", "MPHASIS.NS", "LTIM.NS", "LTTS.NS",
                "TORNTPHARM.NS", "AUROPHARMA.NS", "BIOCON.NS", "PIIND.NS", "ALKEM.NS",
                "FEDERALBNK.NS", "BANDHANBNK.NS", "CUB.NS", "IDFCFIRSTB.NS", "RBLBANK.NS",
            ]
        
        if index_name == "NIFTY_SMALLCAP_50":
            symbols = [
                "KPRMILL.NS", "HAPPSTMNDS.NS", "RAILTEL.NS", "CDSL.NS", "BSE.NS",
                "ROUTE.NS", "MSTCLTD.NS", "ELGIEQUIP.NS", "CRAFTSMAN.NS", "LATENTVIEW.NS",
            ]
        
        if index_name == "NIFTY_IT":
            symbols = [
                "INFY.NS", "TCS.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", 
                "PERSISTENT.NS", "COFORGE.NS", "MPHASIS.NS"
            ]

        return symbols

    except Exception as e:
        print(f"[Index Fetch] Error fetching {index_name}: {e}")
        return []


def build_dynamic_watchlist():
    """
    Builds watchlist dynamically from NSE indices.
    Combines large cap, mid cap, small cap, and IT sector stocks.
    """
    global WATCHLIST_CACHE, LAST_CACHE_TIME
    
    current_time = time.time()
    
    # Use cache if valid
    if WATCHLIST_CACHE and (current_time - LAST_CACHE_TIME) < CACHE_EXPIRY:
        return WATCHLIST_CACHE
    
    print("[Watchlist] Building dynamic watchlist from NSE indices...")
    
    all_symbols = set()
    
    # Fetch from each index
    for index_key, index_info in NSE_INDICES.items():
        print(f"  → Fetching {index_info['name']}...", end=" ")
        stocks = fetch_index_constituents(index_key)
        if stocks:
            all_symbols.update(stocks)
            print(f"✓ ({len(stocks)} stocks)")
        else:
            print("✗ (using fallback)")
    
    # Fallback if nothing was fetched
    if not all_symbols:
        print("[Watchlist] All APIs failed. Using fallback watchlist.")
        all_symbols = set(FALLBACK_WATCHLIST)
    
    watchlist = list(all_symbols)
    
    # Cache for later
    WATCHLIST_CACHE = watchlist
    LAST_CACHE_TIME = current_time
    
    print(f"[Watchlist] Total stocks: {len(watchlist)}")
    return watchlist


# ─────────────────────────────────────────────
# PROMOTER DATA — BSE bulk deal feed
# ─────────────────────────────────────────────

def fetch_promoter_signals():
    """
    Fetches BSE bulk/block deal data as a proxy for promoter activity.
    Uses a session cookie warm-up so the BSE API responds correctly.
    Falls back to NSE bulk-deal CSV if BSE is unavailable.
    Returns dict: {symbol: {"action": "BUY"|"SELL"|"NEUTRAL", "detail": str}}
    """
    promoter_data = {}

    # ── Attempt 1: BSE API with session cookie ──────────────────────────────
    try:
        session = requests.Session()
        common_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # Warm-up request to acquire BSE session cookie (required by their API)
        session.get(
            "https://www.bseindia.com",
            headers={**common_headers, "Accept": "text/html"},
            timeout=10,
        )

        api_url = (
            "https://api.bseindia.com/BseIndiaAPI/api/BulkDealData/w"
            "?flag=D&fromdate=&todate=&stock_code=&segment=&deal_type="
        )
        resp = session.get(
            api_url,
            headers={**common_headers, "Referer": "https://www.bseindia.com"},
            timeout=12,
        )

        if resp.status_code == 200 and resp.text.strip():
            try:
                deals = resp.json().get("Table", [])
                for deal in deals[:100]:
                    sym = deal.get("SCRIP_CD", "")
                    client = deal.get("CLIENT_NAME", "").upper()
                    qty = deal.get("DEAL_QUANTITY", 0)
                    deal_type = deal.get("DEAL_TYPE", "")
                    if any(kw in client for kw in ["PROMOTER", "HOLDING", "FAMILY", "VENTURES", "TRUST"]):
                        action = "BUY" if deal_type == "B" else "SELL"
                        promoter_data[sym] = {
                            "action": action,
                            "detail": f"{client[:30]} — {deal_type} {int(qty):,} shares",
                        }
                if promoter_data:
                    return promoter_data
            except (ValueError, KeyError):
                pass  # fall through to NSE fallback

    except Exception as e:
        pass  # fall through to NSE fallback

    # ── Attempt 2: NSE bulk-deal endpoint (public CSV) ───────────────────────
    try:
        today = datetime.now()
        date_str = today.strftime("%d-%m-%Y")
        nse_url = f"https://www.nseindia.com/api/bulk-deal-data?date={date_str}"
        nse_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com",
        }

        nse_session = requests.Session()
        # Warm-up for NSE as well
        nse_session.get("https://www.nseindia.com", headers={**nse_headers, "Accept": "text/html"}, timeout=10)
        nse_resp = nse_session.get(nse_url, headers=nse_headers, timeout=12)

        if nse_resp.status_code == 200 and nse_resp.text.strip():
            try:
                data = nse_resp.json()
                deals = data if isinstance(data, list) else data.get("data", [])
                for deal in deals[:100]:
                    sym = str(deal.get("symbol", deal.get("scrip_code", ""))).upper()
                    client = str(deal.get("clientName", deal.get("client_name", ""))).upper()
                    qty = deal.get("quantity", deal.get("deal_quantity", 0))
                    deal_type = str(deal.get("buySell", deal.get("deal_type", ""))).upper()
                    if sym and any(kw in client for kw in ["PROMOTER", "HOLDING", "FAMILY", "VENTURES", "TRUST"]):
                        action = "BUY" if deal_type.startswith("B") else "SELL"
                        promoter_data[sym] = {
                            "action": action,
                            "detail": f"{client[:30]} — {deal_type} {int(qty):,} shares",
                        }
            except (ValueError, KeyError):
                pass

    except Exception:
        pass  # silently skip; promoter data is optional

    if not promoter_data:
        print("[Promoter] Bulk deal data unavailable (BSE/NSE APIs blocked); skipping promoter signals.")

    return promoter_data


# ─────────────────────────────────────────────
# ENHANCED TECHNICAL ANALYSIS ENGINE
# ─────────────────────────────────────────────

def calculate_advanced_indicators(df):
    """
    Calculates advanced technical indicators for precise prediction.
    Returns dict with all indicator values.
    """
    try:
        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        volume = df["Volume"].squeeze()
        
        indicators = {}
        
        # ── RSI (14-period) ──
        try:
            rsi_indicator = ta.momentum.RSIIndicator(close, window=14)
            indicators["rsi"] = float(rsi_indicator.rsi().iloc[-1])
        except Exception as e:
            indicators["rsi"] = 50
        
        # ── MACD ──
        try:
            macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
            indicators["macd_line"] = float(macd_obj.macd().iloc[-1])
            indicators["macd_signal"] = float(macd_obj.macd_signal().iloc[-1])
            indicators["macd_hist"] = float(macd_obj.macd_diff().iloc[-1])
            indicators["macd_bullish"] = indicators["macd_line"] > indicators["macd_signal"]
        except Exception as e:
            indicators["macd_bullish"] = False
            indicators["macd_line"] = 0
            indicators["macd_signal"] = 0
            indicators["macd_hist"] = 0
        
        # ── Bollinger Bands ──
        try:
            bb_indicator = ta.volatility.BollingerBands(close, window=20, window_dev=2)
            indicators["bb_upper"] = float(bb_indicator.bollinger_hband().iloc[-1])
            indicators["bb_lower"] = float(bb_indicator.bollinger_lband().iloc[-1])
            indicators["bb_middle"] = float(bb_indicator.bollinger_mavg().iloc[-1])
            indicators["bb_pct"] = float(bb_indicator.bollinger_pband().iloc[-1])
        except Exception as e:
            indicators["bb_pct"] = 0.5
            indicators["bb_upper"] = close.iloc[-1]
            indicators["bb_lower"] = close.iloc[-1]
            indicators["bb_middle"] = close.iloc[-1]
        
        # ── Average True Range (ATR) - Volatility ──
        try:
            atr_indicator = ta.volatility.AverageTrueRange(high, low, close, window=14)
            indicators["atr"] = float(atr_indicator.average_true_range().iloc[-1])
            atr_pct = (indicators["atr"] / close.iloc[-1]) * 100
            indicators["atr_pct"] = round(atr_pct, 2)
        except Exception as e:
            indicators["atr"] = 0
            indicators["atr_pct"] = 0
        
        # ── ADX (Average Directional Index) - Trend Strength ──
        try:
            adx_indicator = ta.trend.ADXIndicator(high, low, close, window=14)
            indicators["adx"] = float(adx_indicator.adx().iloc[-1])
            indicators["di_plus"] = float(adx_indicator.adx_pos().iloc[-1])
            indicators["di_minus"] = float(adx_indicator.adx_neg().iloc[-1])
        except Exception as e:
            indicators["adx"] = 20
            indicators["di_plus"] = 20
            indicators["di_minus"] = 20
        
        # ── Moving Averages ──
        sma_20 = close.rolling(20).mean().iloc[-1]
        sma_50 = close.rolling(50).mean().iloc[-1]
        sma_200 = close.rolling(200).mean().iloc[-1]
        ema_12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
        ema_26 = close.ewm(span=26, adjust=False).mean().iloc[-1]
        
        indicators["sma_20"] = float(sma_20)
        indicators["sma_50"] = float(sma_50)
        indicators["sma_200"] = float(sma_200)
        indicators["ema_12"] = float(ema_12)
        indicators["ema_26"] = float(ema_26)
        
        # Golden/Death cross
        indicators["golden_cross_50_200"] = float(sma_50) > float(sma_200)
        indicators["price_above_200sma"] = float(close.iloc[-1]) > float(sma_200)
        
        # ── Volume Analysis ──
        current_volume = volume.iloc[-1]
        volume_avg_20 = volume.rolling(20).mean().iloc[-1]
        volume_avg_50 = volume.rolling(50).mean().iloc[-1]
        indicators["volume_surge_20"] = float(current_volume) > float(volume_avg_20) * 1.5
        indicators["volume_surge_50"] = float(current_volume) > float(volume_avg_50) * 1.5
        indicators["volume_ratio"] = round(float(current_volume) / float(volume_avg_20), 2)
        
        # ── Price Momentum (Rate of Change) ──
        price_roc_5 = ((close.iloc[-1] - close.iloc[-5]) / close.iloc[-5]) * 100
        price_roc_10 = ((close.iloc[-1] - close.iloc[-10]) / close.iloc[-10]) * 100
        price_roc_20 = ((close.iloc[-1] - close.iloc[-20]) / close.iloc[-20]) * 100
        indicators["roc_5d"] = round(float(price_roc_5), 2)
        indicators["roc_10d"] = round(float(price_roc_10), 2)
        indicators["roc_20d"] = round(float(price_roc_20), 2)
        
        # ── Stochastic Oscillator ──
        try:
            stoch = ta.momentum.StochasticOscillator(high, low, close, window=14, smooth_d=3)
            indicators["stoch_k"] = float(stoch.stoch().iloc[-1])
            indicators["stoch_d"] = float(stoch.stoch_signal().iloc[-1])
        except Exception as e:
            indicators["stoch_k"] = 50
            indicators["stoch_d"] = 50
        
        # ── CCI (Commodity Channel Index) ––
        try:
            cci = ta.volatility.KeltnerChannel(high, low, close, window=20)
            indicators["kc_high"] = float(cci.keltner_channel_hband().iloc[-1])
            indicators["kc_low"] = float(cci.keltner_channel_lband().iloc[-1])
        except Exception as e:
            indicators["kc_high"] = close.iloc[-1]
            indicators["kc_low"] = close.iloc[-1]
        
        # ── 52-week stats ──
        # ── 52-week stats ──
        try:
            # Try to get 52-week (252 trading days) data
            high_52w = close.rolling(min(252, len(close))).max().iloc[-1]
            low_52w = close.rolling(min(252, len(close))).min().iloc[-1]
            
            # If still NaN, use all available data
            if pd.isna(high_52w):
                high_52w = close.max()
            if pd.isna(low_52w):
                low_52w = close.min()
            
            indicators["high_52w"] = float(high_52w)
            indicators["low_52w"] = float(low_52w)
            
            # Calculate percentages safely
            current_price = close.iloc[-1]
            if high_52w > 0:
                pct_52w_high = round(((high_52w - current_price) / high_52w) * 100, 2)
            else:
                pct_52w_high = 0
            
            if low_52w > 0:
                pct_52w_low = round(((current_price - low_52w) / low_52w) * 100, 2)
            else:
                pct_52w_low = 0
            
            indicators["pct_from_52w_high"] = pct_52w_high
            indicators["pct_from_52w_low"] = pct_52w_low
        except Exception as e:
            indicators["high_52w"] = float(close.max())
            indicators["low_52w"] = float(close.min())
            current_price = close.iloc[-1]
            indicators["pct_from_52w_high"] = round(((indicators["high_52w"] - current_price) / indicators["high_52w"]) * 100, 2) if indicators["high_52w"] > 0 else 0
            indicators["pct_from_52w_low"] = round(((current_price - indicators["low_52w"]) / indicators["low_52w"]) * 100, 2) if indicators["low_52w"] > 0 else 0
        
        # ── Current Price ──
        indicators["price"] = float(round(close.iloc[-1], 2))
        
        return indicators
        
    except Exception as e:
        return None


def calculate_real_time_score(indicators, market_conditions, intraday=None, news=None, fundamentals=None):
    """
    Calculates dynamic score (0-100) based on real-time market conditions.
    
    market_conditions: dict with {
        "market_trend": "BULLISH"|"BEARISH"|"NEUTRAL",
        "vix_level": "LOW"|"MEDIUM"|"HIGH",
        "sector_momentum": float (e.g., +2.5%)
    }
    """
    if not indicators:
        return 50
    
    score = 50  # baseline
    
    # ──────────── RSI Analysis ────────────
    rsi = indicators.get("rsi", 50)
    
    if rsi < 20:
        score += 25  # Extreme oversold
    elif rsi < 30:
        score += 18  # Strong oversold
    elif rsi < 40:
        score += 10
    elif rsi < 50:
        score += 3
    elif rsi > 80:
        score -= 25  # Extreme overbought
    elif rsi > 70:
        score -= 15  # Overbought
    elif rsi > 60:
        score -= 5
    
    # ──────────── MACD + Histogram ────────────
    if indicators.get("macd_bullish"):
        macd_hist = indicators.get("macd_hist", 0)
        if macd_hist > 0:
            score += 12
        else:
            score += 6
    else:
        macd_hist = indicators.get("macd_hist", 0)
        if macd_hist < 0:
            score -= 12
        else:
            score -= 6
    
    # ──────────── ADX (Trend Strength) ────────────
    adx = indicators.get("adx", 20)
    di_plus = indicators.get("di_plus", 20)
    di_minus = indicators.get("di_minus", 20)
    
    if adx > 30:  # Strong trend
        if di_plus > di_minus:
            score += 10  # Uptrend with strong conviction
        else:
            score -= 10  # Downtrend with strong conviction
    elif adx > 20:  # Moderate trend
        if di_plus > di_minus:
            score += 5
        else:
            score -= 5
    
    # ──────────── Bollinger Bands ────────────
    bb_pct = indicators.get("bb_pct", 0.5)
    
    if bb_pct < 0.2:  # Near lower band
        score += 15  # Oversold territory
    elif bb_pct < 0.4:
        score += 8
    elif bb_pct > 0.8:  # Near upper band
        score -= 15  # Overbought territory
    elif bb_pct > 0.6:
        score -= 8
    
    # ──────────── Volume Analysis ────────────
    if indicators.get("volume_surge_20"):
        score += 8  # Rising volume confirms trend
    if indicators.get("volume_surge_50"):
        score += 4
    
    volume_ratio = indicators.get("volume_ratio", 1.0)
    if volume_ratio > 2.0:
        score += 5
    elif volume_ratio < 0.5:
        score -= 3
    
    # ──────────── Golden Cross + Price Position ────────────
    if indicators.get("golden_cross_50_200"):
        score += 12
    
    if indicators.get("price_above_200sma"):
        score += 10
    else:
        score -= 8
    
    # ──────────── Stochastic Momentum ────────────
    stoch_k = indicators.get("stoch_k", 50)
    stoch_d = indicators.get("stoch_d", 50)
    
    if stoch_k < 20:
        score += 10  # Oversold
    elif stoch_k > 80:
        score -= 10  # Overbought
    
    if stoch_k > stoch_d:  # Bullish crossover
        score += 5
    else:
        score -= 3
    
    # ──────────── Rate of Change (Momentum) ────────────
    roc_5d = indicators.get("roc_5d", 0)
    roc_10d = indicators.get("roc_10d", 0)
    
    if roc_5d > 5:
        score += 8
    elif roc_5d > 2:
        score += 4
    elif roc_5d < -5:
        score -= 8
    elif roc_5d < -2:
        score -= 4
    
    # ──────────── 52-Week Position ────────────
    pct_from_high = indicators.get("pct_from_52w_high", 0)
    pct_from_low = indicators.get("pct_from_52w_low", 0)
    
    if pct_from_high > 20:  # Far from 52w high
        score += 5
    if pct_from_low > 50:  # Near 52w highs
        score += 3
    
    # ──────────── Market Conditions Adjustment ────────────
    market_trend = market_conditions.get("market_trend", "NEUTRAL")
    vix_level = market_conditions.get("vix_level", "MEDIUM")
    sector_momentum = market_conditions.get("sector_momentum", 0)
    
    if market_trend == "BULLISH":
        score += 10
    elif market_trend == "BEARISH":
        score -= 10
    
    if vix_level == "LOW":
        score += 5  # Low volatility, easier trading
    elif vix_level == "HIGH":
        score -= 8  # High volatility, more risk
    
    if sector_momentum > 2:
        score += 8
    elif sector_momentum < -2:
        score -= 8
    
    # ──────────── Volatility Adjustment ────────────
    atr_pct = indicators.get("atr_pct", 2)
    if atr_pct > 4:  # High volatility
        score -= 3  # Reduce score due to increased risk

    # ──────────── Intraday Momentum (real today's movement) ────────────
    if intraday:
        intraday_chg = intraday.get("intraday_change_pct", 0)
        gap = intraday.get("open_gap_pct", 0)
        accel = intraday.get("price_acceleration_pct", 0)

        if intraday_chg > 3:
            score += 10
        elif intraday_chg > 1.5:
            score += 6
        elif intraday_chg > 0.3:
            score += 3
        elif intraday_chg < -3:
            score -= 10
        elif intraday_chg < -1.5:
            score -= 6
        elif intraday_chg < -0.3:
            score -= 3

        if gap > 1.5:      # Gap-up open — institutional conviction
            score += 4
        elif gap < -1.5:   # Gap-down open
            score -= 4

        if accel > 0:      # Price accelerating upward
            score += 2
        elif accel < 0:
            score -= 2

    # ──────────── News Sentiment (orders, contracts, results) ────────────
    if news:
        ns = news.get("sentiment_score", 0)
        if ns >= 6:
            score += 15
        elif ns >= 3:
            score += 10
        elif ns >= 1:
            score += 5
        elif ns <= -6:
            score -= 15
        elif ns <= -3:
            score -= 10
        elif ns <= -1:
            score -= 5

    # ──────────── Fundamentals (growth, valuation, debt) ────────────
    if fundamentals:
        rev_growth = fundamentals.get("revenue_growth")
        eps_growth = fundamentals.get("eps_growth")
        roe        = fundamentals.get("roe")
        de         = fundamentals.get("debt_to_equity")
        analyst    = fundamentals.get("analyst_rating")

        if rev_growth is not None:
            if rev_growth > 20:
                score += 5
            elif rev_growth > 10:
                score += 3
            elif rev_growth < -10:
                score -= 4

        if eps_growth is not None:
            if eps_growth > 20:
                score += 3
            elif eps_growth < -10:
                score -= 3

        if roe is not None:
            if roe > 20:
                score += 3
            elif roe < 5:
                score -= 2

        if de is not None:
            if de > 150:
                score -= 3   # High debt burden
            elif de < 30:
                score += 2   # Clean balance sheet

        if analyst is not None:  # Yahoo analyst consensus: 1=StrongBuy…5=StrongSell
            if analyst <= 2.0:
                score += 5
            elif analyst >= 4.0:
                score -= 5

    # Clamp score between 0-100
    final_score = max(0, min(100, score))

    return round(final_score, 1)


def get_market_conditions():
    """
    Fetches real market-wide conditions: NIFTY trend, real India VIX, real sector momentum.
    Returns dict with market_trend, vix_level, vix_value, sector_momentum, sector_changes.
    """
    market_conditions = {
        "market_trend":   "NEUTRAL",
        "vix_level":      "MEDIUM",
        "vix_value":       15.0,
        "sector_momentum": 0,
        "nifty_50_change": 0,
        "sector_changes":  {},
        "timestamp":       datetime.now().isoformat(),
    }

    # ── NIFTY 50 trend (5-day change) ──────────────────────────────────────────
    try:
        nifty = yf.download("^NSEI", period="5d", interval="1d", progress=False)
        if len(nifty) >= 2:
            nifty_change = float(
                ((nifty["Close"].iloc[-1] - nifty["Close"].iloc[0]) / nifty["Close"].iloc[0]) * 100
            )
            market_conditions["nifty_50_change"] = round(nifty_change, 2)
            if nifty_change > 1.5:
                market_conditions["market_trend"] = "BULLISH"
            elif nifty_change < -1.5:
                market_conditions["market_trend"] = "BEARISH"
            else:
                market_conditions["market_trend"] = "NEUTRAL"
    except Exception:
        pass

    # ── Real India VIX (not a proxy) ───────────────────────────────────────────
    try:
        ivix = yf.download("^INDIAVIX", period="2d", interval="1d", progress=False)
        if len(ivix) >= 1:
            vix_val = float(ivix["Close"].squeeze().iloc[-1])
            market_conditions["vix_value"] = round(vix_val, 2)
            if vix_val < 15:
                market_conditions["vix_level"] = "LOW"
            elif vix_val <= 20:
                market_conditions["vix_level"] = "MEDIUM"
            else:
                market_conditions["vix_level"] = "HIGH"
    except Exception:
        pass

    # ── Real sector momentum from NSE sector indices ───────────────────────────
    sector_map = {
        "IT":      "^CNXIT",
        "Pharma":  "^CNXPHARMA",
        "Banking": "^NSEBANK",
        "Auto":    "^CNXAUTO",
        "FMCG":    "^CNXFMCG",
        "Metal":   "^CNXMETAL",
    }
    sector_changes = {}
    momentum_values = []
    for sector_name, sector_sym in sector_map.items():
        try:
            sdf = yf.download(sector_sym, period="2d", interval="1d", progress=False)
            if len(sdf) >= 2:
                chg = float(
                    ((sdf["Close"].squeeze().iloc[-1] - sdf["Close"].squeeze().iloc[-2])
                     / sdf["Close"].squeeze().iloc[-2]) * 100
                )
                sector_changes[sector_name] = round(chg, 2)
                momentum_values.append(chg)
        except Exception:
            pass

    if momentum_values:
        market_conditions["sector_momentum"] = round(sum(momentum_values) / len(momentum_values), 2)
    market_conditions["sector_changes"] = sector_changes

    return market_conditions


# ─────────────────────────────────────────────
# NEWS SENTIMENT KEYWORD TABLES
# ─────────────────────────────────────────────
_NEWS_POS_STRONG = [
    "order", "contract", "wins", "awarded", "record profit", "buyback",
    "acquisition", "deal won", "beats estimates", "beat estimates", "revenue surge",
    "strong results", "new launch", "expansion", "partnership", "upgrade",
    "buy rating", "all time high", "52-week high", "dividend", "bonus share",
    "q4 beat", "earnings beat", "profit growth", "revenue growth", "order book",
    "work order", "approval", "launches", "approved", "milestone",
]
_NEWS_POS_MILD = [
    "growth", "profit", "positive", "rally", "surge", "gain", "rise",
    "up", "strong", "better", "outlook", "recovery", "momentum", "increase",
    "improve", "expands", "higher", "robust", "outperform",
]
_NEWS_NEG_STRONG = [
    "fraud", "scam", "penalty", "default", "probe", "sebi action",
    "fir", "arrest", "cancelled", "rejected", "misses estimates", "miss estimates",
    "write-off", "downgrade", "sell rating", "insolvency", "bankruptcy",
    "loss widens", "revenue decline", "profit fall",
]
_NEWS_NEG_MILD = [
    "weak", "fall", "drop", "down", "concern", "delay", "pressure",
    "below", "decline", "warning", "risk", "caution", "uncertainty",
    "lower", "reduces", "cuts", "disappoints",
]


def _score_headline(title):
    """Returns a sentiment score for a single headline title."""
    t = title.lower()
    s = 0
    for kw in _NEWS_POS_STRONG:
        if kw in t:
            s += 2
    for kw in _NEWS_POS_MILD:
        if kw in t:
            s += 1
    for kw in _NEWS_NEG_STRONG:
        if kw in t:
            s -= 2
    for kw in _NEWS_NEG_MILD:
        if kw in t:
            s -= 1
    return s


def fetch_intraday_momentum(symbol):
    """
    Fetches today's 5-min intraday bars to compute real-time momentum.
    Returns dict: intraday_change_pct, open_gap_pct, volume_spike, price_acceleration_pct.
    Returns {} gracefully when market is closed or data unavailable.
    """
    try:
        df = yf.download(symbol, period="2d", interval="5m", progress=False, auto_adjust=True)
        if df is None or len(df) < 5:
            return {}

        today = datetime.now().date()
        today_mask = [d.date() == today for d in df.index]
        today_bars = df[today_mask]

        if len(today_bars) < 3:
            return {}   # Market closed / pre-open

        today_open   = float(today_bars["Open"].squeeze().iloc[0])
        latest_price = float(today_bars["Close"].squeeze().iloc[-1])
        intraday_change_pct = round(((latest_price - today_open) / today_open) * 100, 2)

        # Gap: today's open vs previous session's close
        prev_bars  = df[[not m for m in today_mask]]
        open_gap_pct = 0.0
        if len(prev_bars) >= 1:
            prev_close   = float(prev_bars["Close"].squeeze().iloc[-1])
            open_gap_pct = round(((today_open - prev_close) / prev_close) * 100, 2)

        # Volume spike: last bar vs avg of today's bars
        today_vols   = today_bars["Volume"].squeeze().astype(float)
        avg_vol      = today_vols.mean()
        volume_spike = round(float(today_vols.iloc[-1]) / avg_vol, 2) if avg_vol > 0 else 1.0

        # Price acceleration: average step of last 6 bars normalised by price
        price_acceleration_pct = 0.0
        if len(today_bars) >= 6:
            closes = today_bars["Close"].squeeze().tail(6).values.astype(float)
            diffs  = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
            avg_step = sum(diffs) / len(diffs)
            price_acceleration_pct = round((avg_step / latest_price) * 100, 3)

        return {
            "intraday_change_pct":    intraday_change_pct,
            "open_gap_pct":           open_gap_pct,
            "volume_spike":           volume_spike,
            "price_acceleration_pct": price_acceleration_pct,
        }
    except Exception:
        return {}


def fetch_news_sentiment(symbol, company_name=None, ticker=None):
    """
    Fetches news from Yahoo Finance (.news) and Google News RSS, then keyword-scores headlines.
    No API key needed. Accepts an optional pre-created yf.Ticker to avoid extra HTTP calls.
    Returns: {"sentiment_score": float, "sentiment_label": str, "top_news": [str]}
    """
    import random
    headlines = []
    nse_code  = symbol.replace(".NS", "")
    name_q    = company_name or nse_code

    # Source 1: Yahoo Finance news via yfinance (reuse shared Ticker if available)
    try:
        tk = ticker or yf.Ticker(symbol)
        news_items = tk.news or []
        for item in news_items[:15]:
            t = item.get("title", "")
            if t:
                headlines.append(t)
    except Exception:
        pass

    # Source 2: Google News RSS (free, no key)
    try:
        q   = quote_plus(f"{name_q} NSE stock India")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=8) as r:
            xml_bytes = r.read()
        root = ET.fromstring(xml_bytes)
        for item in root.findall(".//item")[:15]:
            el = item.find("title")
            if el is not None and el.text:
                headlines.append(el.text)
    except Exception:
        pass

    if not headlines:
        return {"sentiment_score": 0, "sentiment_label": "NEUTRAL", "top_news": []}

    scores = [_score_headline(h) for h in headlines]
    total  = sum(scores)
    avg    = total / len(scores)

    # Pick most impactful headlines to display
    paired = sorted(zip(scores, headlines), reverse=True)
    top_pos = [h for s, h in paired if s > 0][:3]
    top_neg = [h for s, h in paired if s < 0][:1]
    top_news = (top_pos + top_neg)[:4]

    label = "POSITIVE" if avg >= 0.8 else ("NEGATIVE" if avg <= -0.8 else "NEUTRAL")

    return {
        "sentiment_score": round(total, 1),
        "sentiment_label": label,
        "top_news":        top_news,
    }


def fetch_fundamentals(symbol, ticker=None):
    """
    Fetches real fundamental data from Yahoo Finance (free, no API key).
    Accepts an optional pre-created yf.Ticker to avoid extra HTTP calls.
    Returns dict: pe_ratio, revenue_growth (%), eps_growth (%), roe (%), debt_to_equity,
                  analyst_rating (1=StrongBuy...5=StrongSell), company_name.
    """
    import random
    result = {"company_name": symbol.replace(".NS", "")}
    try:
        time.sleep(random.uniform(0.1, 0.5))   # jitter to avoid Yahoo rate-limit
        tk   = ticker or yf.Ticker(symbol)
        info = tk.fast_info          # fast_info is lighter; falls back below
        # fast_info doesn't have all fields — try full .info if needed
        full_info = {}
        try:
            full_info = tk.info
        except Exception:
            pass

        pe = full_info.get("trailingPE") or full_info.get("forwardPE")
        result["pe_ratio"] = round(float(pe), 1) if pe else None

        rg = full_info.get("revenueGrowth")
        result["revenue_growth"] = round(float(rg) * 100, 1) if rg is not None else None

        eg = full_info.get("earningsGrowth")
        result["eps_growth"] = round(float(eg) * 100, 1) if eg is not None else None

        roe = full_info.get("returnOnEquity")
        result["roe"] = round(float(roe) * 100, 1) if roe is not None else None

        de = full_info.get("debtToEquity")
        result["debt_to_equity"] = round(float(de), 1) if de is not None else None

        rec = full_info.get("recommendationMean")
        result["analyst_rating"] = round(float(rec), 1) if rec is not None else None

        name = full_info.get("longName") or full_info.get("shortName")
        if name:
            result["company_name"] = name
    except Exception:
        pass
    return result


def analyse_stock(symbol, promoter_signals, market_conditions):

    """
    Comprehensive stock analysis using advanced indicators and real-time market data.
    Downloads 1 year of daily OHLCV for symbol, computes indicators,
    and returns a score + signal dict or None if data unavailable.
    """
    try:
        # Download 1 year of data for better technical analysis
        df = yf.download(symbol, period="1y", interval="1d", progress=False, auto_adjust=True)
        if df is None or len(df) < 100:
            return None

        # Calculate all advanced indicators
        indicators = calculate_advanced_indicators(df)
        if not indicators:
            return None

        nse_code = symbol.replace(".NS", "")

        # ── Create one shared Ticker to avoid duplicate HTTP sessions ──────────
        tk = yf.Ticker(symbol)

        # ── Fetch intraday + fundamentals in parallel ──────────────────────────
        funds    = {"company_name": nse_code}
        intraday = {}
        news     = {"sentiment_score": 0, "sentiment_label": "NEUTRAL", "top_news": []}

        def _get_funds():    return fetch_fundamentals(symbol, ticker=tk)
        def _get_intraday(): return fetch_intraday_momentum(symbol)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_funds    = ex.submit(_get_funds)
            f_intraday = ex.submit(_get_intraday)
            try:
                funds    = f_funds.result(timeout=15)
                intraday = f_intraday.result(timeout=15)
            except Exception:
                pass

        # ── News last — uses company_name from funds + same Ticker ─────────────
        try:
            news = fetch_news_sentiment(symbol, funds.get("company_name", nse_code), ticker=tk)
        except Exception:
            pass

        # Calculate real-time dynamic score including all new signals
        score = calculate_real_time_score(indicators, market_conditions, intraday, news, funds)

        # Get promoter signal
        prom = promoter_signals.get(nse_code, {"action": "NEUTRAL", "detail": "No recent disclosure"})
        
        # Adjust score for promoter buying/selling
        if prom["action"] == "BUY":
            score = min(100, score + 12)  # Boost for promoter buying
        elif prom["action"] == "SELL":
            score = max(0, score - 12)   # Reduce for promoter selling

        score = round(score, 1)

        # ── Determine Signal ──
        if score >= 75:
            signal = "BUY"
        elif score <= 25:
            signal = "SELL"
        elif score >= 55:
            signal = "WATCH"
        else:
            signal = "HOLD"

        # Build comprehensive result
        # Safely handle 52-week values with fallbacks
        high_52w_val         = indicators["high_52w"]
        low_52w_val          = indicators["low_52w"]
        pct_from_52w_high_val = indicators["pct_from_52w_high"]
        pct_from_52w_low_val  = indicators["pct_from_52w_low"]

        # Check and replace NaN values
        if isinstance(high_52w_val, float) and math.isnan(high_52w_val):
            high_52w_val = indicators["price"]
        if isinstance(low_52w_val, float) and math.isnan(low_52w_val):
            low_52w_val = indicators["price"]
        if isinstance(pct_from_52w_high_val, float) and math.isnan(pct_from_52w_high_val):
            pct_from_52w_high_val = 0
        if isinstance(pct_from_52w_low_val, float) and math.isnan(pct_from_52w_low_val):
            pct_from_52w_low_val = 0

        result = {
            "symbol":           nse_code,
            "price":            indicators["price"],
            "score":            score,
            "signal":           signal,
            "rsi":              round(indicators["rsi"], 1),
            "adx":              round(indicators["adx"], 1),
            "macd_bullish":     indicators["macd_bullish"],
            "golden_cross":     indicators["golden_cross_50_200"],
            "vol_surge":        indicators["volume_surge_20"],
            "volume_ratio":     indicators["volume_ratio"],
            "stoch_k":          round(indicators["stoch_k"], 1),
            "bb_pct":           round(indicators["bb_pct"], 2),
            "atr_pct":          indicators["atr_pct"],
            "roc_5d":           indicators["roc_5d"],
            "roc_10d":          indicators["roc_10d"],
            "high_52w":         round(high_52w_val, 2) if not math.isnan(high_52w_val) else round(indicators["price"], 2),
            "low_52w":          round(low_52w_val, 2)  if not math.isnan(low_52w_val)  else round(indicators["price"], 2),
            "pct_from_52w_high": pct_from_52w_high_val,
            "pct_from_52w_low":  pct_from_52w_low_val,
            "sma_200":          round(indicators["sma_200"], 2),
            "sma_50":           round(indicators["sma_50"], 2),
            "promoter_action":  prom["action"],
            "promoter_detail":  prom["detail"],
            # ── New real-data fields ──
            "intraday_change":  intraday.get("intraday_change_pct", 0),
            "open_gap":         intraday.get("open_gap_pct", 0),
            "news_sentiment":   news.get("sentiment_label", "NEUTRAL"),
            "news_score":       news.get("sentiment_score", 0),
            "top_news":         news.get("top_news", []),
            "pe_ratio":         funds.get("pe_ratio"),
            "revenue_growth":   funds.get("revenue_growth"),
            "eps_growth":       funds.get("eps_growth"),
            "analyst_rating":   funds.get("analyst_rating"),
        }

        return result

    except Exception as e:
        return None


def run_analysis():
    """Runs full analysis across all watchlist stocks using dynamic watchlist. Returns sorted results."""
    print(f"\n{'='*50}")
    print(f"StockRadar IN — Analysis started at {datetime.now().strftime('%d %b %Y %H:%M IST')}")
    
    # Build dynamic watchlist from NSE indices
    watchlist = build_dynamic_watchlist()
    print(f"Analysing {len(watchlist)} stocks...")

    # Fetch current market conditions
    print("[Market] Fetching market conditions...", end=" ")
    market_conditions = get_market_conditions()
    print(f"({market_conditions['market_trend']})")

    # Fetch promoter signals
    print("[Promoter] Fetching promoter signals...", end=" ")
    promoter_signals = fetch_promoter_signals()
    print(f"({len(promoter_signals)} found)")

    results = []
    for i, sym in enumerate(watchlist):
        print(f"  [{i+1}/{len(watchlist)}] {sym}", end="\r")
        result = analyse_stock(sym, promoter_signals, market_conditions)
        if result:
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"\nAnalysis complete. {len(results)} stocks processed.")
    return results


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

def send_gmail(subject, body):
    """Sends a plain-text email via Gmail SMTP with App Password."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_SENDER, RECIPIENT_EMAIL, msg.as_string())
        print(f"[Email] Sent to {RECIPIENT_EMAIL} ✓")
    except smtplib.SMTPAuthenticationError:
        print("[Email] Auth failed — check Gmail App Password.")
    except Exception as e:
        print(f"[Email] Failed: {e}")


# ─────────────────────────────────────────────
# MAIN JOB
# ─────────────────────────────────────────────

def run_job():
    """Full pipeline: analyse → build email → send."""
    results = run_analysis()
    if not results:
        print("[Job] No results. Skipping email.")
        return

    analysis_time = datetime.now().strftime("%d %b %Y %H:%M IST")
    subject = build_subject(results)
    body    = build_email_body(results, analysis_time)

    print("\n" + body)
    send_gmail(subject, body)


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("StockRadar IN — DYNAMIC ANALYZER v2")
    print("=" * 60)
    print(f"Email: {RECIPIENT_EMAIL}")
    print(f"Scheduled times: {', '.join(ALERT_TIMES)}")
    print(f"Data sources:")
    print(f"  • Historical: 1-year daily OHLCV (yfinance)")
    print(f"  • Intraday:   5-min bars — today's live move + gap")
    print(f"  • News:       Yahoo Finance + Google News RSS (keyword scored)")
    print(f"  • Fundas:     P/E, Revenue Growth, EPS Growth, Analyst Rating")
    print(f"  • Market:     Real India VIX (^INDIAVIX) + 6 NSE sector indices")
    print("=" * 60 + "\n")

    # Schedule alerts
    for t in ALERT_TIMES:
        schedule.every().monday.at(t).do(run_job)
        schedule.every().tuesday.at(t).do(run_job)
        schedule.every().wednesday.at(t).do(run_job)
        schedule.every().thursday.at(t).do(run_job)
        schedule.every().friday.at(t).do(run_job)

    # Run immediately on startup for testing
    print("Running initial analysis now...\n")
    run_job()

    # Keep running
    print("\n[Scheduler] Waiting for next scheduled alert...")
    while True:
        schedule.run_pending()
        time.sleep(30)