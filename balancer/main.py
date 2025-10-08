# filepath: balancer/main.py
import os
import sys
import time
import traceback

import pandas as pd
import yaml
from tabulate import tabulate
import pandas_market_calendars as mcal
from datetime import datetime, timezone, timedelta

from .auth import get_session
from .etrade_client import ETradeClient
from .optimizer import optimize_integer_portfolio  # ADD THIS import
from .portfolio import extract_positions
from .rebalance import current_weights


def load_config():
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError("config.yaml not found.")

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    classes = cfg.get("classes", {})

    # Extract and validate targets/mapping
    targets = {}
    mapping = {}

    for class_name, class_config in classes.items():
        if not isinstance(class_config, dict):
            raise ValueError(
                f"Class '{class_name}' must have 'target_percent' and 'preferred_symbol' fields"
            )

        target_percent = class_config.get("target_percent")
        preferred_symbol = class_config.get("preferred_symbol")

        if target_percent is None or preferred_symbol is None:
            raise ValueError(f"Class '{class_name}' missing required fields")

        targets[class_name] = float(target_percent)
        mapping[class_name] = str(preferred_symbol).upper()

    # Validate targets sum to 100%
    total_target = sum(targets.values())
    if abs(total_target - 100.0) > 0.01:
        raise ValueError(f"Target percentages must sum to 100.0%, got {total_target:.2f}%")

    return {
        "targets": targets,
        "mapping": mapping,
        "rebalance": cfg.get("rebalance", {}),
        "loop": cfg.get("loop", {}),
        "accounts": cfg.get("accounts", {}),
        "trading": cfg.get("trading", {}),
    }


def is_market_open():
    """
    Return (is_open: bool, next_open: datetime|None).
    - If market is open now -> (True, None)
    - If market is closed -> (False, next_open_datetime or None)
    """
    nyse = mcal.get_calendar("NYSE")
    now = datetime.now(timezone.utc)

    # Try today's schedule first in a safe way
    try:
        schedule = nyse.schedule(start_date=now.date(), end_date=now.date())
    except Exception:
        schedule = None

    # If schedule exists and open_at_time works, check now
    if schedule is not None and not schedule.empty:
        try:
            if nyse.open_at_time(schedule, now):
                return True, None
        except Exception:
            # If open_at_time cannot determine (timestamp not covered), continue to search
            pass

    # Find next trading day's open within a reasonable window
    max_lookahead_days = 30
    for d in range(0, max_lookahead_days + 1):
        day = (now + timedelta(days=d)).date()
        try:
            sched = nyse.schedule(start_date=day, end_date=day)
            if sched is None or sched.empty:
                continue
            next_open = sched.iloc[0]["market_open"].to_pydatetime()
            # Normalize to timezone-aware UTC
            if next_open.tzinfo is None:
                next_open = next_open.replace(tzinfo=timezone.utc)
            return False, next_open
        except Exception:
            continue

    # No open found in lookahead window
    return False, None


# -------- PLAN HELPERS --------
def _funding_view(cash_now: float, sells, buys):
    sells_total = sum(t["est_notional"] for t in sells)
    buys_total = sum(t["est_notional"] for t in buys)
    avail_after_sells = cash_now + sells_total
    status = (
        "fully_funded_now"
        if buys_total <= cash_now
        else ("funded_after_sells" if buys_total <= avail_after_sells else "unfunded")
    )
    return {
        "cash_now": round(cash_now, 2),
        "sells_total": round(sells_total, 2),
        "buys_total": round(buys_total, 2),
        "available_after_sells": round(avail_after_sells, 2),
        "funding_status": status,
    }


