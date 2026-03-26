"""
Daily circuit breaker. Tracks cumulative trades/turnover per calendar day.
State file: ~/.balancer/circuit_breaker.json
"""

import json
import os
from datetime import date

_STATE_DIR = os.path.join(os.path.expanduser("~"), ".balancer")
_STATE_PATH = os.path.join(_STATE_DIR, "circuit_breaker.json")


def _load_state() -> dict:
    """Load state, resetting if it's a new day."""
    if not os.path.exists(_STATE_PATH):
        return {"date": str(date.today()), "trades": 0, "turnover": 0.0}
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        if state.get("date") != str(date.today()):
            return {"date": str(date.today()), "trades": 0, "turnover": 0.0}
        return state
    except Exception:
        return {"date": str(date.today()), "trades": 0, "turnover": 0.0}


def _save_state(state: dict):
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def check_allowed(num_trades: int, turnover: float, max_trades: int = 0, max_turnover: float = 0) -> tuple:
    """
    Check if the proposed trades are within daily limits.
    Returns (allowed: bool, reason: str).
    max_trades=0 or max_turnover=0 means unlimited.
    """
    state = _load_state()
    new_trades = state["trades"] + num_trades
    new_turnover = state["turnover"] + turnover

    if max_trades > 0 and new_trades > max_trades:
        return False, f"Daily trade limit reached: {state['trades']}/{max_trades} trades used, {num_trades} new requested"

    if max_turnover > 0 and new_turnover > max_turnover:
        return False, f"Daily turnover limit reached: ${state['turnover']:,.0f}/${max_turnover:,.0f} used, ${turnover:,.0f} new requested"

    return True, "OK"


def record_trades(num_trades: int, turnover: float):
    """Record executed trades against today's limits."""
    state = _load_state()
    state["trades"] += num_trades
    state["turnover"] += turnover
    _save_state(state)
