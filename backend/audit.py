"""
StockRadar IN — Audit trail (Phase 4, SR-9)
===========================================
Who did what, from where. Deliberately dependency-free and import-light so
both auth.py and security.py can use it without an import cycle.

Events go to two places:
  • the normal application log (so they show up in `docker logs` / the console)
  • backend/data/audit.log, rotated, so the trail survives a restart

A line looks like:
    2026-10-24 15:04:11 AUDIT login.ok identity=me@example.com ip=127.0.0.1 provider=google

Audit lines are deliberately boring and greppable: `action=` is always the
first field after AUDIT, identity and ip are always present (as "-" when
unknown), and everything else is appended as key=value.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

_AUDIT_DIR = Path(__file__).parent / "data"
_AUDIT_FILE = _AUDIT_DIR / "audit.log"
_MAX_BYTES = int(os.environ.get("AUDIT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
_BACKUPS = int(os.environ.get("AUDIT_LOG_BACKUPS", "5"))

log = logging.getLogger("stockradar.audit")

# Don't let the root handler double-print these — we attach our own.
log.propagate = True

_configured = False


def _configure() -> None:
    """Attach the rotating file handler once, lazily."""
    global _configured
    if _configured:
        return
    _configured = True
    try:
        _AUDIT_DIR.mkdir(exist_ok=True)
        handler = RotatingFileHandler(
            _AUDIT_FILE, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(handler)
    except Exception as exc:  # pragma: no cover — never let auditing break a request
        logging.getLogger("stockradar").warning(
            "Audit file handler unavailable (%s) — auditing to console only", exc
        )


def client_ip(request: Any, trust_proxy: bool = False) -> str:
    """Best-effort client IP.

    X-Forwarded-For is attacker-controlled unless a trusted reverse proxy sets
    it, so it is only consulted when *trust_proxy* is on (TRUST_PROXY_HEADERS).
    Behind the NAS nginx that is correct; exposed directly it would let anyone
    forge their way past a per-IP rate limit.
    """
    try:
        if trust_proxy:
            fwd = request.headers.get("x-forwarded-for", "")
            if fwd:
                # left-most entry is the original client
                return fwd.split(",")[0].strip() or "-"
        return (request.client.host if request.client else None) or "-"
    except Exception:
        return "-"


def audit(action: str, *, identity: Optional[str] = None, ip: Optional[str] = None, **fields: Any) -> None:
    """Record one audit event. Never raises."""
    try:
        _configure()
        parts = [f"AUDIT {action}", f"identity={identity or '-'}", f"ip={ip or '-'}"]
        parts += [f"{k}={v}" for k, v in fields.items() if v is not None]
        log.info(" ".join(parts))
    except Exception:  # pragma: no cover
        pass