def _generate_plan(
    results,
    client,
    account_id_lookup,
    account_desc_lookup,
    acct_positions,
    prices,
    targets,
    mapping,
):
    """
    Build plan; DROP unfunded buys; also attach per-class before/after allocation rows.
    """
    accounts_plan = []
    for r in results:
        k = r["accountIdKey"]
        trades = r["trades"]
        sells = [t for t in trades if t["action"] == "SELL"]
        buys = [t for t in trades if t["action"] == "BUY"]

        bal = client.balance(k, account_id_lookup.get(k))
        cash_now = float(bal.get("cash", 0.0))

        fv = _funding_view(cash_now, sells, buys)

        # Enforce funding (drop all buys if not fully funded by cash + sells)
        if buys:
            if fv["buys_total"] > fv["available_after_sells"]:
                buys = []
                # Recompute funding view w/out buys
                fv = _funding_view(cash_now, sells, buys)

        # Build detailed per-class rows (current vs after-trade)
        rows = _build_class_rows(k, acct_positions.get(k), sells + buys, targets, mapping, prices)

        accounts_plan.append(
            {
                "accountIdKey": k,
                "accountDesc": account_desc_lookup.get(k, k),
                "accountValue": r["accountValue"],
                "cash_now": fv["cash_now"],
                "sells": sells,
                "buys": buys,
                "funding": fv,
                "cash_source": bal.get("cash_source"),
                "class_rows": rows,
            }
        )
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accounts": accounts_plan,
    }


def _build_class_rows(account_key, positions_df, trades, targets, mapping, prices):
    """
    Return list of dict rows:
      class, symbol, target_pct, current_shares, new_shares, delta_shares,
      current_value, new_value, delta_value, current_weight_pct, new_weight_pct
    """
    # Map symbol -> current shares
    current_shares = {}
    if positions_df is not None:
        for _, row in positions_df.iterrows():
            sym = row.get("symbol")
            if not sym:
                continue
            try:
                current_shares[sym] = int(row.get("quantity", 0))
            except Exception:
                current_shares[sym] = 0

    # Aggregate planned deltas by symbol
    delta_by_symbol = {}
    for t in trades:
        sym = t["symbol"]
        q = int(t["quantity"])
        if t["action"] == "SELL":
            q = -q
        delta_by_symbol[sym] = delta_by_symbol.get(sym, 0) + q

    # Compute current/new values per class
    class_rows = []
    # Collect all classes present in targets (drives ordering)
    all_classes = list(targets.keys())

    # Precompute invested totals
    def value(sym, shares):
        px = prices.get(sym, 0)
        return shares * px

    current_total_value = 0.0
    new_total_value = 0.0
    tmp_values = []
    for cls in all_classes:
        sym = mapping.get(cls)
        if not sym:
            continue
        cur_sh = current_shares.get(sym, 0)
        delta = delta_by_symbol.get(sym, 0)
        new_sh = max(0, cur_sh + delta)
        cur_val = value(sym, cur_sh)
        new_val = value(sym, new_sh)
        current_total_value += cur_val
        new_total_value += new_val
        tmp_values.append((cls, sym, cur_sh, new_sh, delta, cur_val, new_val))

    for cls, sym, cur_sh, new_sh, delta, cur_val, new_val in tmp_values:
        if current_total_value > 0:
            cur_w = 100 * cur_val / current_total_value
        else:
            cur_w = 0.0
        if new_total_value > 0:
            new_w = 100 * new_val / new_total_value
        else:
            new_w = 0.0
        class_rows.append(
            {
                "class": cls,
                "symbol": sym,
                "target_pct": targets[cls],
                "current_shares": cur_sh,
                "new_shares": new_sh,
                "delta_shares": delta,
                "current_value": round(cur_val, 2),
                "new_value": round(new_val, 2),
                "delta_value": round(new_val - cur_val, 2),
                "current_weight_pct": round(cur_w, 2),
                "new_weight_pct": round(new_w, 2),
            }
        )
    return class_rows


