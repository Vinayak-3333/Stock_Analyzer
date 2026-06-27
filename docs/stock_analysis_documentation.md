# Stock Analysis Documentation

This document explains the analysis pipeline used by the project to generate stock results, scores, and final `BUY`, `WATCH`, `HOLD`, or `SELL` signals.

The live FastAPI backend uses the modular pipeline in `core/pipeline.py`. The older `Analyzer.py` file is still used for email formatting and Gmail sending, but the stock analysis itself is now mainly driven by:

- `core/pipeline.py`
- `core/features/store.py`
- `core/features/technical.py`
- `core/features/fundamental.py`
- `core/features/institutional.py`
- `core/features/sentiment.py`
- `core/features/regime.py`
- `core/scoring/hybrid.py`
- `core/risk/engine.py`

## 1. High-Level Flow

The live analysis flow is:

1. Initialize DuckDB lake schema.
2. Create an NSE session.
3. Collect NSE quote data for available symbols.
4. If NSE quotes are unavailable, use fallback symbols:
   `RELIANCE`, `TCS`, `HDFCBANK`, `INFY`, `ICICIBANK`, `SBIN`, `BHARTIARTL`, `ITC`, `LT`, `AXISBANK`, `KOTAKBANK`, `HCLTECH`, `SUNPHARMA`, `TATAMOTORS`, `WIPRO`.
5. Compute market regime and macro conditions.
6. Download NIFTY 50 history.
7. For each stock:
   - Download 1-year daily OHLCV history from Yahoo Finance.
   - Store OHLCV into DuckDB.
   - Compute technical features.
   - Compute fundamental features.
   - Compute institutional features.
   - Compute sentiment features.
   - Build scoring inputs.
   - Calculate final multi-factor score.
   - Convert score into signal.
8. Apply risk engine:
   - Stop loss.
   - Target.
   - Reward/risk ratio.
   - Position size.
   - Tradeability flags.
9. Sort final results by score descending.
10. Save the run in SQLite.
11. Send email only if results are not empty and email is enabled.

## 2. Is This a Prediction Model?

Currently, this project is not a trained machine-learning prediction model.

It is a rule-based stock screening and scoring engine. It combines technical, fundamental, institutional, sentiment, market-regime, and risk signals into a weighted score.

There is a placeholder in `calculate_final_score()` for `ml_prob`, where a future ML probability can be blended into the score:

```text
final = weighted_score * 0.65 + ml_probability_score * 0.35
```

But in the current live pipeline, `ml_prob` is not supplied, so the final score is fully rule-based.

## 3. Data Sources

### NSE

Used for:

- Live quote data.
- Last traded price.
- Day change.
- Volume.
- Delivery data.
- FII/DII flow.
- Option chain data.
- PCR.
- Max pain.

### Yahoo Finance

Used for:

- 1-year daily OHLCV price history.
- NIFTY history.
- Company fundamentals.
- Yahoo news.
- Global/macro tickers such as crude, USD/INR, S&P 500, and US 10Y yield.

### Google News RSS

Used for:

- Stock/company headlines.

### Economic Times RSS

Used for:

- General Indian market headlines filtered by stock/company name.

### DuckDB Lake

Used for:

- Raw OHLCV.
- Delivery history.
- FII/DII history.
- Other lake-backed feature storage.

### SQLite

Used by the API to persist completed analysis runs and their result JSON.

## 4. Technical Features

Technical features are computed from 1-year daily OHLCV data.

### Technical Parameters Extracted

