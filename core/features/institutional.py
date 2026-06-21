"""
Institutional Activity Features
================================
Computes market-microstructure features driven by institutional flows,
delivery data, and options-chain analytics.

Features
--------
- FII / DII net flows (market-wide, 3-day cumulative)
- Delivery % vs historical average (per-symbol)
- Put/Call ratio, max-pain distance, OI buildup (per-symbol)

All data is fetched directly from NSE APIs via ``core.collectors.nse``.
No Kafka or DuckDB dependency — works standalone.

Usage
-----
>>> from core.features.institutional import compute_institutional_features, compute_institutional_score
>>> feats = compute_institutional_features("RELIANCE")
>>> score = compute_institutional_score(feats)
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

log = logging.getLogger("stockradar.features.institutional")

# ---------------------------------------------------------------------------
# Safe numeric helpers
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert *val* to float, returning *default* on failure or None/NaN."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return default


def _safe_bool(val: Any, default: bool = False) -> bool:
    """Coerce *val* to bool safely."""
    if val is None:
        return default
    try:
        return bool(val)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Internal NSE helpers (lazy session, collector imports)
# ---------------------------------------------------------------------------

def _get_session(nse_session=None):
    """Return an existing NSE requests session or create a fresh one."""
    if nse_session is not None:
        return nse_session
    try:
        from core.collectors.nse import _make_session
        return _make_session()
    except Exception as exc:
        log.warning("Cannot create NSE session: %s", exc)
        return None


def _fetch_fii_dii_safe(session) -> Optional[dict]:
    """Fetch today's FII/DII flow; returns ``None`` on any failure."""
    if session is None:
        return None
    try:
        from core.collectors.nse import fetch_fii_dii
        return fetch_fii_dii(session)
    except Exception as exc:
        log.warning("FII/DII fetch failed: %s", exc)
        return None