def _aggregate_classes(plan, targets):
    """
    Aggregate per-class current/new values & shares across all accounts.
    Returns (rows, totals_dict)
    """
    agg = {}
    for acct in plan["accounts"]:
        for r in acct.get("class_rows", []):
            cls = r["class"]
            a = agg.setdefault(
                cls,
                {
                    "class": cls,
                    "target_pct": targets.get(cls, 0.0),
                    "current_value": 0.0,
                    "new_value": 0.0,
                    "current_shares": 0,
                    "new_shares": 0,
                },
            )
            a["current_value"] += r["current_value"]
            a["new_value"] += r["new_value"]
            a["current_shares"] += r["current_shares"]
            a["new_shares"] += r["new_shares"]

    total_current = sum(v["current_value"] for v in agg.values())
    total_new = sum(v["new_value"] for v in agg.values())

    rows = []
    for cls, v in agg.items():
        cur_w = (v["current_value"] / total_current * 100) if total_current > 0 else 0
        new_w = (v["new_value"] / total_new * 100) if total_new > 0 else 0
        delta_w = new_w - cur_w
        delta_val = v["new_value"] - v["current_value"]
        net_shares = v["new_shares"] - v["current_shares"]
        if net_shares > 0:
            action = "BUY"
        elif net_shares < 0:
            action = "SELL"
        else:
            action = "HOLD"
        rows.append(
            {
                "class": cls,
                "target_pct": v["target_pct"],
                "current_value": round(v["current_value"], 2),
                "new_value": round(v["new_value"], 2),
                "delta_value": round(delta_val, 2),
                "current_weight_pct": round(cur_w, 4),
                "new_weight_pct": round(new_w, 4),
                "delta_weight_pct": round(delta_w, 4),
                "net_shares": net_shares,
                "net_action": action,
            }
        )

    # Drift metrics (RMS deviation from target)
    import math

    def rms(values, key):
        if not values:
            return 0.0
        s = 0.0
        n = 0
        for r in values:
            tgt = r["target_pct"]
            if tgt is None:
                continue
            s += (r[key] - tgt) ** 2
            n += 1
        return math.sqrt(s / n) if n else 0.0

    rms_before = rms(rows, "current_weight_pct")
    rms_after = rms(rows, "new_weight_pct")

    totals = {
        "total_current_value": round(total_current, 2),
        "total_new_value": round(total_new, 2),
        "rms_before": round(rms_before, 4),
        "rms_after": round(rms_after, 4),
        "rms_improvement": round(rms_before - rms_after, 4),
    }
    # Sort rows by class name for stable output
    rows.sort(key=lambda x: x["class"])
    return rows, totals


def _print_plan(plan, detailed=True, verbose=False, targets=None):
    """
    Print rebalance plan with optimized information density.
    detailed: Show per-account allocation tables (default True)
    verbose: Show global aggregation + RMS metrics (env var controlled)
    """
    print("\n" + "=" * 60)
    print("REBALANCE PLAN".center(60))
    print("=" * 60)

    total_trades = 0
    total_turnover = 0.0

    for acct in plan["accounts"]:
        f = acct["funding"]
        desc = acct.get("accountDesc", "Unknown")
        val = f"${acct['accountValue']:,.0f}"
        cash = f"${f['cash_now']:,.0f}"

        print(f"\n🏦 {desc} ({acct['accountIdKey']})  Value={val}  Cash={cash}")

        if detailed and acct.get("class_rows"):
            # Prepare table data
            table_data = []
            for r in acct["class_rows"]:
                class_name = r["class"]
                if class_name.lower() == "international":
                    class_name = "Intl"
                elif len(class_name) > 7:
                    class_name = class_name[:7]
                symbol = r["symbol"]
                table_data.append(
                    [
                        class_name,
                        symbol,
                        f"{r['target_pct']:>5.1f}%",
                        r["current_shares"],
                        r["new_shares"],
                        f"{r['delta_shares']:+d}",
                        f"${r['current_value']:,.0f}",
                        f"${r['new_value']:,.0f}",
                        f"${r['delta_value']:+,.0f}",
                        f"{r['current_weight_pct']:>5.1f}%",
                        f"{r['new_weight_pct']:>5.1f}%",
                    ]
                )
            headers = [
                "Class",
                "Sym",
                "Target",
                "CurSh",
                "NewSh",
                "ΔSh",
                "CurVal",
                "NewVal",
                "ΔVal",
                "CurWt%",
                "NewWt%",
            ]
            print(tabulate(table_data, headers=headers, tablefmt="fancy_grid"))

        # Compact trade summary
        trades_summary = []
        for s in acct["sells"]:
            trades_summary.append(
                f"SELL {s['quantity']} {s['symbol']} (~${s['est_notional']:,.0f})"
            )
            total_turnover += s["est_notional"]
            total_trades += 1

        for b in acct["buys"]:
            trades_summary.append(f"BUY {b['quantity']} {b['symbol']} (~${b['est_notional']:,.0f})")
            total_turnover += b["est_notional"]
            total_trades += 1

        if trades_summary:
            print(f"\n📋 Trades: {' • '.join(trades_summary)}")

            # Remove the confusing funding message entirely
            # Users understand that sells fund buys - no need to over-explain
        else:
            print("📋 No trades needed")

    # Global summary
    if verbose and targets:
        agg_rows, totals = _aggregate_classes(plan, targets)
        print(f"\n📊 Global Summary: {total_trades} trades • ${total_turnover:,.0f} turnover")
        print(
            f"   Drift: {totals['rms_before']:.2f}% → {totals['rms_after']:.2f}% RMS (Δ{totals['rms_improvement']:+.2f}%)"
        )

        print("\n--- Global Allocation ---")
        for r in agg_rows:
            action_icon = (
                "🟢" if r["net_action"] == "BUY" else "🔴" if r["net_action"] == "SELL" else "⚪"
            )

            class_name = r["class"]
            if class_name.lower() == "international":
                class_name = "Intl"
            elif len(class_name) > 7:
                class_name = class_name[:7]

            print(
                f"{action_icon} {class_name:<7} {r['target_pct']:>5.1f}% target │"
                f" {r['current_weight_pct']:>5.1f}% → {r['new_weight_pct']:>5.1f}% │"
                f" {r['net_action']:<4} {abs(r['net_shares']):>3} shares"
            )
    else:
        print(f"\n📊 Summary: {total_trades} trades • ${total_turnover:,.0f} turnover")

    print("\n" + "=" * 60 + "\n")


