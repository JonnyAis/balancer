"""
Append-only trade journal. Writes one JSON line per event to ~/.balancer/trades.jsonl
"""

import json
import os
from datetime import datetime, timezone

_JOURNAL_DIR = os.path.join(os.path.expanduser("~"), ".balancer")
_JOURNAL_PATH = os.path.join(_JOURNAL_DIR, "trades.jsonl")


def log_event(event_type: str, data: dict):
    """
    Append a single event to the trade journal.

    event_type: one of "preview", "place", "cycle_start", "cycle_end", "error"
    data: dict of event-specific fields
    """
    os.makedirs(_JOURNAL_DIR, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **data,
    }
    with open(_JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def log_preview(account: str, symbol: str, action: str, quantity: int, result: dict):
    """Log a preview order attempt."""
    log_event("preview", {
        "account": account,
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "ok": result.get("ok", False),
        "preview_id": result.get("preview_id"),
        "status": result.get("status"),
    })


def log_place(account: str, symbol: str, action: str, quantity: int, result: dict):
    """Log a placed order."""
    log_event("place", {
        "account": account,
        "symbol": symbol,
        "action": action,
        "quantity": quantity,
        "ok": result.get("ok", False),
        "status": result.get("status"),
    })


def log_cycle(phase: str, mode: str, sandbox: bool, accounts: int = 0, trades: int = 0, turnover: float = 0.0):
    """Log cycle start/end."""
    log_event(f"cycle_{phase}", {
        "mode": mode,
        "sandbox": sandbox,
        "accounts": accounts,
        "trades": trades,
        "turnover": round(turnover, 2),
    })