def _fetch_delivery_safe(session, symbol: str) -> Optional[dict]:
    """Fetch delivery data for *symbol*; returns ``None`` on any failure."""
    if session is None:
        return None
    try:
        from core.collectors.nse import fetch_delivery
        return fetch_delivery(session, symbol)
    except Exception as exc:
        log.warning("Delivery fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_option_chain_safe(session, symbol: str) -> Optional[dict]:
    """Fetch option-chain summary for *symbol*; returns ``None`` on failure."""
    if session is None:
        return None
    try:
        from core.collectors.nse import fetch_option_chain
        return fetch_option_chain(symbol, session)
    except Exception as exc:
        log.warning("Option-chain fetch failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Historical delivery helpers (DuckDB lake, best-effort)
# ---------------------------------------------------------------------------

def _get_delivery_history(symbol: str, days: int = 5) -> list[float]:
    """
    Try to read the last *days* delivery-pct values from the DuckDB lake.

    Returns a list of floats (may be empty if the lake is unavailable or has
    no data for *symbol*).
    """
    try:
        from core.lake.manager import get_lake
        conn = get_lake()
        rows = conn.execute(
            """
            SELECT delivery_pct
            FROM raw_delivery
            WHERE symbol = ?
              AND delivery_pct IS NOT NULL
            ORDER BY date DESC
            LIMIT ?
            """,
            [symbol, days],
        ).fetchall()
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception as exc:
        log.debug("Lake delivery history unavailable for %s: %s", symbol, exc)
        return []


def _get_fii_dii_history(days: int = 3) -> list[dict]:
    """
    Read the last *days* FII/DII records from the lake.

    Returns list of dicts with ``fii_net`` / ``dii_net`` keys (may be empty).
    """
    try:
        from core.lake.manager import get_lake
        conn = get_lake()
        rows = conn.execute(
            """
            SELECT fii_net, dii_net
            FROM raw_fii_dii
            ORDER BY date DESC
            LIMIT ?
            """,
            [days],
        ).fetchall()
        return [{"fii_net": r[0], "dii_net": r[1]} for r in rows]
    except Exception as exc:
        log.debug("Lake FII/DII history unavailable: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Main feature computation
# ---------------------------------------------------------------------------

def compute_institutional_features(
    symbol: str,
    nse_session=None,
) -> dict:
    """
    Compute institutional-activity features for *symbol*.

    Parameters
    ----------
    symbol : str
        NSE equity symbol (e.g. ``"RELIANCE"``).
    nse_session : requests.Session, optional
        Pre-warmed NSE session.  If ``None``, a new session is created.

    Returns
    -------
    dict
        Feature dictionary with keys:

        * **fii_net_3d** — cumulative FII net flow (₹ Cr) over last 3 days
        * **dii_net_3d** — cumulative DII net flow (₹ Cr) over last 3 days
        * **fii_dii_divergence** — True if FII selling and DII buying
        * **delivery_pct** — today's delivery % for *symbol*
        * **delivery_5d_avg** — 5-day rolling average delivery %
        * **delivery_spike** — True if today's delivery > 2× the 5d avg
        * **pcr** — Put/Call ratio (nearest expiry)
        * **max_pain_distance_pct** — distance of spot from max-pain as %
        * **oi_buildup_bullish** — True if rising OI + rising price
    """

    session = _get_session(nse_session)

    # -- Safe defaults (returned on any failure) ----------------------------
    features: dict[str, Any] = {
        "fii_net_3d":            0.0,
        "dii_net_3d":            0.0,
        "fii_dii_divergence":    False,
        "delivery_pct":          0.0,
        "delivery_5d_avg":       0.0,
        "delivery_spike":        False,
        "pcr":                   None,
        "max_pain_distance_pct": None,
        "oi_buildup_bullish":    False,
    }

    # ── 1. FII / DII 3-day cumulative flow ────────────────────────────────
    try:
        history = _get_fii_dii_history(days=3)

        # If the lake has fewer than 3 days, supplement with today's API hit
        if len(history) < 3:
            today = _fetch_fii_dii_safe(session)
            if today:
                # Avoid double-counting: only prepend if today isn't already
                # in the lake result set (simple heuristic — compare fii_net)
                if not history or history[0].get("fii_net") != today.get("fii_net"):
                    history.insert(0, {
                        "fii_net": today.get("fii_net"),
                        "dii_net": today.get("dii_net"),
                    })

        fii_vals = [_safe_float(h.get("fii_net")) for h in history]
        dii_vals = [_safe_float(h.get("dii_net")) for h in history]

        features["fii_net_3d"] = round(sum(fii_vals), 2)
        features["dii_net_3d"] = round(sum(dii_vals), 2)

        # Divergence: FII selling (net < 0) while DII buying (net > 0)
        if fii_vals and dii_vals:
            features["fii_dii_divergence"] = (
                features["fii_net_3d"] < 0 and features["dii_net_3d"] > 0
            )
    except Exception as exc:
        log.warning("FII/DII feature computation failed: %s", exc)

    # ── 2. Delivery % ─────────────────────────────────────────────────────
    try:
        delivery_data = _fetch_delivery_safe(session, symbol)
        today_del_pct = _safe_float(
            delivery_data.get("delivery_pct") if delivery_data else None
        )
        features["delivery_pct"] = round(today_del_pct, 2)

        # 5-day history from lake
        hist_pcts = _get_delivery_history(symbol, days=5)

        # If the lake returned results, use them; otherwise just use today
        if hist_pcts:
            # Prepend today if not already present
            if today_del_pct > 0 and (
                not hist_pcts or abs(hist_pcts[0] - today_del_pct) > 0.01
            ):
                hist_pcts.insert(0, today_del_pct)
            avg_5d = sum(hist_pcts[:5]) / len(hist_pcts[:5])
        elif today_del_pct > 0:
            avg_5d = today_del_pct  # only have today's data
        else:
            avg_5d = 0.0

        features["delivery_5d_avg"] = round(avg_5d, 2)

        # Spike detection: today > 2× 5d average
        if avg_5d > 0 and today_del_pct > 0:
            features["delivery_spike"] = today_del_pct > (2.0 * avg_5d)
    except Exception as exc:
        log.warning("Delivery feature computation failed for %s: %s", symbol, exc)

    # ── 3. Option chain: PCR, max-pain, OI buildup ────────────────────────
    try:
        oc = _fetch_option_chain_safe(session, symbol)
        if oc:
            pcr_val = oc.get("pcr")
            features["pcr"] = round(float(pcr_val), 3) if pcr_val is not None else None

            spot = _safe_float(oc.get("spot_price"))
            max_pain = _safe_float(oc.get("max_pain_strike"))

            if spot > 0 and max_pain > 0:
                distance_pct = abs(spot - max_pain) / spot * 100
                features["max_pain_distance_pct"] = round(distance_pct, 2)

            # OI buildup bullish heuristic:
            #   total PE OI > total CE OI (PCR > 1) is a bullish proxy
            #   combined with spot being above max-pain → OI buildup bullish
            total_pe = _safe_float(oc.get("total_pe_oi"))
            total_ce = _safe_float(oc.get("total_ce_oi"))
            if total_ce > 0 and spot > 0 and max_pain > 0:
                features["oi_buildup_bullish"] = (
                    total_pe > total_ce and spot >= max_pain
                )
    except Exception as exc:
        log.warning("Options feature computation failed for %s: %s", symbol, exc)

    log.info(
        "Institutional features for %s: FII_3d=%.0f DII_3d=%.0f del=%.1f%% PCR=%s",
        symbol,
        features["fii_net_3d"],
        features["dii_net_3d"],
        features["delivery_pct"],
        features["pcr"],
    )
    return features


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_institutional_score(features: dict) -> float:
    """
    Convert institutional feature dict into a 0–100 score.

    Scoring rubric
    ~~~~~~~~~~~~~~
    +----------------------------------+--------+
    | Condition                        | Points |
    +==================================+========+
    | FII net 3d > 500 Cr             |  +12   |
    | FII net 3d > 0                  |   +6   |
    | FII net 3d < -500 Cr            |  -12   |
    | FII net 3d < 0                  |   -6   |
    | DII net 3d > 500 Cr             |   +6   |
    | FII/DII divergence              |   +8   |
    | Delivery % > 60                 |   +8   |
    | Delivery % > 50                 |   +4   |
    | Delivery % < 30                 |   -4   |
    | Delivery spike                  |  +10   |
    | PCR > 1.2                       |   +6   |
    | PCR < 0.7                       |   -6   |
    | Max-pain distance < 2%          |   +4   |
    | OI buildup bullish              |   +8   |
    +----------------------------------+--------+

    Base score is **50**.  Final value is clamped to ``[0, 100]``.

    Parameters
    ----------
    features : dict
        Output of :func:`compute_institutional_features`.

    Returns
    -------
    float
        Score in the range [0, 100].
    """

    score = 50.0

    # ── FII net flow ──────────────────────────────────────────────────────
    fii_3d = _safe_float(features.get("fii_net_3d"))
    if fii_3d > 500:
        score += 12
    elif fii_3d > 0:
        score += 6
    elif fii_3d < -500:
        score -= 12
    elif fii_3d < 0:
        score -= 6

    # ── DII domestic support ──────────────────────────────────────────────
    dii_3d = _safe_float(features.get("dii_net_3d"))
    if dii_3d > 500:
        score += 6

    # ── FII/DII divergence ────────────────────────────────────────────────
    if _safe_bool(features.get("fii_dii_divergence")):
        score += 8

    # ── Delivery % ────────────────────────────────────────────────────────
    delivery = _safe_float(features.get("delivery_pct"))
    if delivery > 60:
        score += 8
    elif delivery > 50:
        score += 4
    elif delivery < 30 and delivery > 0:
        # Only penalise if we actually have a reading (>0)
        score -= 4

    # ── Delivery spike ────────────────────────────────────────────────────
    if _safe_bool(features.get("delivery_spike")):
        score += 10

    # ── PCR ───────────────────────────────────────────────────────────────
    pcr = features.get("pcr")
    if pcr is not None:
        pcr_f = _safe_float(pcr, default=-1.0)
        if pcr_f > 0:
            if pcr_f > 1.2:
                score += 6
            elif pcr_f < 0.7:
                score -= 6

    # ── Max-pain distance ─────────────────────────────────────────────────
    mp_dist = features.get("max_pain_distance_pct")
    if mp_dist is not None:
        if _safe_float(mp_dist) < 2.0:
            score += 4

    # ── OI buildup ────────────────────────────────────────────────────────
    if _safe_bool(features.get("oi_buildup_bullish")):
        score += 8

    # ── Clamp to [0, 100] ─────────────────────────────────────────────────
    score = max(0.0, min(100.0, score))
    return round(score, 2)


# ---------------------------------------------------------------------------
# Convenience: one-call entry point
# ---------------------------------------------------------------------------

def institutional_analysis(symbol: str, nse_session=None) -> dict:
    """
    High-level helper that computes features *and* score in one call.

    Returns
    -------
    dict
        ``{"features": {...}, "score": float}``
    """
    feats = compute_institutional_features(symbol, nse_session=nse_session)
    score = compute_institutional_score(feats)
    return {"features": feats, "score": score}


# ---------------------------------------------------------------------------
# CLI quick-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-36s  %(levelname)-7s  %(message)s",
    )

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    result = institutional_analysis(sym)

    print(f"\n{'─' * 52}")
    print(f"  Institutional Analysis — {sym}")
    print(f"{'─' * 52}")
    for k, v in result["features"].items():
        print(f"  {k:28s} : {v}")
    print(f"{'─' * 52}")
    print(f"  SCORE : {result['score']:.1f} / 100")
    print(f"{'─' * 52}\n")
