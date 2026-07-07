"""Central outbound-HTTP fetch engine (per-host gates, pacing, retries, breakers)."""

from core.fetch.engine import CircuitOpen, FetchEngine, HostPolicy, get_engine

__all__ = ["CircuitOpen", "FetchEngine", "HostPolicy", "get_engine"]
