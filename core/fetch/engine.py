"""
Fetch Engine — central control plane for every outbound HTTP call
==================================================================
One flat thread pool cannot pace three very different upstreams (Yahoo,
NSE, news RSS) at once: the slowest host starves the fastest, and a single
hung socket can stall a whole pipeline run.  This engine gives each host
its own independent policy:

  * **Concurrency gate** — a semaphore caps in-flight requests per host,
    so raising the pipeline worker count never turns into a hammer on a
    throttle-happy upstream.
  * **Pacing** — a minimum interval between request *starts* per host
    (token-bucket style), replacing ad-hoc ``time.sleep`` calls scattered
    through the collectors.
  * **Timeouts everywhere** — every attempt gets the policy timeout; no
    call may block forever.
  * **Retries** — exponential backoff with ±25% jitter.
  * **Circuit breaker** — after N consecutive failures the host is marked
    down and calls fail fast for a cooldown window instead of burning
    worker time on a dead upstream.
  * **Metrics** — per-host counters (calls / ok / errors / retries /
    breaker trips / avg latency) served by ``GET /api/fetch-stats``.

Usage
-----
    from core.fetch import get_engine

    engine = get_engine()
    resp = engine.get("google_news", url)              # raw HTTP GET
    info = engine.call("yahoo", lambda: ticker.info)   # gate a library call

Per-host defaults live in ``_DEFAULT_POLICIES`` and every knob can be
overridden from the environment, e.g. ``FETCH_YAHOO_CONCURRENCY=16`` or
``FETCH_NSE_MIN_INTERVAL_MS=200``.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from dataclasses import dataclass, replace

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger("stockradar.fetch")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class CircuitOpen(RuntimeError):
    """Raised when a host's circuit breaker is open — callers fail fast."""


@dataclass(frozen=True)
class HostPolicy:
    name: str
    max_concurrent: int = 8       # in-flight request cap for this host
    min_interval: float = 0.0     # seconds between request starts
    timeout: float = 12.0         # per-attempt timeout (HTTP + semaphore wait)
    retries: int = 1              # extra attempts after the first failure
    backoff_base: float = 1.0     # sleep before first retry; doubles per retry
    breaker_threshold: int = 10   # consecutive failures that open the breaker
    breaker_cooldown: float = 60.0
    queue_timeout: float | None = None  # max wait for a slot (None → 2×timeout, min 30s)


_DEFAULT_POLICIES: dict[str, HostPolicy] = {
    # yfinance metadata calls: .info / .news / fast_info / financials
    "yahoo":        HostPolicy("yahoo", max_concurrent=12, min_interval=0.05,
                               timeout=15, retries=1, breaker_threshold=20),
    # yf.download bulk chunks — few, heavy calls; Yahoo throttles by volume
    "yahoo_bulk":   HostPolicy("yahoo_bulk", max_concurrent=2, min_interval=0.5,
                               timeout=120, retries=1, backoff_base=5.0,
                               breaker_threshold=6, breaker_cooldown=120),
    # NSE JSON API (cookie session, aggressive anti-bot).  Tier-2 floods this
    # host with ~400 option-chain calls at once: 6 slots + 100ms pacing gives
    # ~10 req/s, and the 120s queue_timeout lets workers wait politely instead
    # of being counted as rejected.  Threshold 15 tolerates a cookie-expiry
    # burst while the re-warm kicks in.
    "nse":          HostPolicy("nse", max_concurrent=6, min_interval=0.10,
                               timeout=12, retries=2, breaker_threshold=15,
                               breaker_cooldown=60, queue_timeout=120),
    # NSE static archives (bhavcopy, EQUITY_L.csv, fo_mktlots.csv)
    "nse_archives": HostPolicy("nse_archives", max_concurrent=2, timeout=20,
                               retries=2, breaker_threshold=6),
    "google_news":  HostPolicy("google_news", max_concurrent=12, min_interval=0.05,
                               timeout=8, retries=1, breaker_threshold=15),
    "et_rss":       HostPolicy("et_rss", max_concurrent=2, timeout=8, retries=1),
    "screener":     HostPolicy("screener", max_concurrent=2, min_interval=1.0,
                               timeout=15, retries=1),
}


