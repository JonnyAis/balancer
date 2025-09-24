# filepath: balancer/main.py
import os, json, yaml, pandas as pd
from .auth import get_session
from .etrade_client import ETradeClient
from .portfolio import extract_positions
from .rebalance import current_weights, build_trades
from .optimizer import optimize_integer_portfolio  # ADD THIS import
import traceback
import sys
import time

def load_config():
    base_dir = os.path.dirname(__file__)
    path = os.path.join(base_dir, "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError("config.yaml not found.")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    classes = cfg.get("classes", {})
    targets = {k: v["target_percent"] for k, v in classes.items()}
    mapping = {k: v["preferred_symbol"] for k, v in classes.items()}
    return {
        "targets": targets,
        "mapping": mapping,
        "rebalance": cfg.get("rebalance", {}),
        "loop": cfg.get("loop", {}),
        "accounts": cfg.get("accounts", {}),
        "trading": cfg.get("trading", {})
    }

# -------- PLAN HELPERS --------
def _funding_view(cash_now: float, sells, buys):
    sells_total = sum(t["est_notional"] for t in sells)
    buys_total = sum(t["est_notional"] for t in buys)
    avail_after_sells = cash_now + sells_total
    status = ("fully_funded_now" if buys_total <= cash_now else
              ("funded_after_sells" if buys_total <= avail_after_sells else "unfunded"))
    return {
        "cash_now": round(cash_now, 2),
        "sells_total": round(sells_total, 2),
        "buys_total": round(buys_total, 2),
        "available_after_sells": round(avail_after_sells, 2),
        "funding_status": status
    }

def _generate_plan(results, client, account_id_lookup, account_desc_lookup, acct_positions, prices, targets, mapping):
    """
    Build plan; DROP unfunded buys; also attach per-class before/after allocation rows.
    """
    accounts_plan = []
    for r in results:
        k = r["accountIdKey"]
        trades = r["trades"]
        sells = [t for t in trades if t["action"] == "SELL"]
        buys  = [t for t in trades if t["action"] == "BUY"]

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

        accounts_plan.append({
            "accountIdKey": k,
            "accountDesc": account_desc_lookup.get(k, k),
            "accountValue": r["accountValue"],
            "cash_now": fv["cash_now"],
            "sells": sells,
            "buys": buys,
            "funding": fv,
            "cash_source": bal.get("cash_source"),
            "class_rows": rows
        })
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "accounts": accounts_plan
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
        class_rows.append({
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
            "new_weight_pct": round(new_w, 2)
        })
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
            a = agg.setdefault(cls, {
                "class": cls,
                "target_pct": targets.get(cls, 0.0),
                "current_value": 0.0,
                "new_value": 0.0,
                "current_shares": 0,
                "new_shares": 0
            })
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
        if net_shares > 0: action = "BUY"
        elif net_shares < 0: action = "SELL"
        else: action = "HOLD"
        rows.append({
            "class": cls,
            "target_pct": v["target_pct"],
            "current_value": round(v["current_value"], 2),
            "new_value": round(v["new_value"], 2),
            "delta_value": round(delta_val, 2),
            "current_weight_pct": round(cur_w, 4),
            "new_weight_pct": round(new_w, 4),
            "delta_weight_pct": round(delta_w, 4),
            "net_shares": net_shares,
            "net_action": action
        })

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
        "rms_improvement": round(rms_before - rms_after, 4)
    }
    # Sort rows by class name for stable output
    rows.sort(key=lambda x: x["class"])
    return rows, totals