| Parameter | Meaning | Default |
|---|---|---:|
| `rsi_14` | 14-period RSI | `50.0` |
| `macd_histogram` | MACD histogram using 12/26/9 MACD | `0.0` |
| `macd_bullish` | MACD line above signal line | `False` |
| `adx` | 14-period ADX trend strength | `20.0` |
| `di_plus` | Positive directional indicator | `20.0` |
| `di_minus` | Negative directional indicator | `20.0` |
| `bb_pct` | Bollinger Band percent position | `0.5` |
| `stoch_k` | Stochastic oscillator %K | `50.0` |
| `stoch_d` | Stochastic oscillator %D | `50.0` |
| `roc_5d` | 5-day rate of change | `0.0` |
| `roc_10d` | 10-day rate of change | `0.0` |
| `roc_20d` | 20-day rate of change | `0.0` |
| `sma_50` | 50-day simple moving average | `0.0` |
| `sma_200` | 200-day simple moving average | `0.0` |
| `sma_ratio` | `sma_50 / sma_200` | `1.0` |
| `golden_cross` | 50-SMA above 200-SMA | `False` |
| `price_above_200sma` | Price above 200-SMA | `False` |
| `atr_pct` | ATR as percentage of price | `0.0` |
| `volume_ratio` | Current volume divided by 20-day average volume | `1.0` |
| `volume_surge` | `volume_ratio >= 1.5` | `False` |
| `rs_vs_nifty` | Relative strength versus NIFTY | `1.0` |
| `near_52w_high` | Price within 3% of 52-week high | `False` |
| `breakout_52w` | Near 52-week high and volume ratio above 2 | `False` |
| `hh_hl_count` | Weekly higher-high/higher-low count over last 4 weeks | `0` |
| `price` | Latest close price | `0.0` |
| `high_52w` | 52-week high | `0.0` |
| `low_52w` | 52-week low | `0.0` |
| `pct_from_52w_high` | Distance below 52-week high | `0.0` |
| `pct_from_52w_low` | Distance above 52-week low | `0.0` |

### Technical Score Rules

Technical scoring starts from `50`.

RSI:

- RSI below 30 improves score as oversold.
- RSI above 75 reduces score as overbought.

MACD:

- Positive MACD histogram improves score.
- Negative MACD histogram reduces score.

ADX and SMA ratio:

- Strong ADX with `sma_ratio > 1` improves score.
- Strong ADX with `sma_ratio < 1` reduces score.

Bollinger Band position:

- Low `bb_pct` improves score as oversold.
- High `bb_pct` reduces score as overbought.

Stochastic:

- `stoch_k < 20` improves score.
- `stoch_k > 80` reduces score.

Momentum:

- Positive 5-day ROC improves score.
- Negative 5-day ROC reduces score.

Moving averages:

- `sma_ratio > 1` improves score.
- `sma_ratio < 1` reduces score.

Volume:

- High `volume_ratio` improves score.
- Very low `volume_ratio` reduces score.

Relative strength:

- Strong `rs_vs_nifty` improves score.

52-week breakout:

- `breakout_52w` gives a bullish score boost.

Volatility:

- High `atr_pct` reduces score.

## 5. Fundamental Features

Fundamental features are mainly collected from Yahoo Finance.

### Fundamental Parameters Extracted

| Parameter | Meaning | Default |
|---|---|---:|
| `pe_ratio` | Trailing P/E ratio | `None` |
| `forward_pe` | Forward P/E ratio | `None` |
| `pe_vs_sector` | P/E divided by fallback sector P/E | `None` |
| `roe` | Return on equity percentage | `None` |
| `roce` | Return on capital employed percentage or approximation | `None` |
| `eps_growth_1y` | 1-year EPS growth percentage | `None` |
| `revenue_growth_1y` | 1-year revenue growth percentage | `None` |
| `debt_to_equity` | Debt-to-equity percentage | `None` |
| `current_ratio` | Current assets divided by current liabilities | `None` |
| `fcf_yield` | Free cash flow divided by market cap | `None` |
| `promoter_holding` | Insider/promoter holding estimate | `None` |
| `pledged_pct` | Pledged promoter shares | `0.0` |
| `market_cap_cr` | Market cap in crores | `None` |
| `dividend_yield` | Dividend yield percentage | `None` |
| `analyst_rating` | Yahoo recommendation mean, lower is better | `None` |
| `company_name` | Company display name | `""` |

### Fundamental Score Rules

Fundamental scoring starts from `50`.

Profitability:

- Higher ROE improves score.
- Very low ROE reduces score.
- Higher ROCE improves score.
- Very low ROCE reduces score.

Growth:

- Strong EPS growth improves score.
- Negative EPS growth reduces score.
- Strong revenue growth improves score.
- Negative revenue growth reduces score.

Balance sheet:

- Low debt-to-equity improves score.
- High debt-to-equity reduces score.
- Good current ratio improves score.
- Weak current ratio reduces score.

Cash generation:

- Positive high FCF yield improves score.
- Negative FCF yield reduces score.

Valuation:

- Lower `pe_vs_sector` improves score.
- Higher `pe_vs_sector` reduces score.

Governance:

- High pledged shares reduce score.

Analyst view:

- Lower analyst rating number improves score.
- Higher analyst rating number reduces score.

Dividend:

- Dividend yield above 2% gives a small score boost.

## 6. Institutional Features

Institutional features use NSE APIs and historical lake data.

### Institutional Parameters Extracted

| Parameter | Meaning | Default |
|---|---|---:|
| `fii_net_3d` | 3-day cumulative FII net flow in crores | `0.0` |
| `dii_net_3d` | 3-day cumulative DII net flow in crores | `0.0` |
| `fii_dii_divergence` | FII selling while DII buying | `False` |
| `delivery_pct` | Latest delivery percentage | `0.0` |
| `delivery_5d_avg` | 5-day average delivery percentage | `0.0` |
| `delivery_spike` | Today's delivery more than 2x 5-day average | `False` |
| `pcr` | Put/call ratio from option chain | `None` |
| `max_pain_distance_pct` | Distance from max pain as percentage | `None` |
| `oi_buildup_bullish` | Bullish open-interest buildup heuristic | `False` |

### Institutional Score Rules

Institutional scoring starts from `50`.

FII/DII:

- Strong positive FII flow improves score.
- Negative FII flow reduces score.
- Strong positive DII flow improves score.
- FII selling plus DII buying gives support bonus.

Delivery:

- High delivery percentage improves score.
- Low delivery percentage reduces score if delivery data exists.
- Delivery spike improves score.

Options:

- PCR above 1.2 improves score.
- PCR below 0.7 reduces score.
- Price close to max pain gives a small range-bound support bonus.
- Bullish OI buildup improves score.

## 7. Sentiment Features

Sentiment is computed using Yahoo Finance news, Google News RSS, and Economic Times RSS.

### Sentiment Parameters Extracted

| Parameter | Meaning | Default |
|---|---|---:|
| `vader_score` | Average VADER compound sentiment, range approximately `-1` to `+1` | `0.0` |
| `keyword_score` | Total keyword score from headline keywords | `0` |
| `headline_count` | Number of unique headlines | `0` |
| `positive_ratio` | Ratio of positive headlines | `0.0` |
| `negative_ratio` | Ratio of negative headlines | `0.0` |
| `event_type` | Detected event type | `"none"` |
| `buzz_factor` | Normalized headline count, capped at `1.0` | `0.0` |
| `top_headlines` | Most important positive/negative headlines | `[]` |
| `sentiment_label` | `POSITIVE`, `NEGATIVE`, or `NEUTRAL` | `NEUTRAL` |

### Sentiment Event Types

The system detects:

- `earnings`
- `acquisition`
- `regulatory`
- `fraud`
- `dividend`
- `order_win`
- `none`

### Sentiment Score Rules

Sentiment scoring starts from `50`.

VADER:

- Strong positive VADER improves score.
- Strong negative VADER reduces score.

Keyword score:

- Positive keywords improve score.
- Negative keywords reduce score.

Events:

- Positive earnings, order wins, acquisitions, and dividends improve score.
- Fraud and regulatory events reduce score.

Buzz:

- High news coverage gives a small boost.
- Very low news coverage gives a small penalty.

## 8. Market Regime and Macro Features

Market regime is computed once per run and shared across stocks.

### Regime Parameters Extracted