def _env_override(policy: HostPolicy) -> HostPolicy:
    """Apply FETCH_<HOST>_* environment overrides to a policy."""
    prefix = f"FETCH_{policy.name.upper()}_"
    changes: dict = {}
    conc = os.getenv(prefix + "CONCURRENCY")
    if conc and conc.isdigit() and int(conc) > 0:
        changes["max_concurrent"] = int(conc)
    interval_ms = os.getenv(prefix + "MIN_INTERVAL_MS")
    if interval_ms and interval_ms.isdigit():
        changes["min_interval"] = int(interval_ms) / 1000.0
    timeout = os.getenv(prefix + "TIMEOUT")
    if timeout:
        try:
            changes["timeout"] = float(timeout)
        except ValueError:
            pass
    retries = os.getenv(prefix + "RETRIES")
    if retries and retries.isdigit():
        changes["retries"] = int(retries)
    return replace(policy, **changes) if changes else policy


class _HostState:
    __slots__ = (
        "policy", "semaphore", "lock", "next_slot",
        "consecutive_failures", "breaker_open_until",
        "calls", "ok", "errors", "retries", "breaker_trips",
        "rejected", "total_time",
    )

    def __init__(self, policy: HostPolicy):
        self.policy = policy
        self.semaphore = threading.BoundedSemaphore(policy.max_concurrent)
        self.lock = threading.Lock()
        self.next_slot = 0.0
        self.consecutive_failures = 0
        self.breaker_open_until = 0.0
        self.calls = 0
        self.ok = 0
        self.errors = 0
        self.retries = 0
        self.breaker_trips = 0
        self.rejected = 0
        self.total_time = 0.0


