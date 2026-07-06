import { useState, useEffect, useCallback, useSyncExternalStore } from 'react'
import axios from 'axios'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, RadialBarChart, RadialBar, Cell
} from 'recharts'
import {
  TrendingUp, TrendingDown, Eye, Zap, RefreshCw, Clock,
  X, ChevronUp, ChevronDown, AlertTriangle, Search, Trophy
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

// ── SummaryCard ───────────────────────────────────────────────────────────────
function SummaryCard({ type, icon, count, label, delay = 0 }) {
  const n = useCountUp(count)
  return (
    <div className={`summary-card ${type} rise`} style={{ animationDelay: `${delay}s` }}>
      <div className={`summary-icon ${type}`}>{icon}</div>
      <div>
        <div className={`summary-num ${type}`}>{n}</div>
        <div className="summary-label">{label}</div>
      </div>
    </div>
  )
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

// ── NewsIcon ──────────────────────────────────────────────────────────────────
function NewsIcon({ sentiment }) {
  if (sentiment === 'POSITIVE') return <span title="Positive news" style={{color:'var(--green)'}}>📰✅</span>
  if (sentiment === 'NEGATIVE') return <span title="Negative news" style={{color:'var(--red)'}}>📰⚠️</span>
  return <span style={{color:'var(--text-3)'}}>—</span>
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
    { k:'Revenue Growth',  v: fmtPct(stock.revenue_growth) },
    { k:'EPS Growth',      v: fmtPct(stock.eps_growth) },
    { k:'Analyst Rating',  v: stock.analyst_rating != null
        ? [{1:'Strong Buy',2:'Buy',3:'Hold',4:'Sell',5:'Strong Sell'}[Math.round(stock.analyst_rating)] || fmt(stock.analyst_rating,1)]
        : '—',
      cls: stock.analyst_rating <= 2 ? 'pos' : stock.analyst_rating >= 4 ? 'neg' : ''
    },
    { k:'52W High',        v: fmtPrice(stock.high_52w) },
    { k:'52W Low',         v: fmtPrice(stock.low_52w) },
    { k:'% from High',     v: fmtPct(-stock.pct_from_52w_high), cls:'neg' },
    { k:'% from Low',      v: fmtPct(stock.pct_from_52w_low), cls:'pos' },
  ]

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}><X size={14}/> Close</button>

        <div style={{display:'flex', alignItems:'flex-start', justifyContent:'space-between'}}>
          <div>
            <div className="modal-title">{stock.symbol}</div>
            <div className="modal-price">{fmtPrice(stock.price)}</div>
            <div style={{marginTop:8, display:'flex', gap:8, flexWrap:'wrap', alignItems:'center'}}>
              <span className={`signal-pill ${stock.signal}`}>{stock.signal}</span>
              {stock.intraday_change !== 0 && (
                <span style={{fontSize:12, fontFamily:'var(--mono)', color: stock.intraday_change > 0 ? 'var(--green)' : 'var(--red)'}}>
                  Today: {fmtPct(stock.intraday_change)}
                </span>
              )}
              {stock.open_gap !== 0 && (
                <span style={{fontSize:12, color:'var(--text-2)'}}>Gap: {fmtPct(stock.open_gap)}</span>
              )}
              <NewsIcon sentiment={stock.news_sentiment} />
              <span style={{fontSize:12, color:'var(--text-3)'}}>News score: {fmt(newsScore,0)}/100</span>
            </div>
          </div>

          {/* Score Gauge */}
          <div style={{textAlign:'center'}}>
            <ResponsiveContainer width={100} height={80}>
              <RadialBarChart cx="50%" cy="80%" innerRadius="60%" outerRadius="100%"
                startAngle={180} endAngle={0} data={[{ value: stock.score }]}>
                <RadialBar background dataKey="value" fill={scoreColor(stock.score)} cornerRadius={4} />
              </RadialBarChart>
            </ResponsiveContainer>
            <div style={{fontSize:22, fontWeight:800, fontFamily:'var(--mono)', color:scoreColor(stock.score), marginTop:-8}}>{fmt(stock.score,0)}</div>
            <div style={{fontSize:10, color:'var(--text-3)'}}>Score /100</div>
          </div>
        </div>

        <div className="modal-grid">
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
function StocksTable({ stocks, filter, title, colorClass, query }) {
  const [sortKey, setSortKey] = useState('score')
  const [sortDir, setSortDir] = useState(-1)
  const [selected, setSelected] = useState(null)

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
    <>
      {selected && <StockModal stock={selected} onClose={() => setSelected(null)} />}
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
                {col('volume_ratio','Vol', 'Volume relative to 20-day average (e.g., 2.0x)')}
                <th title="Final recommendation signal">Signal</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(s => (
                <tr key={s.symbol}>
                  <td className="symbol-cell" onClick={() => setSelected(s)}>{s.symbol}</td>
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
                  <td className="mono">{fmt(s.volume_ratio,1)}x</td>
                  <td><span className={`signal-pill ${s.signal}`}>{s.signal}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

// ── HistoryPanel ──────────────────────────────────────────────────────────────
function HistoryPanel({ history }) {
  if (!history.length) return (
    <div className="empty"><div className="empty-icon">📋</div><div className="empty-msg">No history yet</div></div>
  )
  return (
    <div>
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

// ── MarketBar ─────────────────────────────────────────────────────────────────
function MarketBar({ run }) {
  if (!run) return null
  const sc = run.sector_changes || {}
  return (
    <div className="market-bar rise">
      <div className="market-tile">
        <div className="market-tile-label">NIFTY 50</div>
        <div className={`market-tile-value ${trendColor(run.nifty_change)}`}>{fmtPct(run.nifty_change)}</div>
        <div className="market-tile-sub">5-day change</div>
      </div>
      <div className="market-tile">
        <div className="market-tile-label">India VIX</div>
        <div className={`market-tile-value ${run.vix_value > 20 ? 'neg' : run.vix_value < 15 ? 'pos' : 'neu'}`}>
          {fmt(run.vix_value,1)}
        </div>
        <div className="market-tile-sub">{run.vix_value > 20 ? 'HIGH' : run.vix_value < 15 ? 'LOW' : 'MEDIUM'} volatility</div>
      </div>
      {Object.entries(sc).map(([name, chg]) => (
        <div className="market-tile" key={name}>
          <div className="market-tile-label">{name}</div>
          <div className={`market-tile-value ${trendColor(chg)}`}>{fmtPct(chg)}</div>
          <div className="market-tile-sub">Today</div>
        </div>
      ))}
    </div>
  )
}

// ── ScoreDistChart ────────────────────────────────────────────────────────────
function ScoreDistChart({ results }) {
  if (!results.length) return null
  const bins = Array.from({length:10}, (_,i) => ({ range:`${i*10}-${i*10+10}`, count:0 }))
  results.forEach(r => { const i = Math.min(Math.floor(r.score/10), 9); bins[i].count++ })
  return (
    <div className="modal-box" style={{padding:'16px 16px 8px'}}>
      <div className="modal-box-title">Score Distribution</div>
      <ResponsiveContainer width="100%" height={120}>
        <BarChart data={bins} margin={{top:0,right:0,left:-20,bottom:0}}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="range" tick={{fontSize:9, fill:'#475569'}} />
          <YAxis tick={{fontSize:9, fill:'#475569'}} />
          <Tooltip
            contentStyle={{background:'#0d0d1a',border:'1px solid rgba(255,255,255,0.08)',borderRadius:8,fontSize:12}}
            labelStyle={{color:'#94a3b8'}}
          />
          <Bar dataKey="count" radius={[3,3,0,0]}>
            {bins.map((entry, idx) => (
              <Cell key={idx} fill={idx >= 7 ? 'var(--green)' : idx >= 5 ? 'var(--blue)' : 'var(--text-3)'} />
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

  const isRunning = status?.analysis_running || triggering
  const mktTrend  = run?.market_trend || 'NEUTRAL'
  const lastTime  = run?.run_time ? new Date(run.run_time).toLocaleString('en-IN') : null

  const topPick = results.length
    ? results.reduce((a, b) => (b.score > a.score ? b : a), results[0])
    : null

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
            <div className="summary-row">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="skeleton" style={{ height: 94, animationDelay: `${i * 0.1}s` }} />
              ))}
            </div>
            <div className="skeleton" style={{ height: 110 }} />
            <div className="skeleton" style={{ height: 340 }} />
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
              Downloading market data and computing signals for all stocks.<br/>
              This takes <strong>3–8 minutes</strong> — the dashboard will update automatically when done.
            </div>
          </div>
        )}

        {!loading && run && (
          <>
            {/* Market Bar */}
            <MarketBar run={run} />

            {/* Summary Cards */}
            <div className="summary-row">
              <SummaryCard type="buy"   icon={<TrendingUp size={24} color="var(--green)"/>}  count={buys.length}    label="BUY Signals" />
              <SummaryCard type="watch" icon={<Eye size={24} color="var(--blue)"/>}          count={watches.length} label="WATCH List"  delay={0.06} />
              <SummaryCard type="sell"  icon={<TrendingDown size={24} color="var(--red)"/>}  count={sells.length}   label="SELL Signals" delay={0.12} />
              {topPick && (
                <div className="summary-card pick rise" style={{ animationDelay: '0.18s' }}>
                  <div className="summary-icon pick"><Trophy size={24} color="var(--purple)"/></div>
                  <div>
                    <div className="summary-num pick" style={{ fontSize: 24 }}>{topPick.symbol}</div>
                    <div className="summary-label">
                      Top Pick · Score {fmt(topPick.score, 0)} · <span className={`signal-pill ${topPick.signal}`} style={{ fontSize: 10, padding: '1px 8px' }}>{topPick.signal}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Score dist chart + quick stats */}
            <div className="rise" style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(300px, 1fr))', gap:16, animationDelay:'0.1s'}}>
              <ScoreDistChart results={results} />
              <div className="modal-box" style={{padding:'16px 18px'}}>
                <div className="modal-box-title">Analysis Summary</div>
                {[
                  {k:'Stocks Analysed', v: results.length},
                  {k:'India VIX',       v: run.vix_value != null ? fmt(run.vix_value,1) : '—'},
                  {k:'NIFTY 5-day',     v: fmtPct(run.nifty_change), cls: trendColor(run.nifty_change)},
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
                <StocksTable stocks={results} filter="BUY"   title="BUY Signals"  colorClass="buy"   query={query} />
                <StocksTable stocks={results} filter="WATCH" title="Watch List"    colorClass="watch" query={query} />
                <StocksTable stocks={results} filter="SELL"  title="SELL Signals"  colorClass="sell"  query={query} />
                {query && <StocksTable stocks={results} filter="HOLD" title="HOLD" colorClass="hold" query={query} />}
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
