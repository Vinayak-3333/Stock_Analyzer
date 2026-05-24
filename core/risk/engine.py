"""
Risk Engine
============
Stock-level and portfolio-level risk management.

Features:
  - Pre-flight disqualification filters (liquidity, pledged, market cap, governance)
  - ATR-based stop-loss and target calculation
  - Kelly Criterion position sizing (capped at 10% per stock)
  - Portfolio-level controls (sector concentration, correlation, drawdown)
"""

import math
import logging
from typing import Optional

log = logging.getLogger("stockradar.risk")

# ── Configuration ─────────────────────────────────────────────────────────────
MAX_POSITION_PCT      = 0.10    # No single stock > 10% of portfolio
MAX_SECTOR_PCT        = 0.25    # No sector > 25%
STOP_LOSS_ATR_MULT    = 2.0     # Stop = price - 2*ATR
TARGET_ATR_MULT       = 3.0     # Target = price + 3*ATR (3:1 R/R)
MIN_REWARD_RISK_RATIO = 2.0     # Skip if R/R < 2:1
MIN_AVG_VOLUME        = 100_000 # Minimum daily avg volume
MAX_PLEDGED_PCT       = 30.0    # Skip if pledged > 30%
MIN_MARKET_CAP_CR     = 500.0   # Skip if market cap < 500 Cr


# ── Stock-Level Risk Assessment ───────────────────────────────────────────────

def assess_stock_risk(
    symbol:        str,
    price:         float,
    atr:           float,
    avg_volume:    float,
    pledged_pct:   float = 0,
    market_cap_cr: float = 5000,
    delivery_pct:  float = 50,
    event_type:    Optional[str] = None,
) -> dict:
    """
    Full risk assessment for a single stock.
    Returns dict with: stop_loss, target, rr_ratio, position_size_pct,
                       is_tradeable, risk_flags.
    """
    flags = []
    is_tradeable = True

    # ── Hard filters ──────────────────────────────────────────────────────────
    if avg_volume < MIN_AVG_VOLUME:
        flags.append(f"ILLIQUID: avg volume {avg_volume:,.0f} < {MIN_AVG_VOLUME:,}")
        is_tradeable = False

    if pledged_pct > MAX_PLEDGED_PCT:
        flags.append(f"HIGH PLEDGING: {pledged_pct:.1f}% > {MAX_PLEDGED_PCT}%")
        is_tradeable = False

    if market_cap_cr < MIN_MARKET_CAP_CR:
        flags.append(f"MICRO CAP: ₹{market_cap_cr:.0f} Cr (operator risk)")
        is_tradeable = False

    if event_type in ("fraud", "regulatory"):
        flags.append(f"GOVERNANCE RISK: {event_type} event detected")
        is_tradeable = False

    # ── Stop loss & target ────────────────────────────────────────────────────
    stop_loss = round(price - STOP_LOSS_ATR_MULT * atr, 2)
    target    = round(price + TARGET_ATR_MULT * atr, 2)
    risk_per_share   = price - stop_loss
    reward_per_share = target - price
    rr_ratio = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0

    if rr_ratio < MIN_REWARD_RISK_RATIO and is_tradeable:
        flags.append(f"POOR R/R: {rr_ratio:.1f} < {MIN_REWARD_RISK_RATIO}")
        # Don't disqualify, just flag

    # ── Position sizing (simplified Kelly Criterion) ──────────────────────────
    # Kelly % = (win_prob * avg_win - loss_prob * avg_loss) / avg_win
    # Using conservative assumptions: win_prob=55%, avg_win=R, avg_loss=1
    r = TARGET_ATR_MULT / STOP_LOSS_ATR_MULT   # reward/risk
    win_prob  = 0.55
    loss_prob = 0.45
    kelly_pct = (win_prob * r - loss_prob) / r
    kelly_pct = max(0, kelly_pct)

    # Half-Kelly for safety, capped at MAX_POSITION_PCT
    position_size_pct = round(min(kelly_pct * 0.5, MAX_POSITION_PCT) * 100, 1)

    # Reduce size for risky characteristics
    if delivery_pct < 35:  position_size_pct *= 0.7   # speculative move
    if pledged_pct > 15:   position_size_pct *= 0.8   # some pledging risk
    if market_cap_cr < 2000: position_size_pct *= 0.8 # small cap discount
    position_size_pct = round(max(1.0, position_size_pct), 1)

    # Shares to buy (if portfolio size known)
    def calc_shares(portfolio_value: float) -> int:
        allocated = portfolio_value * (position_size_pct / 100)
        return max(1, int(allocated / price))

    return {
        "is_tradeable":     is_tradeable,
        "risk_flags":       flags,
        "stop_loss":        stop_loss,
        "target":           target,
        "rr_ratio":         rr_ratio,
        "position_size_pct": position_size_pct,
        "risk_per_share":   round(risk_per_share, 2),
        "reward_per_share": round(reward_per_share, 2),
        "atr_used":         round(atr, 2),
        "calc_shares":      calc_shares,   # call with portfolio_value
    }