| Parameter | Meaning |
|---|---|
| `market_regime` | `BULL`, `NEUTRAL`, or `BEAR` |
| `regime_score` | `2` for bull, `1` for neutral, `0` for bear |
| `nifty_5d_change` | NIFTY 5-day percentage change |
| `nifty_20d_change` | NIFTY 20-day percentage change |
| `nifty_above_200sma` | Whether NIFTY is above its 200-day SMA |
| `vix_value` | Latest India VIX |
| `vix_regime` | `LOW`, `MEDIUM`, or `HIGH` |
| `breadth_pct` | Estimated percentage of sampled NIFTY stocks above 200-SMA |
| `crude_change_1m` | Crude oil 1-month percentage change |
| `crude_regime` | `RISING`, `STABLE`, or `FALLING` |
| `usdinr_change_1m` | USD/INR 1-month percentage change |
| `usdinr_trend` | `STRENGTHENING`, `WEAKENING`, or `STABLE` |
| `us_market_trend` | S&P 500 short-term trend |
| `us_10y_yield` | US 10-year yield |
| `sector_breadth` | 1-day percentage change of sector indices |

### Market Regime Rules

Bull market:

- NIFTY 20-day change above 3%.
- NIFTY above 200-SMA.
- India VIX below 18.

Bear market:

- NIFTY 20-day change below -3%, or
- India VIX above 22 and NIFTY 5-day change below -1%.

Neutral:

- Anything else.

### Regime Multiplier

The detected market regime adjusts the final score:

| Regime | Multiplier |
|---|---:|
| `BULL` | `1.10` |
| `NEUTRAL` | `1.00` |
| `BEAR` | `0.85` |

## 9. Final Multi-Factor Scoring

The project uses `calculate_final_score()` in `core/scoring/hybrid.py`.

Each factor produces a score from `0` to `100`.

The final weighted score is:

```text
final_score =
  fundamental_score   * 0.30 +
  technical_score     * 0.25 +
  institutional_score * 0.15 +
  sentiment_score     * 0.10 +
  sector_score        * 0.10 +
  risk_score          * 0.10
```

Then the regime multiplier is applied:

```text
final_score = final_score * regime_multiplier
```

If a future ML probability is supplied, it can be blended:

```text
final_score = final_score * 0.65 + ml_probability * 100 * 0.35
```

Currently, the live pipeline does not pass an ML probability.

## 10. Signal Generation

The final score maps to signal like this:

| Final Score | Signal |
|---:|---|
| `>= 75` | `BUY` |
| `>= 60` and `< 75` | `WATCH` |
| `>= 40` and `< 60` | `HOLD` |
| `< 40` | `SELL` |

## 11. Risk Engine

After scoring, the risk engine adds trade-management fields.

### Risk Inputs

| Parameter | Meaning | Default Used If Missing |
|---|---|---:|
| `price` | Current/latest price | `100` |
| `atr_pct` | ATR percentage | `2` |
| `avg_volume` / `live_volume` | Liquidity | `200000` |
| `pledged_pct` | Promoter pledging | `0` |
| `market_cap_cr` | Market cap in crores | `5000` |
| `delivery_pct` / `delivery_pct_5d` | Delivery quality | `50` |
| `event_type` | Sentiment/governance event | `None` |

### Risk Rules

Hard tradeability flags:

- Average volume below `100,000` marks stock as illiquid.
- Pledged percentage above `30%` marks high pledging risk.
- Market cap below `500 Cr` marks micro-cap risk.
- Fraud or regulatory event marks governance risk.

Stop loss:

```text
stop_loss = price - 2 * ATR
```

Target:

```text
target = price + 3 * ATR
```

Reward/risk:

```text
rr_ratio = reward_per_share / risk_per_share
```

Position size:

- Uses a simplified half-Kelly approach.
- Caps single-stock position at `10%`.
- Reduces size for:
  - delivery below 35%,
  - pledged shares above 15%,
  - market cap below 2000 Cr.

Portfolio controls:

- Maximum single stock exposure: `10%`.
- Maximum sector exposure: `25%`.
- Maximum total portfolio exposure: `95%`.