# Keep the existing _aggregate_classes function (it's already good):
def _aggregate_classes(plan, targets):
    """
    Aggregate per-class current/new values & shares across all accounts.
    Returns (rows, totals_dict)
    """
    agg = {}
    for acct in plan["accounts"]:
        for r in acct.get("class_rows", []):
            cls = r["class"]
            a = agg.setdefault(
                cls,
                {
                    "class": cls,
                    "target_pct": targets.get(cls, 0.0),
                    "current_value": 0.0,
                    "new_value": 0.0,
                    "current_shares": 0,
                    "new_shares": 0,
                },
            )
            a["current_value"] += r["current_value"]
            a["new_value"] += r["new_value"]
            a["current_shares"] += r["current_shares"]
            a["new_shares"] += r["new_shares"]

    total_current = sum(v["current_value"] for v in agg.values())
    total_new = sum(v["new_value"] for v in agg.values())

    rows = []
    for cls, v in agg.items():
        cur_w = (v["current_value"] / total_current * 100) if total_current > 0 else 0
        new_w = (v["new_value"] / total_new * 100) if total_new > 0 else 0
        delta_w = new_w - cur_w
        delta_val = v["new_value"] - v["current_value"]
        net_shares = v["new_shares"] - v["current_shares"]
        if net_shares > 0:
            action = "BUY"
        elif net_shares < 0:
            action = "SELL"
        else:
            action = "HOLD"
        rows.append(
            {
                "class": cls,
                "target_pct": v["target_pct"],
                "current_value": round(v["current_value"], 2),
                "new_value": round(v["new_value"], 2),
                "delta_value": round(delta_val, 2),
                "current_weight_pct": round(cur_w, 4),
                "new_weight_pct": round(new_w, 4),
                "delta_weight_pct": round(delta_w, 4),
                "net_shares": net_shares,
                "net_action": action,
            }
        )

    # Drift metrics (RMS deviation from target)
    import math

    def rms(values, key):
        if not values:
            return 0.0
        s = 0.0
        n = 0
        for r in values:
            tgt = r["target_pct"]
            if tgt is None:
                continue
            s += (r[key] - tgt) ** 2
            n += 1
        return math.sqrt(s / n) if n else 0.0

    rms_before = rms(rows, "current_weight_pct")
    rms_after = rms(rows, "new_weight_pct")

    totals = {
        "total_current_value": round(total_current, 2),
        "total_new_value": round(total_new, 2),
        "rms_before": round(rms_before, 4),
        "rms_after": round(rms_after, 4),
        "rms_improvement": round(rms_before - rms_after, 4),
    }
    # Sort rows by class name for stable output
    rows.sort(key=lambda x: x["class"])
    return rows, totals


