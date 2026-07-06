# 📡 StockRadar IN — AI-Powered Indian Stock Analyzer

> A production-grade, fully automated Indian stock market intelligence system with a live React dashboard deployed on Vercel.

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Vercel-black?logo=vercel)](https://stock-analyzer-vinayak.vercel.app)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python)](https://python.org)
[![React](https://img.shields.io/badge/Frontend-React%2019-61DAFB?logo=react)](https://react.dev)

---

## ✨ What It Does

StockRadar IN scans up to **~2,000 NSE equities** every market day using a **two-tier funnel** — a cheap technical screen over the full universe, then a deep multi-factor dive on the strongest candidates — and delivers:

- 🔭 **Dynamic universe** — resolved fresh each run from 26 NSE indices + bhavcopy + the NSE equity master list (no hardcoded stock lists, ETFs/liquid funds filtered out)
- ⚡ **Two-tier analysis** — Tier 1 screens all ~2,000 with technicals only; Tier 2 runs the full fundamental/news/institutional engine on the top 400
- 📧 **Email alerts** at 09:15 AM & 15:30 IST (market open + close)
- 🌐 **Live React dashboard** with sortable tables, score breakdowns, detail modals
- 📊 **8-layer architecture** — from raw data collection to risk-adjusted recommendations

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                  Data Sources Layer                      │
│  NSE India API · Yahoo Finance · Groww · Screener.in     │
│  GDELT · Google News RSS · Economic Times RSS · NewsAPI  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               Collectors (core/collectors/)              │
│  nse.py · market_data.py · global_data.py               │
│  fundamentals.py · news.py                               │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               Data Lake (DuckDB)                         │
│  raw_ohlcv · raw_delivery · raw_fii_dii · raw_news       │
│  raw_macro · raw_options_summary · known_symbols         │
│  fundamentals_cache (7-day TTL)                          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│      Symbol Universe Resolution (core/pipeline.py)       │
│  NSE live (26 indices) + bhavcopy ∩ equity master        │
│  + DuckDB symbol cache → ~2,000 equities, zero ETFs      │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                 Two-Tier Analysis Funnel                 │
│  Tier 1: technical screen of all ~2,000 (bulk OHLCV)     │
│  Tier 2: top 400 → fundamentals · news · delivery        │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│         Multi-Factor Scoring Engine (core/scoring/)      │
│  Fundamental 30% · Technical 25% · Institutional 15%    │
│  Sentiment 10% · Sector 10% · Risk 10%                  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│             Risk Engine (core/risk/)                     │
│  ATR stops · Kelly sizing · Portfolio concentration      │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│          FastAPI Backend + APScheduler                   │
│  REST API · SQLite history · Email dispatcher            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│           React Dashboard (Vercel)                       │
│  BUY/WATCH/HOLD/SELL tables · Score bars · Modals        │
└─────────────────────────────────────────────────────────┘
```

---

## 🔭 How a Run Works — Dynamic Universe + Two-Tier Funnel

**1. Symbol resolution (no hardcoded lists).** Each run builds the universe fresh:
- **NSE live quotes** from 26 indices (~350 stocks with the richest data)
- **Supplemented** by the day's bhavcopy, filtered against the NSE equity master list (`EQUITY_L.csv`) so ETFs and liquid funds never enter the universe
- **DuckDB `known_symbols` cache** as a last-resort fallback if every live source fails
- Capped at ~2,000; when over cap, the supplement is shuffled so coverage rotates across runs instead of always dropping the same stocks

**2. Tier 1 — broad screen.** All ~2,000 equities get 1 year of daily OHLCV via chunked bulk downloads (throttle-resilient: chunked batches → cooldown retry → per-symbol fallback) and a **technical-only score**. No per-symbol fundamentals/news calls — screening the whole market costs almost nothing.

**3. Tier 2 — deep dive.** The top **`DEEP_ANALYSIS_COUNT`** (default 400) by technical score get the full engine: fundamentals, news sentiment, institutional flows, delivery data — reusing the history Tier 1 already downloaded.

**4. Aggressive caching keeps it fast (~10× fewer HTTP calls):**

| Cache | Backing | Refresh |
|---|---|---|
| Fundamentals | DuckDB `fundamentals_cache` | 7 days (staggered per symbol) |
| Delivery % | DuckDB `raw_delivery`, fed by the daily bhavcopy | Daily, zero per-symbol NSE calls |
| Market-wide news (ET RSS) | In-process | Once per run |
| Symbol universe | DuckDB `known_symbols` | Every successful run |

Tune with env vars: `DEEP_ANALYSIS_COUNT` (Tier-2 size) and `FUNDAMENTALS_CACHE_TTL_DAYS`.

---

## 📁 Project Structure

```
Stock_Analyzer/
├── Analyzer.py                  ← Core analysis engine (technical + scoring)
│
├── backend/
│   └── api.py                   ← FastAPI server + APScheduler + SQLite REST API
│
├── core/                        ← Production v2 architecture modules
│   ├── pipeline.py              ← Orchestrator: symbol resolution + two-tier analysis
│   ├── collectors/
│   │   ├── nse.py               ← NSE live quotes, delivery%, FII/DII, options chain, bhavcopy
│   │   ├── market_data.py       ← Multi-provider quote fallback (Groww, Twelve Data, AV, FMP, Finnhub)
│   │   ├── global_data.py       ← Crude oil, USD/INR, US indices, India VIX, bond yields
│   │   ├── fundamentals.py      ← Screener.in scraper + yfinance fundamentals
│   │   └── news.py              ← Multi-source news + VADER/FinBERT sentiment pipeline
│   ├── features/                ← Per-dimension feature computation
│   │   ├── technical.py · fundamental.py · institutional.py
│   │   ├── sentiment.py · regime.py
│   │   └── store.py             ← Aggregates all features per symbol
│   ├── lake/
│   │   ├── manager.py           ← Thread-safe DuckDB connection manager
│   │   └── schema.py            ← DDL for 14 analytical tables (incl. caches)
│   ├── events/                  ← Pipeline observability (local/Kafka event bus)
│   ├── scoring/
│   │   └── hybrid.py            ← Weighted multi-factor scoring (6 dimensions)
│   ├── risk/
│   │   └── engine.py            ← Stock + portfolio risk management
│   ├── backtest/
│   │   └── engine.py            ← VectorBT backtest + manual fallback
│   └── alerts/
│       └── engine.py            ← Breakout/volume/FII/news alerts → Telegram + Email
│
├── frontend/                    ← React dashboard (deployed on Vercel)
│   ├── src/
│   │   ├── App.jsx              ← Main dashboard + tables + modals
│   │   └── index.css            ← Dark theme design system
│   ├── vercel.json              ← Vercel deployment config
│   └── .env.example             ← Environment variable reference
│
├── docker-compose.yml           ← Kafka + Zookeeper + Redis (optional streaming)
├── requirements.txt             ← All Python dependencies
└── README.md
```

---

## 📊 Scoring Model

Each stock is scored **0–100** across 6 weighted dimensions:

| Dimension | Weight | Signals Used |
|---|---|---|
| **Fundamental** | 30% | ROE, ROCE, EPS growth, Revenue growth, D/E ratio, FCF yield, P/E, Promoter holding |
| **Technical** | 25% | RSI, MACD, ADX, Bollinger Bands, Stochastic, ROC, SMA 50/200, Volume ratio, 52W breakout, RS vs NIFTY |
| **Institutional** | 15% | FII 3-day net flow, DII flow, Delivery %, Delivery spike, PCR, OI buildup, Max pain |
| **Sentiment** | 10% | FinBERT/VADER news score, Event type (earnings/M&A/regulatory/fraud), GDELT tone |
| **Sector** | 10% | Sector momentum, Market breadth, Crude oil regime, USD/INR impact |
| **Risk** | 10% | Liquidity filter, Pledged shares %, Market cap, ATR volatility |

**Regime multiplier** applied on top: Bear (×0.85) / Neutral (×1.00) / Bull (×1.10)

**Final Signal:**
| Score | Signal |
|---|---|
| ≥ 75 | 🟢 BUY |
| 60–74 | 🔵 WATCH |
| 40–59 | ⚪ HOLD |
| < 40 | 🔴 SELL |

---

## 📡 API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Scheduler status, next run times |
| `/api/latest` | GET | Most recent analysis + all stock results |
| `/api/history` | GET | List of all past runs |
| `/api/runs/{id}` | GET | Full results for a specific run |
| `/api/trigger` | POST | Manually trigger analysis now |
| `/api/market` | GET | Live market conditions |
| `/docs` | GET | Interactive Swagger API docs |

---

## 🚀 Local Setup

### 1. Clone & install Python deps
```bash
git clone https://github.com/Vinayak-3333/Stock_Analyzer.git
cd Stock_Analyzer
pip install -r requirements.txt
```

### 2. Configure credentials
Create a `.env` file in the project root (use `.env.nas.example` as the template):
```bash
GMAIL_SENDER=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # 16-char Gmail App Password
RECIPIENT_EMAIL=your@gmail.com
# Optional: market-data API keys, provider order, Telegram, tuning knobs —
# see .env.nas.example for the full list
```
> ⚠️ Never hardcode credentials in source files and never commit `.env` — it's gitignored for a reason.

### 3. Start the backend
```bash
cd backend
python -m uvicorn api:app --host 0.0.0.0 --port 8000
```

### 4. Start the frontend (local dev)
```bash
cd frontend
npm install
npm run dev
# Open http://localhost:5173
```

---

## 🌐 Deployment

### Frontend — Vercel (live)
The React dashboard is deployed on **Vercel** and automatically rebuilds on every `git push` to `master`.

**Re-deploy manually:**
```bash
git add .
git commit -m "your changes"
git push origin master   # Vercel auto-deploys
```

**Environment variable required on Vercel:**
```
VITE_API_URL = https://your-backend-url/api
```

### Backend — Run Locally or on a VPS
The FastAPI backend needs a persistent server (it runs scheduled jobs at 09:15 & 15:30 IST).

**Recommended options:**
| Platform | Cost | Notes |
|---|---|---|
| Railway.app | ~$5/month | Easiest, persistent disk |
| Hetzner CX22 | ~₹360/month | Best value VPS |
| DigitalOcean | ~$4–6/month | Popular, good docs |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Analysis Engine** | Python, yfinance, `ta` library, pandas, numpy |
| **ML / Scoring** | scikit-learn, XGBoost, LightGBM, SHAP, HMM (regime) |
| **Sentiment NLP** | VADER, FinBERT (ProsusAI/finbert via transformers) |
| **Data Lake** | DuckDB (14 analytical tables incl. fundamentals + symbol caches) |
| **Event Bus** | Apache Kafka (optional, via Docker) |
| **Cache** | Redis (optional, via Docker) |
| **Backtesting** | VectorBT (optional) + manual fallback engine |
| **Backend API** | FastAPI, APScheduler, SQLite, Uvicorn |
| **Frontend** | React 19, Vite, Recharts, Lucide React, Axios |
| **Deployment** | Vercel (frontend), Railway/Hetzner (backend) |
| **Data Sources** | NSE India, Yahoo Finance, Groww, Screener.in, GDELT, ET RSS, Google News, NewsAPI |

---

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**. It is **NOT SEBI-registered investment advice**. Past performance does not guarantee future results. Always conduct your own due diligence. Trade at your own risk.

---

## 📬 Contact

Built by **Vinayak** · [GitHub](https://github.com/Vinayak-3333)
