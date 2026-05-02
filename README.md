# 📡 StockRadar IN — Real-Time Indian Stock Analyzer

A fully automated, AI-powered Indian stock market analyzer with a React dashboard.

## ✨ Features

| Feature | Detail |
|---|---|
| **Automated Daily Alerts** | Email at 09:15 & 15:30 IST every weekday (APScheduler) |
| **Real India VIX** | Live `^INDIAVIX` — not a proxy |
| **6 Sector Indices** | IT, Pharma, Banking, Auto, FMCG, Metal momentum |
| **Intraday Momentum** | 5-min bars — today's live price change + gap open |
| **News Sentiment** | Yahoo Finance + Google News RSS, keyword-scored per stock |
| **Fundamentals** | P/E, Revenue Growth, EPS Growth, Analyst Rating (yfinance) |
| **React Dashboard** | Sortable tables, score bars, news icons, detail modals, run history |
| **SQLite Persistence** | Every run saved; queryable via REST API |

## 🏗️ Project Structure

```
Stock_Analyzer/
├── Analyzer.py          ← Core engine (technical + news + fundamentals)
├── backend/
│   └── api.py           ← FastAPI server + APScheduler + SQLite
├── frontend/
│   └── src/
│       ├── App.jsx      ← React dashboard
│       └── index.css    ← Dark theme styles
├── start.bat            ← One-click launcher (Windows)
├── requirements.txt     ← Python dependencies
└── README.md
```

## 🚀 Quick Start

### 1. Python setup
```bash
pip install -r requirements.txt
pip install fastapi "uvicorn[standard]" apscheduler
```

### 2. Configure credentials
Edit `Analyzer.py` — fill in your Gmail App Password and recipient email:
```python
GMAIL_SENDER       = "your@gmail.com"
GMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"   # 16-char Gmail App Password
RECIPIENT_EMAIL    = "your@gmail.com"
```

### 3. Frontend setup
```bash
cd frontend
npm install
```

### 4. Start everything
**Windows:** Double-click `start.bat`

**Manual:**
```bash
# Terminal 1 — API (port 8000)
cd backend
python -m uvicorn api:app --host 0.0.0.0 --port 8000

# Terminal 2 — Dashboard (port 5173)
cd frontend
npm run dev
```

Open **http://localhost:5173**

## 📡 API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Scheduler status, next run times |
| `/api/latest` | GET | Most recent analysis + all stock results |
| `/api/history` | GET | List of past runs |
| `/api/runs/{id}` | GET | Full results for a specific run |
| `/api/trigger` | POST | Manually trigger analysis |
| `/api/market` | GET | Live market conditions |
| `/docs` | GET | Interactive API docs (Swagger) |

## 📊 Scoring Model

Each stock gets a score **0–100** from:

| Signal Layer | Max ± pts | Source |
|---|---|---|
| RSI (14) | ±25 | Oversold/overbought |
| MACD + Histogram | ±12 | Crossover direction |
| ADX trend strength | ±10 | Trend conviction |
| Bollinger Band position | ±15 | Mean reversion |
| Volume surge | +12 | 1.5× avg confirms move |
| Golden/Death cross | ±12 | 50/200 SMA |
| Price above 200 SMA | ±10 | Macro trend |
| Stochastic %K | ±10 | Momentum extremes |
| Rate of Change 5d | ±8 | Short-term momentum |
| **Intraday change** | **±10** | **Live today's move** |
| **News sentiment** | **±15** | **Orders, contracts, results** |
| **Fundamentals** | **±8** | **Revenue growth, analyst rating** |
| Market conditions | ±10 | NIFTY trend |
| Sector momentum | ±8 | Real sector index change |
| India VIX | ±8 | Volatility regime |
| Promoter buying | ±12 | BSE bulk deals |

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**. It is **NOT SEBI-registered investment advice**. Always do your own research. Trade at your own risk.

## 📦 Python Requirements

```
yfinance
pandas
ta
requests
schedule
```

## 🛠️ Tech Stack

- **Analysis Engine:** Python, yfinance, `ta` library
- **Backend API:** FastAPI, APScheduler, SQLite
- **Frontend:** React 18, Vite, Recharts, Lucide React, Axios
- **Data Sources:** Yahoo Finance (free), Google News RSS (free), NSE sector indices
