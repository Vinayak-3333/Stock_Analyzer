import { useState, useEffect, useCallback, useSyncExternalStore } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Cell, PieChart, Pie, AreaChart, Area,
  RadarChart, PolarGrid, PolarAngleAxis, Radar,
} from 'recharts'
import {
  TrendingUp, TrendingDown, Zap, RefreshCw, Clock,
  X, ChevronUp, ChevronDown, AlertTriangle, Search,
} from 'lucide-react'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'

// ── helpers ──────────────────────────────────────────────────────────────────
const fmt = (n, d=2) => n != null ? Number(n).toFixed(d) : '—'
const fmtPct = n => n != null ? `${n > 0 ? '+' : ''}${fmt(n)}%` : '—'
const fmtPrice = n => n != null ? `₹${Number(n).toLocaleString('en-IN', {minimumFractionDigits:2,maximumFractionDigits:2})}` : '—'
const scoreColor = s => s >= 75 ? 'var(--green)' : s >= 55 ? 'var(--blue)' : s <= 25 ? 'var(--red)' : 'var(--text-2)'
const newsScoreColor = s => s >= 60 ? 'var(--green)' : s <= 40 ? 'var(--red)' : 'var(--text-3)'
const getNewsScore = s => {
  const raw = s?.news_score
  const fallback = s?.factor_scores?.sentiment
  if ((raw == null || Number(raw) === 0) && fallback != null) return Number(fallback)
  return raw != null ? Number(raw) : null
}
const trendColor = v => v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu'

const FACTORS = [
  ['fundamental',   'Fundamental'],
  ['technical',     'Technical'],
  ['institutional', 'Institutional'],
  ['sentiment',     'Sentiment'],
  ['sector',        'Sector'],
  ['risk',          'Risk'],
]
const factorList = stock => FACTORS.map(([key, label]) => ({
  key, label, value: Math.max(0, Math.min(100, Number(stock?.factor_scores?.[key] ?? 0))),
}))
const hasFactorData = stock => factorList(stock).some(f => f.value > 0)

// ── useNow — wall-clock time, quantised to stepMs so renders stay stable ─────
function useNow(stepMs = 30_000) {
  const tick = useSyncExternalStore(
    onStoreChange => {
      const t = setInterval(onStoreChange, stepMs)
      return () => clearInterval(t)
    },
    () => Math.floor(Date.now() / stepMs),
  )
  return tick * stepMs
}