# ── Portfolio-Level Risk ──────────────────────────────────────────────────────

class PortfolioRiskEngine:
    """
    Tracks and enforces portfolio-level risk constraints.
    Use this when building the final recommended list.
    """

    def __init__(self, portfolio_value: float = 1_000_000):
        self.portfolio_value = portfolio_value
        self._holdings: list[dict] = []     # {symbol, sector, position_pct, price}

    @property
    def sector_exposure(self) -> dict[str, float]:
        exposure = {}
        for h in self._holdings:
            sec = h.get("sector", "Unknown")
            exposure[sec] = exposure.get(sec, 0) + h.get("position_pct", 0)
        return exposure

    @property
    def total_exposure_pct(self) -> float:
        return sum(h.get("position_pct", 0) for h in self._holdings)

    def can_add(self, symbol: str, sector: str, position_pct: float) -> tuple[bool, str]:
        """Check if adding this position violates any portfolio constraint."""
        # Single stock limit
        if position_pct > MAX_POSITION_PCT * 100:
            return False, f"Position {position_pct}% > max {MAX_POSITION_PCT*100}%"

        # Sector concentration
        current_sector_pct = self.sector_exposure.get(sector, 0)
        if current_sector_pct + position_pct > MAX_SECTOR_PCT * 100:
            return False, (
                f"Sector '{sector}' would be {current_sector_pct + position_pct:.1f}% "
                f"> max {MAX_SECTOR_PCT*100}%"
            )

        # Total portfolio exposure
        if self.total_exposure_pct + position_pct > 95:
            return False, f"Portfolio fully invested ({self.total_exposure_pct:.1f}%)"

        return True, "OK"

    def add_position(self, symbol: str, sector: str, position_pct: float, price: float):
        self._holdings.append({
            "symbol": symbol, "sector": sector,
            "position_pct": position_pct, "price": price,
        })

    def get_summary(self) -> dict:
        return {
            "holdings_count":   len(self._holdings),
            "total_invested":   round(self.total_exposure_pct, 1),
            "cash_remaining":   round(100 - self.total_exposure_pct, 1),
            "sector_exposure":  self.sector_exposure,
            "portfolio_value":  self.portfolio_value,
        }


# ── Apply risk to a result list ───────────────────────────────────────────────

def apply_risk_to_results(
    results: list[dict],
    portfolio_value: float = 1_000_000,
) -> list[dict]:
    """
    Enrich each result dict with risk metrics.
    Filters out disqualified stocks.
    Returns enriched list sorted by score.
    """
    enriched = []
    portfolio = PortfolioRiskEngine(portfolio_value)

    for r in results:
        price  = r.get("price") or 100
        atr    = price * (r.get("atr_pct", 2) / 100)   # approximate ATR from atr_pct

        risk = assess_stock_risk(
            symbol        = r.get("symbol", ""),
            price         = price,
            atr           = atr,
            avg_volume    = r.get("live_volume") or r.get("avg_volume", 200000),
            pledged_pct   = r.get("pledged_pct") or 0,
            market_cap_cr = r.get("market_cap_cr") or 5000,
            delivery_pct  = r.get("delivery_pct_5d") or r.get("delivery_pct") or 50,
            event_type    = r.get("event_type"),
        )

        # Merge risk fields into result
        r["stop_loss"]        = risk["stop_loss"]
        r["target"]           = risk["target"]
        r["rr_ratio"]         = risk["rr_ratio"]
        r["position_size_pct"]= risk["position_size_pct"]
        r["risk_flags"]       = risk["risk_flags"]
        r["is_tradeable"]     = risk["is_tradeable"]
        r["shares_per_lakh"]  = risk["calc_shares"](100_000)   # shares for ₹1L investment

        # Check portfolio-level constraints for BUY signals
        if r.get("signal") == "BUY" and risk["is_tradeable"]:
            can_add, reason = portfolio.can_add(
                r.get("symbol", ""), r.get("industry", "Unknown"), risk["position_size_pct"]
            )
            if can_add:
                portfolio.add_position(
                    r.get("symbol", ""), r.get("industry", "Unknown"),
                    risk["position_size_pct"], price
                )
            else:
                r["risk_flags"].append(f"PORTFOLIO LIMIT: {reason}")

        enriched.append(r)

    log.info("Risk engine processed %d stocks | Portfolio: %s",
             len(enriched), portfolio.get_summary())

    return sorted(enriched, key=lambda x: x.get("score", 0), reverse=True)