def _prompt_user_confirm(plan):
    total_sells = 0.0
    total_buys = 0.0
    trade_count = 0
    for acct in plan["accounts"]:
        for t in acct["sells"]:
            total_sells += t["est_notional"]
            trade_count += 1
        for t in acct["buys"]:
            total_buys += t["est_notional"]
            trade_count += 1
    gross = round(total_sells + total_buys, 2)
    print(
        f"\n[Confirm] Accounts: {len(plan['accounts'])} Trades: {trade_count} "
        f"Sells=${round(total_sells, 2)} Buys=${round(total_buys, 2)} GrossTurnover=${gross}"
    )
    if not sys.stdin.isatty():
        print("[Confirm] Non-interactive session detected; aborting (mode=confirm).")
        return False
    ans = input("Execute these trades? (y/N): ").strip().lower()
    return ans in ("y", "yes")


# === Add a reusable single-cycle executor ===
def _one_cycle(cfg):
    targets = cfg["targets"]
    mapping = cfg["mapping"]

    # Get settings from config and environment
    rebalance_cfg = cfg.get("rebalance", {})
    opt_cfg = rebalance_cfg.get("integer_optimizer", {})
    trading_cfg = cfg.get("trading", {})
    acct_cfg = cfg.get("accounts", {})

    # Get mode and sandbox from environment (set by run.py)
    mode = os.getenv("TRADING_MODE", "preview").lower()
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"

    # Get other settings from config
    max_orders_per_acct = 20  # Could move to config if needed
    allow_scale = True  # Could move to config if needed

    print(f"[CycleInit] Mode={mode} Sandbox={sandbox} Optimizer=True")

    interactive = mode != "auto"
    session = get_session(sandbox, interactive=interactive)
    # Remove the FORCE_ONE_INTERACTIVE check since we removed that env var

    if session is None:
        print("[Cycle] No session (auth unavailable). Skipping this cycle.")
        return
    client = ETradeClient(session, sandbox=sandbox)

    accounts_df = client.accounts()
    if accounts_df.empty:
        print("[Warn] No accounts.")
        return

    # Filtering
    excl = [s.lower() for s in (acct_cfg.get("exclude_if_desc_contains") or [])]
    only = [s.lower() for s in (acct_cfg.get("include_only_descriptions") or [])]

    def _keep(desc):
        d = (desc or "").lower()
        if any(x in d for x in excl):
            return False
        if only and d not in only:
            return False
        return True

    accounts_df = accounts_df[accounts_df.accountDesc.apply(_keep)]
    if accounts_df.empty:
        print("[Warn] No accounts after filters.")
        return

    # Lookups
    account_id_lookup = {}
    account_desc_lookup = {}
    for _, row in accounts_df.iterrows():
        key = str(row["accountIdKey"])
        account_id_lookup[key] = str(row.get("accountId", ""))
        desc = (row.get("accountDesc") or row.get("accountName") or "").strip()
        account_desc_lookup[key] = desc or key

    acct_keys = accounts_df.accountIdKey.astype(str).tolist()
    print(f"[CycleData] Accounts: {acct_keys}")

    # Collect positions
    acct_positions = {}
    for k in acct_keys:
        port = client.portfolio_raw(k)
        try:
            acct_positions[k] = extract_positions(port)
        except Exception as e:
            print(f"[Err] Positions parse fail {k}: {e}")
            acct_positions[k] = None

    # Prices (unique symbols from mapping) - UPDATED
    symbols = list({mapping[c] for c in mapping if mapping[c]})
    quotes_dict = client.quotes(symbols)  # Now returns dict instead of DataFrame

    # Extract prices from the quotes dict
    prices = {}
    for sym in symbols:
        quote_data = quotes_dict.get(sym, {})
        price = (
            quote_data.get("lastPrice") or quote_data.get("last") or quote_data.get("close") or 0
        )
        prices[sym] = float(price) if price > 0 else 0
        if price == 0:
            print(f"[Prices] Warning: Zero price for {sym}")

    print(f"[Prices] Retrieved: {prices}")  # Debug output

    # Convert dict to DataFrame format that rebalance.py expects
    quotes_data = []
    for sym in symbols:
        quote_data = quotes_dict.get(sym, {})
        price = (
            quote_data.get("lastPrice") or quote_data.get("last") or quote_data.get("close") or 0
        )

        # Add to quotes_data for DataFrame creation
        quotes_data.append({"symbol": sym, "last": price, "lastPrice": price, "close": price})

    # Create DataFrame that rebalance.py expects
    quotes_df = pd.DataFrame(quotes_data)
    if not quotes_df.empty:
        quotes_df.set_index("symbol", inplace=True)

    # Build optimizer inputs per account
    results = []
    total_val_all = 0.0
    for k in acct_keys:
        pos_df = acct_positions.get(k)
        if pos_df is None or pos_df.empty:
            continue

        # Current weights (pass DataFrame as expected) - MOVED INSIDE THE LOOP
        weights, invested_total = current_weights(
            pos_df, quotes_df, {mapping[c]: c for c in mapping}
        )

        bal = client.balance(k, account_id_lookup.get(k))
        cash_now = float(bal.get("cash", 0.0))
        total_account_value = invested_total + cash_now
        total_val_all += total_account_value

        current_shares = {}
        for _, row in pos_df.iterrows():
            sym = row["symbol"]
            try:
                current_shares[sym] = int(row["quantity"])
            except Exception:
                current_shares[sym] = 0

        # Always use integer optimizer (remove heuristic code path)
        trades = optimize_integer_portfolio(
            targets_pct=targets,
            prices=prices,
            class_to_symbol=mapping,
            current_shares=current_shares,
            total_account_value=total_account_value,
            window=opt_cfg.get("window", 4),
            turnover_penalty=opt_cfg.get("turnover_penalty", 0.0),
            min_notional_trade=rebalance_cfg.get("min_notional_trade", 100),
            account_min_turnover=rebalance_cfg.get("account_min_turnover", 200),
            cash_buffer=rebalance_cfg.get("cash_buffer", 0),
        )

        results.append(
            {"accountIdKey": k, "accountValue": round(total_account_value, 2), "trades": trades}
        )

    print(f"[CycleSummary] Aggregate value: {round(total_val_all, 2)}")

    # Plan (detailed view)
    show_detailed = True
    show_verbose = cfg.get("display", {}).get("verbose", False)  # Move to config if needed

    plan = _generate_plan(
        results,
        client,
        account_id_lookup,
        account_desc_lookup,
        acct_positions,
        prices,
        targets,  # Already in correct format
        mapping,  # Already in correct format
    )

    # Print plan with optimized defaults
    _print_plan(plan, detailed=True, verbose=False, targets=targets)

    if mode == "preview":
        print("[Cycle] Mode=preview -> no execution.")
        return
    if mode == "confirm":
        if not _prompt_user_confirm(plan):
            print("[Cycle] User declined.")
            return
        print("[Cycle] Confirmed -> executing.")
    elif mode == "auto":
        print("[Cycle] Auto execute.")
    else:
        print(f"[Cycle] Unknown mode={mode}; skipping execution.")
        return

    # Execute orders
    for acct in plan["accounts"]:
        k = acct["accountIdKey"]
        desc = acct.get("accountDesc", k)
        sells = acct["sells"]
        buys = acct["buys"]
        all_trades = sells + buys
        if not all_trades:
            continue
        if len(all_trades) > max_orders_per_acct:
            print(f"[Execute][{desc}] Skip (trade count {len(all_trades)})")
            continue

        # Sells
        for s in sells:
            pv = client.preview_equity_order(k, s["symbol"], "SELL", s["quantity"])
            if not pv["ok"]:
                print(f"[Execute][{desc}] SELL preview FAIL {s['symbol']}")
                continue
            pl = client.place_equity_order(
                k, s["symbol"], "SELL", s["quantity"], pv["preview_id"], pv["client_order_id"]
            )
            print(f"[Execute][{desc}] SELL {s['symbol']} status={'OK' if pl['ok'] else 'FAIL'}")

        # Refresh cash
        bal2 = client.balance(k, account_id_lookup.get(k))
        cash_after = float(bal2.get("cash", 0.0))
        desired_buy_total = sum(b["est_notional"] for b in buys)
        print(f"[Execute][{desc}] Post-sell cash={cash_after} Planned buys={desired_buy_total}")

        exec_buys = buys
        if desired_buy_total > cash_after:
            if allow_scale and cash_after > 0:
                scale = cash_after / desired_buy_total
                scaled = []
                for b in buys:
                    px = b["est_notional"] / max(b["quantity"], 1)
                    new_val = b["est_notional"] * scale
                    qty = int(new_val / px)
                    if qty <= 0:
                        continue
                    scaled.append(
                        {
                            **b,
                            "quantity": qty,
                            "est_notional": round(px * qty, 2),
                            "funding_scaled": True,
                        }
                    )
                exec_buys = scaled
                print(f"[Execute][{desc}] Scaled buys count={len(exec_buys)} scale={scale:.4f}")
            else:
                print(f"[Execute][{desc}] Dropping all buys (insufficient cash).")
                exec_buys = []

        for b in exec_buys:
            pv = client.preview_equity_order(k, b["symbol"], "BUY", b["quantity"])
            if not pv["ok"]:
                print(f"[Execute][{desc}] BUY preview FAIL {b['symbol']}")
                continue
            pl = client.place_equity_order(
                k, b["symbol"], "BUY", b["quantity"], pv["preview_id"], pv["client_order_id"]
            )
            print(f"[Execute][{desc}] BUY {b['symbol']} status={'OK' if pl['ok'] else 'FAIL'}")

    print("[Cycle] Complete.")