// ── useCountUp — animates a number from 0 to target ──────────────────────────
function useCountUp(target, duration = 700) {
  const [val, setVal] = useState(0)
  useEffect(() => {
    const n = Number(target) || 0
    let raf
    const t0 = performance.now()
    const step = now => {
      const p = Math.min((now - t0) / duration, 1)
      setVal(Math.round(n * (1 - Math.pow(1 - p, 3))))   // ease-out cubic
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, duration])
  return val
}

// ── ScoreRing — SVG radial score indicator ────────────────────────────────────
function ScoreRing({ score, size = 56 }) {
  const stroke = 5
  const r = (size - stroke * 2) / 2
  const c = 2 * Math.PI * r
  const color = scoreColor(score)
  const val = Math.max(0, Math.min(100, Number(score) || 0))
  return (
    <svg width={size} height={size} className="score-ring">
      <circle cx={size/2} cy={size/2} r={r} stroke="rgba(255,255,255,0.08)" strokeWidth={stroke} fill="none" />
      <circle
        cx={size/2} cy={size/2} r={r}
        stroke={color} strokeWidth={stroke} fill="none" strokeLinecap="round"
        strokeDasharray={`${(val/100) * c} ${c}`}
        transform={`rotate(-90 ${size/2} ${size/2})`}
        style={{ filter: `drop-shadow(0 0 4px ${color})`, transition: 'stroke-dasharray 0.8s cubic-bezier(0.22,1,0.36,1)' }}
      />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
        fill={color} fontSize={size/4} fontWeight="700" fontFamily="var(--mono)">
        {Math.round(val)}
      </text>
    </svg>
  )
}

// ── FactorBars — six vertical micro-bars, one per scoring dimension ──────────
function FactorBars({ stock }) {
  if (!hasFactorData(stock)) return null
  return (
    <div className="factor-bars">
      {factorList(stock).map(f => (
        <div className="factor-bar" key={f.key} title={`${f.label}: ${Math.round(f.value)}/100`}>
          <div className="fb-track">
            <div className="fb-fill" style={{ height: `${f.value}%`, background: scoreColor(f.value) }} />
          </div>
          <span>{f.label[0]}</span>
        </div>
      ))}
    </div>
  )
}

// ── NewsIcon ──────────────────────────────────────────────────────────────────
function NewsIcon({ sentiment }) {
  if (sentiment === 'POSITIVE') return <span title="Positive news" style={{color:'var(--green)'}}>📰✅</span>
  if (sentiment === 'NEGATIVE') return <span title="Negative news" style={{color:'var(--red)'}}>📰⚠️</span>
  return <span style={{color:'var(--text-3)'}}>—</span>
}

// ── ScoreBar ──────────────────────────────────────────────────────────────────
function ScoreBar({ score }) {
  const color = scoreColor(score)
  return (
    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
      <div className="score-bar-wrap">
        <div className="score-bar-bg">
          <div className="score-bar-fill" style={{
            width:`${score}%`,
            background:`linear-gradient(90deg, ${color}99, ${color})`,
            boxShadow:`0 0 6px ${color}66`,
          }} />
        </div>
      </div>
      <span style={{ fontFamily:'var(--mono)', fontSize:12, color, fontWeight:700 }}>{fmt(score,0)}</span>
    </div>
  )
}

// ── RegimePanel — market regime, NIFTY, VIX, sector heat chips ───────────────
function RegimePanel({ run }) {
  const trend = run.market_trend || 'NEUTRAL'
  const sectors = Object.entries(run.sector_changes || {})
  const vixLevel = run.vix_value > 20 ? 'HIGH' : run.vix_value < 15 ? 'LOW' : 'MEDIUM'
  return (
    <div className="panel">
      <div className="panel-eyebrow">Market Pulse</div>
      <div className="regime-head">
        <div className={`regime-badge ${trend.toLowerCase()}`}>
          {trend === 'BULLISH' ? <TrendingUp size={18}/> : trend === 'BEARISH' ? <TrendingDown size={18}/> : <span style={{fontSize:15}}>≈</span>}
          {trend}
        </div>
      </div>
      <div className="regime-stats">
        <div className="regime-stat">
          <div className="rs-label">NIFTY 50 · 5d</div>
          <div className={`rs-value ${trendColor(run.nifty_change)}`}>{fmtPct(run.nifty_change)}</div>
        </div>
        <div className="regime-stat">
          <div className="rs-label">India VIX</div>
          <div className={`rs-value ${run.vix_value > 20 ? 'neg' : run.vix_value < 15 ? 'pos' : 'neu'}`}>
            {fmt(run.vix_value,1)} <span className="rs-sub">{vixLevel}</span>
          </div>
        </div>
      </div>
      {sectors.length > 0 && (
        <div className="sector-heat">
          {sectors.map(([name, chg]) => (
            <span key={name} className={`heat-chip ${trendColor(chg)}`} title={`${name}: ${fmtPct(chg)} today`}>
              {name} {fmtPct(chg)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── SignalDonut — distribution of BUY / WATCH / HOLD / SELL ──────────────────
function SignalDonut({ counts }) {
  const total = counts.reduce((a, c) => a + c.value, 0)
  const animated = useCountUp(total)
  const data = counts.filter(c => c.value > 0)
  return (
    <div className="panel">
      <div className="panel-eyebrow">Signal Mix</div>
      <div className="donut-wrap">
        <div className="donut-chart">
          <ResponsiveContainer width="100%" height={150}>
            <PieChart>
              <Pie data={data} dataKey="value" nameKey="name"
                innerRadius={48} outerRadius={66} paddingAngle={3}
                strokeWidth={0} startAngle={90} endAngle={-270} isAnimationActive>
                {data.map(c => <Cell key={c.name} fill={c.color} />)}
              </Pie>
              <Tooltip
                contentStyle={{background:'#0d0d1a',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,fontSize:12}}
                itemStyle={{color:'var(--text-1)'}}
                formatter={(v, name) => [`${v} stocks`, name]}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="donut-center">
            <div className="donut-total">{animated}</div>
            <div className="donut-sub">analysed</div>
          </div>
        </div>
        <div className="donut-legend">
          {counts.map(c => (
            <div className="legend-row" key={c.name}>
              <span className="legend-dot" style={{ background: c.color }} />
              <span className="legend-name">{c.name}</span>
              <span className="legend-val mono">{c.value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── RunTrend — coverage & market across recent runs ──────────────────────────
function RunTrend({ history }) {
  const data = [...history].reverse().map(r => ({
    t: new Date(r.run_time).toLocaleDateString('en-IN', { day:'2-digit', month:'short' }),
    stocks: r.stock_count ?? 0,
    nifty: r.nifty_change,
    vix: r.vix_value,
  }))
  if (data.length < 2) return (
    <div className="panel">
      <div className="panel-eyebrow">Run Trend</div>
      <div className="panel-placeholder">More runs needed to chart a trend</div>
    </div>
  )
  return (
    <div className="panel">
      <div className="panel-eyebrow">Run Trend · stocks per run</div>
      <ResponsiveContainer width="100%" height={150}>
        <AreaChart data={data} margin={{ top: 10, right: 4, left: -26, bottom: 0 }}>
          <defs>
            <linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--green)" stopOpacity={0.35}/>
              <stop offset="100%" stopColor="var(--green)" stopOpacity={0}/>
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false}/>
          <XAxis dataKey="t" tick={{ fontSize: 9, fill: '#475569' }} tickLine={false} axisLine={false}/>
          <YAxis tick={{ fontSize: 9, fill: '#475569' }} tickLine={false} axisLine={false}/>
          <Tooltip
            contentStyle={{background:'#0d0d1a',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,fontSize:12}}
            labelStyle={{color:'#94a3b8'}}
            formatter={(v, name) => name === 'stocks' ? [`${v}`, 'stocks analysed'] : [v, name]}
          />
          <Area type="monotone" dataKey="stocks" stroke="var(--green)" strokeWidth={2}
            fill="url(#trendFill)" dot={false} activeDot={{ r: 3 }}/>
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── PickCard — designed card for a top-ranked stock ──────────────────────────
function PickCard({ stock, rank, onSelect }) {
  return (
    <button className="pick-card rise" style={{ animationDelay: `${rank * 0.06}s` }} onClick={() => onSelect(stock)}>
      <div className="pick-head">
        <span className="pick-rank">#{rank}</span>
        <span className={`signal-pill ${stock.signal}`}>{stock.signal}</span>
      </div>
      <div className="pick-symbol">{stock.symbol}</div>
      <div className="pick-company" title={stock.company_name}>{stock.company_name || ' '}</div>
      <div className="pick-body">
        <div>
          <div className="pick-price">{fmtPrice(stock.price)}</div>
          <div className={`pick-change ${trendColor(stock.intraday_change)}`}>
            {stock.intraday_change ? fmtPct(stock.intraday_change) : '—'} today
          </div>
        </div>
        <ScoreRing score={stock.score} />
      </div>
      <FactorBars stock={stock} />
    </button>
  )
}

// ── StockModal ────────────────────────────────────────────────────────────────
function StockModal({ stock, onClose }) {
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose])

  if (!stock) return null
  const newsScore = getNewsScore(stock)
  const factors = factorList(stock)
  const showRadar = hasFactorData(stock)
  const showRisk = stock.stop_loss != null || stock.target != null

  const technicals = [
    { k:'RSI (14)',      v: fmt(stock.rsi,1),    cls: stock.rsi < 30 ? 'pos' : stock.rsi > 70 ? 'neg' : '' },
    { k:'ADX',          v: fmt(stock.adx,0) },
    { k:'MACD',         v: stock.macd_bullish ? '↑ Bullish' : '↓ Bearish', cls: stock.macd_bullish ? 'pos' : 'neg' },
    { k:'Stochastic %K',v: fmt(stock.stoch_k,0) },
    { k:'BB Position',  v: fmt(stock.bb_pct,2),  cls: stock.bb_pct < 0.3 ? 'pos' : stock.bb_pct > 0.7 ? 'neg' : '' },
    { k:'ATR Volatility',v:`${fmt(stock.atr_pct)}%` },
    { k:'ROC 5d',       v: fmtPct(stock.roc_5d), cls: trendColor(stock.roc_5d) },
    { k:'Volume Ratio', v:`${fmt(stock.volume_ratio,1)}x` },
    { k:'SMA 50',       v: fmtPrice(stock.sma_50) },
    { k:'SMA 200',      v: fmtPrice(stock.sma_200) },
  ]
  const fundamentals = [
    { k:'P/E Ratio',       v: stock.pe_ratio != null ? fmt(stock.pe_ratio,1) : '—' },
    { k:'Market Cap',      v: stock.market_cap_cr != null ? `₹${Number(stock.market_cap_cr).toLocaleString('en-IN',{maximumFractionDigits:0})} Cr` : '—' },
    { k:'Revenue Growth',  v: fmtPct(stock.revenue_growth) },
    { k:'EPS Growth',      v: fmtPct(stock.eps_growth) },
    { k:'Analyst Rating',  v: stock.analyst_rating != null
        ? [{1:'Strong Buy',2:'Buy',3:'Hold',4:'Sell',5:'Strong Sell'}[Math.round(stock.analyst_rating)] || fmt(stock.analyst_rating,1)]
        : '—',
      cls: stock.analyst_rating <= 2 ? 'pos' : stock.analyst_rating >= 4 ? 'neg' : ''
    },
    { k:'Delivery %',      v: stock.delivery_pct ? `${fmt(stock.delivery_pct,1)}%` : '—' },
    { k:'52W High',        v: fmtPrice(stock.high_52w) },
    { k:'52W Low',         v: fmtPrice(stock.low_52w) },
    { k:'% from High',     v: fmtPct(-stock.pct_from_52w_high), cls:'neg' },
    { k:'% from Low',      v: fmtPct(stock.pct_from_52w_low), cls:'pos' },
  ]

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}><X size={14}/> Close</button>

        <div style={{display:'flex', alignItems:'flex-start', justifyContent:'space-between', gap:16}}>
          <div>
            <div className="modal-title">{stock.symbol}</div>
            {stock.company_name && <div className="modal-company">{stock.company_name}{stock.industry && stock.industry !== 'Unknown' ? ` · ${stock.industry}` : ''}</div>}
            <div className="modal-price">{fmtPrice(stock.price)}</div>
            <div style={{marginTop:8, display:'flex', gap:8, flexWrap:'wrap', alignItems:'center'}}>
              <span className={`signal-pill ${stock.signal}`}>{stock.signal}</span>
              {stock.intraday_change !== 0 && (
                <span style={{fontSize:12, fontFamily:'var(--mono)', color: stock.intraday_change > 0 ? 'var(--green)' : 'var(--red)'}}>
                  Today: {fmtPct(stock.intraday_change)}
                </span>
              )}
              <NewsIcon sentiment={stock.news_sentiment} />
              <span style={{fontSize:12, color:'var(--text-3)'}}>News score: {fmt(newsScore,0)}/100</span>
            </div>
          </div>
          <div style={{textAlign:'center', flexShrink:0, paddingTop:6}}>
            <ScoreRing score={stock.score} size={84} />
            <div style={{fontSize:10, color:'var(--text-3)', marginTop:4}}>Score /100</div>
          </div>
        </div>

        <div className="modal-grid">
          {/* Factor radar — the six scoring dimensions */}
          {showRadar && (
            <div className="modal-box">
              <div className="modal-box-title">🎯 Factor Scores</div>
              <ResponsiveContainer width="100%" height={200}>
                <RadarChart data={factors} outerRadius={72}>
                  <PolarGrid stroke="rgba(255,255,255,0.08)" />
                  <PolarAngleAxis dataKey="label" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                  <Radar dataKey="value" stroke="var(--blue)" strokeWidth={2}
                    fill="var(--blue)" fillOpacity={0.22} isAnimationActive />
                  <Tooltip
                    contentStyle={{background:'#0d0d1a',border:'1px solid rgba(255,255,255,0.1)',borderRadius:8,fontSize:12}}
                    formatter={v => [`${Math.round(v)}/100`]}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Risk plan from the risk engine */}
          {showRisk && (
            <div className="modal-box">
              <div className="modal-box-title">🛡 Risk Plan</div>
              {[
                { k:'Entry (last price)', v: fmtPrice(stock.price) },
                { k:'Stop Loss',  v: fmtPrice(stock.stop_loss),
                  s: stock.stop_loss && stock.price ? fmtPct(((stock.stop_loss - stock.price)/stock.price)*100) : null, cls:'neg' },
                { k:'Target',     v: fmtPrice(stock.target),
                  s: stock.target && stock.price ? fmtPct(((stock.target - stock.price)/stock.price)*100) : null, cls:'pos' },
                { k:'Risk : Reward',   v: stock.rr_ratio != null ? `1 : ${fmt(stock.rr_ratio,1)}` : '—' },
                { k:'Position Size',   v: stock.position_size_pct != null ? `${fmt(stock.position_size_pct,1)}% of capital` : '—' },
              ].map(({k,v,s,cls}) => (
                <div className="kv-row" key={k}>
                  <span className="kv-key">{k}</span>
                  <span className={`kv-val ${cls||''}`}>{v}{s ? <span style={{opacity:0.65, marginLeft:6}}>({s})</span> : null}</span>
                </div>
              ))}
              {stock.risk_flags?.length > 0 && (
                <div className="risk-flags">
                  {stock.risk_flags.map((f, i) => (
                    <span className="risk-flag" key={i}><AlertTriangle size={10}/> {f}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Technicals */}
          <div className="modal-box">
            <div className="modal-box-title">📊 Technical Indicators</div>
            {technicals.map(({k,v,cls}) => (
              <div className="kv-row" key={k}>
                <span className="kv-key">{k}</span>
                <span className={`kv-val ${cls||''}`}>{Array.isArray(v) ? v[0] : v}</span>
              </div>
            ))}
          </div>

          {/* Fundamentals */}
          <div className="modal-box">
            <div className="modal-box-title">🏦 Fundamentals</div>
            {fundamentals.map(({k,v,cls}) => (
              <div className="kv-row" key={k}>
                <span className="kv-key">{k}</span>
                <span className={`kv-val ${cls||''}`}>{Array.isArray(v) ? v[0] : v}</span>
              </div>
            ))}
            {stock.promoter_action && stock.promoter_action !== 'NEUTRAL' && (
              <div className="kv-row">
                <span className="kv-key">Promoter</span>
                <span className={`kv-val ${stock.promoter_action === 'BUY' ? 'pos' : 'neg'}`}>{stock.promoter_action}</span>
              </div>
            )}
          </div>

          {/* Why this score */}
          {stock.top_reasons?.length > 0 && (
            <div className="modal-box" style={{gridColumn:'1/-1'}}>
              <div className="modal-box-title">💡 Why This Score</div>
              <div className="reason-list">
                {stock.top_reasons.map((r, i) => (
                  <div className="reason-item" key={i}>
                    <span className="reason-index">{i+1}</span>{r}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* News */}
          {stock.top_news && stock.top_news.length > 0 && (
            <div className="modal-box" style={{gridColumn:'1/-1'}}>
              <div className="modal-box-title" style={{marginBottom:12}}>
                📰 Latest News — {stock.news_sentiment}
              </div>
              {stock.top_news.map((h, i) => (
                <div className="news-item" key={i}>{h}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── StocksTable ───────────────────────────────────────────────────────────────
function StocksTable({ stocks, filter, title, colorClass, query, onSelect }) {
  const [sortKey, setSortKey] = useState('score')
  const [sortDir, setSortDir] = useState(-1)

  const q = (query || '').trim().toUpperCase()
  const enriched = stocks.map(s => ({ ...s, news_score: getNewsScore(s) }))
  const filtered = enriched.filter(s =>
    (!filter || s.signal === filter) &&
    (!q || s.symbol?.toUpperCase().includes(q) || (s.company_name || '').toUpperCase().includes(q))
  )
  const sorted = [...filtered].sort((a,b) => {
    const av = a[sortKey] ?? -999, bv = b[sortKey] ?? -999
    return typeof av === 'string' ? av.localeCompare(bv)*sortDir : (bv-av)*-sortDir
  })

  const col = (key, label, desc) => (
    <th title={desc} onClick={() => { setSortDir(sortKey===key ? -sortDir : -1); setSortKey(key) }}>
      {label}
      {sortKey===key && (sortDir===-1 ? <ChevronDown size={11} style={{marginLeft:4}}/> : <ChevronUp size={11} style={{marginLeft:4}}/>)}
    </th>
  )

  if (!sorted.length) return null

  return (
    <div className="rise">
      <div className="section-header">
        <div className="section-title">
          {colorClass === 'buy' ? '🟢' : colorClass === 'sell' ? '🔴' : colorClass === 'hold' ? '⚪' : '🔵'} {title}
          <span>{sorted.length} stocks</span>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {col('symbol','Symbol', 'Stock ticker symbol on NSE')}
              {col('price','Price', 'Latest traded price in INR')}
              {col('score','Score', 'Multi-Factor Score (0-100)')}
              {col('rsi','RSI', 'Relative Strength Index (Momentum indicator)')}
              {col('adx','ADX', 'Average Directional Index (Trend strength > 25 is strong)')}
              {col('atr_pct','ATR', 'Average True Range % (Daily volatility measure)')}
              {col('intraday_change','Today', 'Intraday price change %')}
              {col('news_score','News', 'Sentiment sub-score from recent news (0-100)')}
              {col('revenue_growth','Rev Gr.', '1-Year Revenue Growth % (Fundamental metric)')}
              {col('target','Target', 'Risk-engine price target')}
              {col('stop_loss','Stop', 'ATR-based stop loss')}
              <th title="Final recommendation signal">Signal</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(s => (
              <tr key={s.symbol}>
                <td className="symbol-cell" onClick={() => onSelect(s)}>{s.symbol}</td>
                <td className="mono">{fmtPrice(s.price)}</td>
                <td><ScoreBar score={s.score} /></td>
                <td className={`mono ${s.rsi < 30 ? 'pos' : s.rsi > 70 ? 'neg' : ''}`}>{fmt(s.rsi,1)}</td>
                <td className="mono">{fmt(s.adx,0)}</td>
                <td className="mono">{fmt(s.atr_pct)}%</td>
                <td className={`mono ${s.intraday_change > 0 ? 'pos' : s.intraday_change < 0 ? 'neg' : ''}`}>
                  {s.intraday_change ? fmtPct(s.intraday_change) : '—'}
                </td>
                <td>
                  <span style={{fontFamily:'var(--mono)', fontSize:12, color: s.news_score != null ? newsScoreColor(s.news_score) : 'var(--text-3)'}}>
                    {s.news_score != null ? fmt(s.news_score,0) : '—'}
                  </span>
                  <NewsIcon sentiment={s.news_sentiment} />
                </td>
                <td className={`mono ${trendColor(s.revenue_growth)}`}>{fmtPct(s.revenue_growth)}</td>
                <td className="mono pos">{s.target != null ? fmtPrice(s.target) : '—'}</td>
                <td className="mono neg">{s.stop_loss != null ? fmtPrice(s.stop_loss) : '—'}</td>
                <td><span className={`signal-pill ${s.signal}`}>{s.signal}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── HistoryPanel ──────────────────────────────────────────────────────────────
function HistoryPanel({ history }) {
  if (!history.length) return (
    <div className="empty"><div className="empty-icon">📋</div><div className="empty-msg">No history yet</div></div>
  )
  return (
    <div className="rise">
      <div className="section-header">
        <div className="section-title">🕐 Run History <span>{history.length} runs</span></div>
      </div>
      <div className="history-list">
        {history.map(r => {
          const t = new Date(r.run_time)
          return (
            <div className="history-item" key={r.id}>
              <div className="history-time">
                {t.toLocaleDateString('en-IN', {day:'2-digit',month:'short'})}
                {' '}
                {t.toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit'})}
              </div>
              <div className="history-stats">
                <span className={`badge ${r.market_trend?.toLowerCase() || 'neutral'}`}>{r.market_trend || '—'}</span>
                <span className="history-stat" style={{color:'var(--text-2)'}}>
                  📊 {r.stock_count ?? '?'} stocks
                </span>
                {r.nifty_change != null && (
                  <span className={`history-stat mono ${trendColor(r.nifty_change)}`}>
                    NIFTY {fmtPct(r.nifty_change)}
                  </span>
                )}
                {r.vix_value && (
                  <span className="history-stat" style={{color:'var(--text-3)'}}>VIX {fmt(r.vix_value,1)}</span>
                )}
                {r.email_sent ? <span style={{color:'var(--green)',fontSize:11}}>✉ Email sent</span> : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── ScoreDistChart ────────────────────────────────────────────────────────────
function ScoreDistChart({ results }) {
  if (!results.length) return null
  const bins = Array.from({length:10}, (_,i) => ({ range:`${i*10}-${i*10+10}`, count:0 }))
  results.forEach(r => { const i = Math.min(Math.floor(r.score/10), 9); bins[i].count++ })
  return (
    <div className="panel" style={{padding:'16px 16px 8px'}}>
      <div className="panel-eyebrow">Score Distribution</div>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={bins} margin={{top:6,right:0,left:-20,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false}/>
          <XAxis dataKey="range" tick={{fontSize:9, fill:'#475569'}} tickLine={false} axisLine={false}/>
          <YAxis tick={{fontSize:9, fill:'#475569'}} tickLine={false} axisLine={false}/>
          <Tooltip
            contentStyle={{background:'#0d0d1a',border:'1px solid rgba(255,255,255,0.08)',borderRadius:8,fontSize:12}}
            labelStyle={{color:'#94a3b8'}}
          />
          <Bar dataKey="count" radius={[3,3,0,0]}>
            {bins.map((entry, idx) => (
              <Cell key={idx} fill={idx >= 7 ? 'var(--green)' : idx >= 5 ? 'var(--blue)' : 'rgba(148,163,184,0.35)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── App ────────────────────────────────────────────────────────────────────────
export default function App() {
  const [status,    setStatus]    = useState(null)
  const [latest,    setLatest]    = useState(null)
  const [history,   setHistory]   = useState([])
  const [loading,   setLoading]   = useState(true)
  const [triggering, setTriggering] = useState(false)
  const [backendOff, setBackendOff] = useState(false)
  const [pollTimer,  setPollTimer]  = useState(null)
  const [tab, setTab] = useState('signals')   // 'signals' | 'history'
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const now = useNow()   // refreshes every 30s for the countdown chip

  const fetchAll = useCallback(async () => {
    try {
      // Cache-busting: add timestamp so browser never serves stale HTTP-cached API response
      const ts = Date.now()
      const [s, l, h] = await Promise.all([
        axios.get(`${API}/status`,        { params: { _t: ts }, headers: { 'Cache-Control': 'no-cache' } }),
        axios.get(`${API}/latest`,        { params: { _t: ts }, headers: { 'Cache-Control': 'no-cache' } }),
        axios.get(`${API}/history?limit=30`, { params: { _t: ts }, headers: { 'Cache-Control': 'no-cache' } }),
      ])
      setBackendOff(false)
      setStatus(s.data)
      setLatest(l.data)
      setHistory(h.data)
      return s.data   // return status so callers can inspect it
    } catch (e) {
      setBackendOff(true)
      console.error('Fetch failed — backend offline?', e)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    const kickoff = setTimeout(fetchAll, 0)     // initial load
    const t = setInterval(fetchAll, 30_000)     // refresh every 30s
    return () => { clearTimeout(kickoff); clearInterval(t) }
  }, [fetchAll])

  // Clean up poll timer on unmount
  useEffect(() => () => { if (pollTimer) clearInterval(pollTimer) }, [pollTimer])

  const triggerRun = async () => {
    setTriggering(true)
    try {
      await axios.post(`${API}/trigger?email=true`)
    } catch (e) {
      console.error('Trigger failed', e)
      setTriggering(false)
      return
    }

    // Wait 3s for the background task to actually start and set analysis_running=true
    // (there's a race: POST returns before the task flips the flag)
    await new Promise(resolve => setTimeout(resolve, 3000))
    await fetchAll()  // first refresh

    const startedAt = Date.now()
    const MAX_POLL_MS = 20 * 60 * 1000  // 20 min safety net

    // Poll every 5s until analysis_running goes false (run finished)
    const timer = setInterval(async () => {
      const s = await fetchAll()
      const elapsed = Date.now() - startedAt
      if ((s && !s.analysis_running) || elapsed > MAX_POLL_MS) {
        clearInterval(timer)
        setPollTimer(null)
        setTriggering(false)
        // Final fetch to get the fresh results
        await fetchAll()
      }
    }, 5000)
    setPollTimer(timer)
  }

  const results = latest?.results || []
  const run     = latest?.run
  const buys    = results.filter(r => r.signal === 'BUY')
  const sells   = results.filter(r => r.signal === 'SELL')
  const watches = results.filter(r => r.signal === 'WATCH')
  const holds   = results.filter(r => r.signal === 'HOLD')

  const isRunning = status?.analysis_running || triggering
  const mktTrend  = run?.market_trend || 'NEUTRAL'
  const lastTime  = run?.run_time ? new Date(run.run_time).toLocaleString('en-IN') : null

  const topPicks = [...results].sort((a, b) => b.score - a.score).slice(0, 5)

  const signalCounts = [
    { name: 'BUY',   value: buys.length,    color: 'var(--green)' },
    { name: 'WATCH', value: watches.length, color: 'var(--blue)' },
    { name: 'HOLD',  value: holds.length,   color: 'rgba(148,163,184,0.45)' },
    { name: 'SELL',  value: sells.length,   color: 'var(--red)' },
  ]

  const nextRunAt = (status?.scheduled_jobs || [])
    .map(j => new Date(j.next_run).getTime())
    .filter(t => t > now)
    .sort((a, b) => a - b)[0]
  const fmtEta = ms => {
    const m = Math.max(1, Math.round(ms / 60000))
    return m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`
  }

  return (
    <div className="app">
      {selected && <StockModal stock={selected} onClose={() => setSelected(null)} />}

      {/* Backend offline warning */}
      {backendOff && (
        <div style={{
          background:'linear-gradient(90deg,#7f1d1d,#450a0a)',
          color:'#fca5a5', padding:'10px 20px', fontSize:13,
          display:'flex', alignItems:'center', gap:10,
          borderBottom:'1px solid rgba(239,68,68,0.4)'
        }}>
          <AlertTriangle size={16}/>
          <strong>Backend offline.</strong>&nbsp;Start the FastAPI server first:
          <code style={{background:'rgba(0,0,0,0.4)',borderRadius:4,padding:'2px 8px',fontFamily:'var(--mono)',fontSize:12}}>
            cd backend &amp;&amp; python -m uvicorn api:app --host 0.0.0.0 --port 8000
          </code>
          <span style={{marginLeft:'auto',opacity:0.7}}>or run <code style={{fontFamily:'var(--mono)'}}>start.bat</code></span>
        </div>
      )}

      {/* Header */}
      <header className="header">
        <div className="header-brand">
          <div className="header-logo">📡</div>
          <div className="header-title">Stock<span>Radar</span> IN</div>
          <span className={`badge ${mktTrend.toLowerCase()}`}>{mktTrend}</span>
          {isRunning && <span className="badge running">⚙ Analysing...</span>}
        </div>
        <div className="header-right">
          {nextRunAt && !isRunning && (
            <div className="next-run" title="Next scheduled analysis">
              <Clock size={12}/> next run in {fmtEta(nextRunAt - now)}
            </div>
          )}
          {lastTime && <div className="last-update">Last run: {lastTime} IST</div>}
          <button className="trigger-btn" onClick={triggerRun} disabled={isRunning || backendOff}>
            {isRunning
              ? <><RefreshCw size={14} style={{animation:'spin 1s linear infinite'}}/> Running...</>
              : <><Zap size={14}/> Run Now</>}
          </button>
        </div>
      </header>

      {isRunning && <div className="progress-line" />}

      <main className="main">
        {loading && (
          <>
            <div className="hero">
              {[0, 1, 2].map(i => (
                <div key={i} className="skeleton" style={{ height: 210, animationDelay: `${i * 0.1}s` }} />
              ))}
            </div>
            <div style={{ display:'flex', gap:14, overflow:'hidden' }}>
              {[0, 1, 2, 3, 4].map(i => (
                <div key={i} className="skeleton" style={{ height: 190, minWidth: 190, flex: 1, animationDelay: `${i * 0.08}s` }} />
              ))}
            </div>
            <div className="skeleton" style={{ height: 320 }} />
          </>
        )}

        {!loading && !run && !isRunning && (
          <div className="empty">
            <div className="empty-icon">🚀</div>
            <div className="empty-msg">No analysis yet</div>
            <div className="empty-sub">Click "Run Now" to start the first analysis. Scheduled runs happen at 09:15 &amp; 15:30 IST on weekdays.</div>
          </div>
        )}

        {!loading && !run && isRunning && (
          <div className="empty">
            <div className="empty-icon" style={{animation:'spin 2s linear infinite',display:'inline-block'}}>⚙️</div>
            <div className="empty-msg">Analysis running…</div>
            <div className="empty-sub">
              Screening the market and computing signals.<br/>
              The dashboard will update automatically when done.
            </div>
          </div>
        )}

        {!loading && run && (
          <>
            {/* Hero: market pulse + signal mix + run trend */}
            <div className="hero rise">
              <RegimePanel run={run} />
              <SignalDonut counts={signalCounts} />
              <RunTrend history={history} />
            </div>

            {/* Top picks strip */}
            {topPicks.length > 0 && (
              <div>
                <div className="section-header">
                  <div className="section-title">🏆 Top Picks <span>highest conviction this run</span></div>
                </div>
                <div className="picks-row">
                  {topPicks.map((s, i) => (
                    <PickCard key={s.symbol} stock={s} rank={i + 1} onSelect={setSelected} />
                  ))}
                </div>
              </div>
            )}

            {/* Analytics row */}
            <div className="rise" style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(300px, 1fr))', gap:16, animationDelay:'0.1s'}}>
              <ScoreDistChart results={results} />
              <div className="panel" style={{padding:'16px 18px'}}>
                <div className="panel-eyebrow">Analysis Summary</div>
                {[
                  {k:'Stocks Analysed', v: results.length},
                  {k:'Avg Score',       v: fmt(results.reduce((a,b)=>a+b.score,0)/(results.length||1),1)},
                  {k:'Positive News',   v: results.filter(r=>r.news_sentiment==='POSITIVE').length + ' stocks'},
                  {k:'Negative News',   v: results.filter(r=>r.news_sentiment==='NEGATIVE').length + ' stocks'},
                  {k:'Scheduled at',    v: '09:15 & 15:30 IST'},
                  {k:'Email alerts',    v: run.email_sent ? '✅ Sent' : '❌ Not sent'},
                ].map(({k,v,cls}) => (
                  <div className="kv-row" key={k}>
                    <span className="kv-key">{k}</span>
                    <span className={`kv-val ${cls||''}`}>{v}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Tabs + search */}
            <div style={{display:'flex', alignItems:'center', gap:12, flexWrap:'wrap'}}>
              <div className="tabs">
                {[['signals','📊 Signals'], ['history','🕐 History']].map(([key, label]) => (
                  <button key={key} className={`tab-btn ${tab===key ? 'active' : ''}`} onClick={() => setTab(key)}>
                    {label}
                  </button>
                ))}
              </div>
              {tab === 'signals' && (
                <div className="search-box">
                  <Search size={14} color="var(--text-3)"/>
                  <input
                    placeholder="Filter symbol or company…"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                  />
                  {query && <X size={13} color="var(--text-3)" style={{cursor:'pointer'}} onClick={() => setQuery('')}/>}
                </div>
              )}
            </div>

            {tab === 'signals' && (
              <>
                <StocksTable stocks={results} filter="BUY"   title="BUY Signals"  colorClass="buy"   query={query} onSelect={setSelected} />
                <StocksTable stocks={results} filter="WATCH" title="Watch List"    colorClass="watch" query={query} onSelect={setSelected} />
                <StocksTable stocks={results} filter="SELL"  title="SELL Signals"  colorClass="sell"  query={query} onSelect={setSelected} />
                {query && <StocksTable stocks={results} filter="HOLD" title="HOLD" colorClass="hold" query={query} onSelect={setSelected} />}
              </>
            )}

            {tab === 'history' && <HistoryPanel history={history} />}
          </>
        )}
      </main>

      <footer style={{textAlign:'center', padding:'14px', fontSize:11, color:'var(--text-3)', borderTop:'1px solid var(--border)'}}>
        ⚠️ StockRadar IN — Algorithmic analysis only. NOT SEBI investment advice. Trade at your own risk.
      </footer>

      <style>{`@keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }`}</style>
    </div>
  )
}