def _print_plan(plan, detailed=False, verbose=False, targets=None):
    """
    Print standard summary; if detailed=True per-account class table;
    if verbose=True also aggregate a global before/after view.
    """
    print("\n================ REBALANCE PLAN ================")
    gross_sells = gross_buys = 0.0
    trade_count = 0

    for acct in plan["accounts"]:
        f = acct["funding"]
        desc = acct.get("accountDesc") or acct["accountIdKey"]
        print(f"\nAccount {desc} ({acct['accountIdKey']})  Value={acct['accountValue']}  Cash={f['cash_now']}")
        print(f"  Sells ({len(acct['sells'])}) total={f['sells_total']}")
        for s in acct["sells"]:
            print(f"    SELL {s['quantity']} {s['symbol']} ~{s['est_notional']}")
            gross_sells += s['est_notional']; trade_count += 1
        print(f"  Buys  ({len(acct['buys'])}) total={f['buys_total']} funding_status={f['funding_status']}")
        for b in acct["buys"]:
            print(f"    BUY  {b['quantity']} {b['symbol']} ~{b['est_notional']}")
            gross_buys += b['est_notional']; trade_count += 1
        if f["funding_status"] == "unfunded":
            print("    NOTE: Buys exceed cash + projected sell proceeds.")

        if detailed:
            rows = acct.get("class_rows", [])
            if rows:
                print("  Allocation Table:")
                hdr = ("    Class  Sym   Target%  CurSh  NewSh  ΔSh  CurVal    NewVal    ΔVal    CurWt%  NewWt%")
                print(hdr)
                for r in rows:
                    print(f"    {r['class']:<6} {r['symbol']:<4} "
                          f"{r['target_pct']:>7.2f}  "
                          f"{r['current_shares']:>5}  {r['new_shares']:>5}  "
                          f"{r['delta_shares']:>3}  "
                          f"{r['current_value']:>7.2f}  {r['new_value']:>7.2f}  "
                          f"{r['delta_value']:>7.2f}  "
                          f"{r['current_weight_pct']:>6.2f}  {r['new_weight_pct']:>6.2f}")

    print(f"\n[Summary] Trades={trade_count} GrossTurnover=${round(gross_sells + gross_buys,2)} "
          f"(Sells=${round(gross_sells,2)} Buys=${round(gross_buys,2)})")

    if verbose and targets:
        agg_rows, totals = _aggregate_classes(plan, targets)
        print("\n--- Global Allocation (Aggregated Across All Accounts) ---")
        print(f"TotalCurrent=${totals['total_current_value']}  TotalNew=${totals['total_new_value']}  "
              f"RMS Drift Before={totals['rms_before']} After={totals['rms_after']} "
              f"Improvement={totals['rms_improvement']}")
        hdr = ("Class   Target%  CurVal     NewVal     ΔVal       CurWt%    NewWt%    ΔWt%    NetAction NetShares")
        print(hdr)
        for r in agg_rows:
            print(f"{r['class']:<7} "
                  f"{r['target_pct']:>7.2f}  "
                  f"{r['current_value']:>9.2f}  {r['new_value']:>9.2f}  {r['delta_value']:>9.2f}  "
                  f"{r['current_weight_pct']:>8.4f}  {r['new_weight_pct']:>8.4f}  {r['delta_weight_pct']:>7.4f}  "
                  f"{r['net_action']:<9} {r['net_shares']:>8}")

    print("\n================================================\n")

def _prompt_user_confirm(plan):
    total_sells = 0.0
    total_buys = 0.0
    trade_count = 0
    for acct in plan["accounts"]:
        for t in acct["sells"]:
            total_sells += t["est_notional"]; trade_count += 1
        for t in acct["buys"]:
            total_buys += t["est_notional"]; trade_count += 1
    gross = round(total_sells + total_buys, 2)
    print(f"\n[Confirm] Accounts: {len(plan['accounts'])} Trades: {trade_count} "
          f"Sells=${round(total_sells,2)} Buys=${round(total_buys,2)} GrossTurnover=${gross}")
    if not sys.stdin.isatty():
        print("[Confirm] Non-interactive session detected; aborting (mode=confirm).")
        return False
    ans = input("Execute these trades? (y/N): ").strip().lower()
    return ans in ("y","yes")

