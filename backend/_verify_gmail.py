"""Verify the Gmail app password in .env actually authenticates.

    python backend/_verify_gmail.py

Reads GMAIL_SENDER / GMAIL_APP_PASSWORD from the project-root .env, opens an
authenticated SMTP session and closes it. Sends no mail.

The password is NEVER printed — only a short fingerprint, so you can confirm
both worktrees carry the same value without the secret appearing on screen or
in a transcript.
"""
import hashlib
import smtplib
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOTS = [
    Path(__file__).resolve().parent.parent,                       # this worktree
    Path(__file__).resolve().parent.parent.parent / "Stock_Analyzer-master",
]

# Fingerprint of the app password that was hardcoded at Analyzer.py:59 and is
# still reachable in 13 public commits. Stored as a hash, deliberately — pasting
# the literal here would publish it a fourteenth time.
LEAKED_FP = "b5096ccc"


def fp(s: str) -> str:
    return hashlib.sha256(s.strip().encode()).hexdigest()[:8] if s else "-"


print("-- .env files --")
seen = {}
for root in ROOTS:
    env_path = root / ".env"
    if not env_path.exists():
        print(f"  {root.name:26s} no .env")
        continue
    env = dotenv_values(env_path)
    pw = (env.get("GMAIL_APP_PASSWORD") or "").strip()
    seen[root.name] = fp(pw)
    leaked = fp(pw) == LEAKED_FP
    print(f"  {root.name:26s} sender={env.get('GMAIL_SENDER','-')} fingerprint={fp(pw)}"
          + ("   <<< THIS IS THE LEAKED PASSWORD" if leaked else ""))

if len(set(seen.values())) > 1:
    print("\n  !! worktrees are OUT OF SYNC — update both .env files to match")
elif len(seen) > 1:
    print("\n  both worktrees in sync")

print("\n-- SMTP auth check (no mail sent) --")
env = dotenv_values(ROOTS[0] / ".env")
sender = (env.get("GMAIL_SENDER") or "").strip()
pw = (env.get("GMAIL_APP_PASSWORD") or "").strip()
if not sender or not pw:
    print("  GMAIL_SENDER / GMAIL_APP_PASSWORD missing from .env")
    sys.exit(1)

try:
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls()
        s.login(sender, pw)
    print(f"  OK — {sender} authenticated successfully")
except smtplib.SMTPAuthenticationError:
    print("  FAILED — Gmail rejected these credentials.")
    print("  If you just revoked the old app password, create a new one and")
    print("  paste it into BOTH .env files, then re-run this.")
    sys.exit(1)
except Exception as exc:
    print(f"  INCONCLUSIVE — could not complete the check: {type(exc).__name__}: {exc}")
    sys.exit(2)