class FetchEngine:
    """Thread-safe singleton coordinating all outbound fetches."""

    def __init__(self) -> None:
        self._hosts: dict[str, _HostState] = {}
        self._registry_lock = threading.Lock()
        self._session: requests.Session | None = None
        self._session_lock = threading.Lock()

    # ── host state ────────────────────────────────────────────────────────────
    def _state(self, host: str) -> _HostState:
        state = self._hosts.get(host)
        if state is not None:
            return state
        with self._registry_lock:
            state = self._hosts.get(host)
            if state is None:
                policy = _env_override(_DEFAULT_POLICIES.get(host, HostPolicy(host)))
                state = _HostState(policy)
                self._hosts[host] = state
        return state

    # ── shared pooled session for direct HTTP ─────────────────────────────────
    @property
    def session(self) -> requests.Session:
        if self._session is None:
            with self._session_lock:
                if self._session is None:
                    s = requests.Session()
                    s.headers.update({"User-Agent": _UA})
                    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64)
                    s.mount("https://", adapter)
                    s.mount("http://", adapter)
                    self._session = s
        return self._session

    # ── breaker helpers ───────────────────────────────────────────────────────
    def _check_breaker(self, state: _HostState) -> None:
        if state.breaker_open_until and time.monotonic() < state.breaker_open_until:
            with state.lock:
                state.rejected += 1
            raise CircuitOpen(
                f"{state.policy.name}: circuit open for another "
                f"{state.breaker_open_until - time.monotonic():.0f}s"
            )

    def _record(self, state: _HostState, success: bool, elapsed: float) -> None:
        with state.lock:
            state.calls += 1
            state.total_time += elapsed
            if success:
                state.ok += 1
                state.consecutive_failures = 0
                state.breaker_open_until = 0.0
            else:
                state.errors += 1
                state.consecutive_failures += 1
                if state.consecutive_failures >= state.policy.breaker_threshold:
                    # Failures from calls already in flight when the breaker
                    # opened just extend the window quietly — one trip, one log.
                    already_open = time.monotonic() < state.breaker_open_until
                    state.breaker_open_until = (
                        time.monotonic() + state.policy.breaker_cooldown
                    )
                    state.consecutive_failures = 0
                    if not already_open:
                        state.breaker_trips += 1
                        log.warning(
                            "Fetch breaker OPEN for host '%s' (%ds cooldown)",
                            state.policy.name, int(state.policy.breaker_cooldown),
                        )

    def _pace(self, state: _HostState) -> None:
        if state.policy.min_interval <= 0:
            return
        with state.lock:
            now = time.monotonic()
            wait = state.next_slot - now
            state.next_slot = max(now, state.next_slot) + state.policy.min_interval
        if wait > 0:
            time.sleep(wait)

    # ── public API ────────────────────────────────────────────────────────────
    def call(self, host: str, fn, *args, retries: int | None = None, **kwargs):
        """
        Run *fn* under *host*'s policy: breaker check, concurrency gate,
        pacing, retries with jittered backoff, metrics.  The callable is
        responsible for its own network timeout (all our callers use
        libraries that enforce one); the engine's semaphore wait is bounded
        so a saturated host cannot block a worker indefinitely.
        """
        state = self._state(host)
        policy = state.policy
        attempts = (policy.retries if retries is None else retries) + 1
        self._check_breaker(state)

        queue_timeout = policy.queue_timeout or max(policy.timeout * 2, 30)
        if not state.semaphore.acquire(timeout=queue_timeout):
            with state.lock:
                state.rejected += 1
            raise TimeoutError(f"{host}: no free slot after {queue_timeout:.0f}s")
        try:
            last_exc: Exception | None = None
            for attempt in range(attempts):
                if attempt:
                    # The breaker may have opened while this call was mid-retry;
                    # stop burning attempts on a host that's already marked down.
                    try:
                        self._check_breaker(state)
                    except CircuitOpen:
                        raise last_exc  # type: ignore[misc]
                    with state.lock:
                        state.retries += 1
                    backoff = policy.backoff_base * (2 ** (attempt - 1))
                    time.sleep(backoff * random.uniform(0.75, 1.25))
                self._pace(state)
                start = time.monotonic()
                try:
                    result = fn(*args, **kwargs)
                    self._record(state, True, time.monotonic() - start)
                    return result
                except Exception as exc:
                    self._record(state, False, time.monotonic() - start)
                    last_exc = exc
                    log.debug("Fetch %s attempt %d/%d failed: %s",
                              host, attempt + 1, attempts, exc)
            raise last_exc  # type: ignore[misc]
        finally:
            state.semaphore.release()

    def get(
        self,
        host: str,
        url: str,
        *,
        session: requests.Session | None = None,
        headers: dict | None = None,
        params: dict | None = None,
        retries: int | None = None,
    ) -> requests.Response:
        """HTTP GET through the engine. Raises on non-2xx/3xx status."""
        state = self._state(host)
        sess = session or self.session

        def _do_get() -> requests.Response:
            resp = sess.get(
                url, params=params, headers=headers, timeout=state.policy.timeout
            )
            if resp.status_code >= 400:
                raise requests.HTTPError(
                    f"{resp.status_code} for {url}", response=resp
                )
            return resp

        return self.call(host, _do_get, retries=retries)

    # ── observability ─────────────────────────────────────────────────────────
    def stats(self) -> dict:
        out: dict[str, dict] = {}
        for name, state in sorted(self._hosts.items()):
            with state.lock:
                calls = state.calls
                out[name] = {
                    "calls": calls,
                    "ok": state.ok,
                    "errors": state.errors,
                    "retries": state.retries,
                    "rejected": state.rejected,
                    "breaker_trips": state.breaker_trips,
                    "breaker_open": time.monotonic() < state.breaker_open_until,
                    "avg_ms": round(state.total_time / calls * 1000, 1) if calls else 0.0,
                    "max_concurrent": state.policy.max_concurrent,
                }
        return out

    def log_summary(self, prefix: str = "Fetch engine") -> None:
        parts = []
        for name, s in self.stats().items():
            if s["calls"] or s["rejected"]:
                parts.append(
                    f"{name}: {s['calls']} calls ({s['errors']} err, "
                    f"{s['retries']} retry, avg {s['avg_ms']}ms)"
                )
        if parts:
            log.info("%s — %s", prefix, " | ".join(parts))


_engine: FetchEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> FetchEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FetchEngine()
    return _engine