# === Add a reusable single-cycle executor ===
def _one_cycle(cfg):
    targets = cfg["targets"]
    mapping = cfg["mapping"]
    rb = cfg["rebalance"]
    trading = cfg["trading"]
    acct_cfg = cfg["accounts"]

    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"
    mode = str(trading.get("mode","preview")).lower()

    # Integer optimizer config
    opt_cfg = (rb.get("integer_optimizer") or {}) if isinstance(rb, dict) else {}
    use_int_opt = bool(opt_cfg.get("enabled", False))
    opt_window = int(opt_cfg.get("window", 4))
    opt_turnover_penalty = float(opt_cfg.get("turnover_penalty", 0.0))
    opt_cash_buffer = float(opt_cfg.get("cash_buffer", 0.0))

    allow_scale = bool(trading.get("allow_partial_funding_scale", True))
    max_orders_per_acct = int(trading.get("max_orders_per_account", 20))

    print(f"[CycleInit] Mode={mode} Sandbox={sandbox} Optimizer={use_int_opt}")

    interactive = mode != "auto"
    session = get_session(sandbox, interactive=interactive, mode=mode)
    if session is None and os.getenv("FORCE_ONE_INTERACTIVE") == "1" and sys.stdin.isatty():
        print("[Cycle] FORCE_ONE_INTERACTIVE=1 -> attempting interactive auth once.")
        session = get_session(sandbox, interactive=True, mode=mode)

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
        account_id_lookup[key] = str(row.get("accountId",""))
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

    # Prices (unique symbols from mapping)
    symbols = list({mapping[c] for c in mapping if mapping[c]})
    quotes = client.quotes(symbols)
    prices = {sym: q.get("lastPrice") or q.get("close") or 0 for sym, q in quotes.items()}

    # Thresholds (heuristic)
    min_drift = float(rb.get("min_percent_drift", 0.5))
    min_notional = float(rb.get("min_notional_trade", 100))
    max_notional_trade = float(rb.get("max_notional_trade", 0)) or None
    account_min_turnover = float(rb.get("account_min_turnover", 0))

    # Build trades per account
    results = []
    total_val_all = 0.0
    for k in acct_keys:
        pos_df = acct_positions.get(k)
        if pos_df is None:
            results.append({"accountIdKey": k, "accountValue": 0.0, "trades": []})
            continue
        weights, invested_total = current_weights(pos_df, quotes, {mapping[c]: c for c in mapping})
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

        if use_int_opt:
            from .optimizer import optimize_integer_portfolio
            trades = optimize_integer_portfolio(
                targets_pct={cls: targets[cls]["target_percent"] if isinstance(targets[cls], dict) else targets[cls]
                             for cls in targets},
                prices=prices,
                class_to_symbol={cls: targets[cls]["preferred_symbol"] if isinstance(targets[cls], dict) else mapping.get(cls)
                                 for cls in targets},
                current_shares=current_shares,
                total_account_value=total_account_value,
                window=opt_window,
                turnover_penalty=opt_turnover_penalty,
                min_notional_trade=min_notional,
                account_min_turnover=account_min_turnover,
                cash_buffer=opt_cash_buffer
            )
        else:
            # Heuristic path
            # Convert targets to the expected format for build_trades (target_percent and preferred symbol)
            norm_targets = {cls: targets[cls]["target_percent"] if isinstance(targets[cls], dict) else targets[cls]
                            for cls in targets}
            symbol_map = {cls: targets[cls]["preferred_symbol"] if isinstance(targets[cls], dict) else mapping.get(cls)
                          for cls in targets}
            trades = build_trades(
                norm_targets, weights, invested_total, prices, symbol_map,
                min_drift=min_drift,
                min_notional=min_notional,
                max_notional_trade=max_notional_trade
            )
            turnover = sum(t["est_notional"] for t in trades)
            if trades and turnover < account_min_turnover:
                trades = []
            # Remove buy-only set (if you still want that restriction in heuristic path)
            sells = [t for t in trades if t["action"] == "SELL"]
            buys = [t for t in trades if t["action"] == "BUY"]
            if buys and not sells:
                trades = []

        results.append({
            "accountIdKey": k,
            "accountValue": round(total_account_value, 2),
            "trades": trades
        })

    print(f"[CycleSummary] Aggregate value: {round(total_val_all,2)}")

    # Plan (detailed view)
    show_detailed = os.getenv("REBALANCER_DETAILED_PLAN") == "1"
    verbose = os.getenv("REBALANCER_VERBOSE_PLAN") == "1"
    plan = _generate_plan(
        results,
        client,
        account_id_lookup,
        account_desc_lookup,
        acct_positions,
        prices,
        {cls: (targets[cls]["target_percent"] if isinstance(targets[cls], dict) else targets[cls]) for cls in targets},
        {cls: (targets[cls]["preferred_symbol"] if isinstance(targets[cls], dict) else mapping.get(cls)) for cls in targets}
    )
    # Pass the targets dict (flat values) for verbose aggregation
    flat_targets = {cls: (targets[cls]["target_percent"] if isinstance(targets[cls], dict) else targets[cls]) for cls in targets}
    _print_plan(plan, detailed=show_detailed, verbose=verbose, targets=flat_targets)

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
            pl = client.place_equity_order(k, s["symbol"], "SELL", s["quantity"], pv["preview_id"], pv["client_order_id"])
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
                    scaled.append({**b, "quantity": qty, "est_notional": round(px * qty, 2), "funding_scaled": True})
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
            pl = client.place_equity_order(k, b["symbol"], "BUY", b["quantity"], pv["preview_id"], pv["client_order_id"])
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
    trading_mode = str(cfg["trading"].get("mode","preview")).lower()
    if trading_mode != "auto":
        print(f"[Loop] Enabled but trading.mode={trading_mode} != auto -> stopping after first cycle.")
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
            _one_cycle(cfg)

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