def run():
    if os.getenv("REBALANCER_KILL"):
        print("[SAFE] REBALANCER_KILL set. Aborting.")
        return

    cfg = load_config()
    loop_cfg = cfg.get("loop", {}) or {}
    loop_enabled = bool(loop_cfg.get("enabled", False))
    interval_min = int(loop_cfg.get("interval_minutes", 30))

    # Always run at least once
    _one_cycle(cfg)

    if not loop_enabled:
        return

    # Need mode=auto for repeating meaningful cycles (validation done inside each cycle too)
    trading_mode = str(cfg["trading"].get("mode", "preview")).lower()
    if trading_mode != "auto":
        print(
            f"[Loop] Enabled but trading.mode={trading_mode} != auto -> stopping after first cycle."
        )
        return

    print(f"[Loop] Continuous mode every {interval_min} minute(s). Ctrl+C to stop.")
    while True:
        try:
            # Optional config reload each cycle
            if os.getenv("REBALANCER_RELOAD_CONFIG") == "1":
                cfg = load_config()
                loop_cfg = cfg.get("loop", {}) or {}
                interval_min = int(loop_cfg.get("interval_minutes", interval_min))

            next_run = time.time() + interval_min * 60
            while True:
                remaining = int(next_run - time.time())
                if remaining <= 0:
                    break
                if remaining % 30 == 0 or remaining <= 10:
                    print(f"[Loop] Next cycle in {remaining}s", end="\r")
                time.sleep(1)

            print("\n[Loop] ===== New Cycle =====")
            if os.getenv("REBALANCER_KILL"):
                print("[Loop] Kill flag detected. Exiting.")
                break

            try:
                is_open, next_open = is_market_open()
            except Exception as e:
                print(f"[Market][Error] Market hours check failed: {e}")
                # If we can't determine market hours, skip this cycle but keep looping
                continue

            if is_open:
                _one_cycle(cfg)
            else:
                now = datetime.now(timezone.utc)
                next_cycle_time = now + timedelta(minutes=interval_min)

                if next_open is None:
                    print("[Market] NYSE closed; no upcoming open found in lookup window. Exiting loop.")
                    break

                # If the next scheduled open is after the next cycle time, stop the program
                if next_open > next_cycle_time:
                    print("[Market] NYSE is closed.")
                    print(f"[Market] Next open: {next_open.isoformat()}")
                    print("[Market] Next scheduled cycle will run before the market re-opens. Exiting.")
                    break
                else:
                    # market will be open by the next cycle; skip this cycle and continue waiting
                    print("[Market] NYSE is closed now, but will open before next cycle. Skipping this cycle.")
                    continue

        except KeyboardInterrupt:
            print("\n[Loop] Interrupted by user.")
            break
        except Exception as e:
            print(f"[Loop][Error] {e}")
            traceback.print_exc()
            # Continue to next scheduled cycle
            continue


# Add this diagnostic wrapper & proper module entrypoint at VERY BOTTOM of file
def _entry():
    try:
        print("[Startup] balancer.main entrypoint")
        run()
        print("[Shutdown] run() returned normally")
    except KeyboardInterrupt:
        print("\n[Exit] KeyboardInterrupt")
    except Exception as e:
        import traceback

        print(f"[Fatal] Unhandled exception: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    _entry()