## 12. Email Sending Rules

The FastAPI job sends email only when:

```text
send_email is true
results list is not empty
```

The email formatting from `Analyzer.py` uses:

```text
MIN_SCORE = 60
```

This means:

- `BUY` stocks are shown if score is at least `60`.
- `WATCH` stocks are shown if score is at least `60`.
- `SELL` and `HOLD` sections are also formatted separately.

If all stocks fail analysis and `results = []`, no email is sent.

## 13. Final API Result Fields

Each stock result returned to the frontend/API can include:

| Field | Meaning |
|---|---|
| `symbol` | NSE/Yahoo symbol without `.NS` |
| `price` | Latest price |
| `score` | Final score |
| `signal` | `BUY`, `WATCH`, `HOLD`, or `SELL` |
| `rsi` | RSI value |
| `adx` | ADX value |
| `macd_bullish` | MACD bullish flag |
| `golden_cross` | Golden cross flag |
| `vol_surge` | Volume surge flag |
| `volume_ratio` | Current volume vs 20-day average |
| `stoch_k` | Stochastic %K |
| `bb_pct` | Bollinger band position |
| `atr_pct` | ATR percentage |
| `roc_5d` | 5-day momentum |
| `roc_10d` | 10-day momentum |
| `high_52w` | 52-week high |
| `low_52w` | 52-week low |
| `pct_from_52w_high` | Percentage below 52-week high |
| `pct_from_52w_low` | Percentage above 52-week low |
| `sma_200` | 200-day SMA |
| `sma_50` | 50-day SMA |
| `intraday_change` | NSE quote percentage change |
| `news_sentiment` | Sentiment label |
| `news_score` | Sentiment score/value |
| `top_news` | Top news headlines |
| `pe_ratio` | P/E ratio |
| `revenue_growth` | Revenue growth |
| `eps_growth` | EPS growth |
| `analyst_rating` | Yahoo analyst rating |
| `company_name` | Company name |
| `industry` | Industry or sector |
| `live_volume` | Latest volume from NSE quote |
| `delivery_pct` | Latest delivery percentage |
| `delivery_pct_5d` | 5-day delivery average |
| `pledged_pct` | Pledged percentage |
| `market_cap_cr` | Market cap in crores |
| `factor_scores` | Component scores |
| `top_reasons` | Top scoring reasons |
| `regime_multiplier` | Applied market-regime multiplier |
| `stop_loss` | Risk-engine stop loss |
| `target` | Risk-engine target |
| `rr_ratio` | Reward/risk ratio |
| `position_size_pct` | Suggested position size |
| `risk_flags` | Tradeability/risk warnings |
| `is_tradeable` | Risk-engine tradeability flag |
| `shares_per_lakh` | Approximate shares for 1 lakh investment |
| `pipeline` | Pipeline label |

## 14. Important Limitations

This project is a practical screening system, but it has limitations:

- It is rule-based, not statistically trained.
- Signal quality depends heavily on data availability from NSE, Yahoo Finance, Google News, and RSS feeds.
- Some fundamentals can be missing or stale.
- Promoter pledge data defaults to `0.0` because yfinance does not provide true pledged-share data.
- Options data may be unavailable for some symbols.
- News sentiment is headline-based and may misread sarcasm, context, or unrelated company mentions.
- Sector scoring is broad and not deeply company-specific.
- The current score should be treated as a ranking/filtering signal, not financial advice or an automatic trading decision.

## 15. Recommended Validation Before Trusting Signals

To make the signal more reliable, validate it with:

- Historical backtesting.
- Hit-rate by signal type.
- Average forward return after `BUY`, `WATCH`, `HOLD`, and `SELL`.
- Drawdown after signals.
- Sector-wise performance.
- Market-regime-wise performance.
- False-positive review for highly scored stocks.
- Comparison against NIFTY benchmark returns.

The scoring framework is a good starting point for screening, but its thresholds should be tuned using real historical outcomes.
