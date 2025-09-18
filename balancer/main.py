# filepath: balancer/main.py
import os, json, yaml, pandas as pd, time, sys
from .auth import get_session
from .etrade_client import ETradeClient
from .portfolio import extract_positions
from .rebalance import current_weights, build_trades
from .optimizer import optimize_integer_portfolio  # ADD THIS import

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

def _print_plan(plan, detailed=False):
    """
    Print standard summary; if detailed=True also print per-class table for each account.
    """
    print("\n================ REBALANCE PLAN ================")
    for acct in plan["accounts"]:
        f = acct["funding"]
        desc = acct.get("accountDesc") or acct["accountIdKey"]
        print(f"\nAccount {desc} ({acct['accountIdKey']})  Value={acct['accountValue']}  Cash={f['cash_now']}")
        print(f"  Sells ({len(acct['sells'])}) total={f['sells_total']}")
        for s in acct["sells"]:
            print(f"    SELL {s['quantity']} {s['symbol']} ~{s['est_notional']}")
        print(f"  Buys  ({len(acct['buys'])}) total={f['buys_total']} funding_status={f['funding_status']}")
        for b in acct["buys"]:
            print(f"    BUY  {b['quantity']} {b['symbol']} ~{b['est_notional']}")
        if f["funding_status"] == "unfunded":
            print("    NOTE: Buys exceed cash + projected sell proceeds.")

        if detailed:
            rows = acct.get("class_rows", [])
            if rows:
                print("  Allocation Table:")
                # Column headers
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

def run():
    if os.getenv("REBALANCER_KILL"):
        print("[SAFE] REBALANCER_KILL set. Aborting.")
        return

    cfg = load_config()
    targets = cfg["targets"]
    mapping = cfg["mapping"]
    rb = cfg["rebalance"]
    trading = cfg["trading"]
    acct_cfg = cfg["accounts"]

    # Sandbox flag (ENV overrides)
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"

    # Single mode (preview | confirm | auto)
    mode = str(trading.get("mode","preview")).lower()

    # Integer optimizer config
    opt_cfg = (rb.get("integer_optimizer") or {}) if isinstance(rb, dict) else {}
    use_int_opt = bool(opt_cfg.get("enabled", False))
    opt_window = int(opt_cfg.get("window", 4))
    opt_turnover_penalty = float(opt_cfg.get("turnover_penalty", 0.0))
    opt_cash_buffer = float(opt_cfg.get("cash_buffer", 0.0))

    # Execution controls
    allow_scale = bool(trading.get("allow_partial_funding_scale", True))
    max_orders_per_acct = int(trading.get("max_orders_per_account", 20))

    print(f"[Init] Mode={mode} Sandbox={sandbox} OptimizerEnabled={use_int_opt}")

    # Auth
    session = get_session(sandbox)
    client = ETradeClient(session, sandbox=sandbox)

    accounts_df = client.accounts()
    if accounts_df.empty:
        print("[Warn] No accounts.")
        return

    desc_col = next((c for c in ("accountDesc","accountDescDisplay","description") if c in accounts_df.columns), None)
    if desc_col and acct_cfg:
        excl = [s.lower() for s in acct_cfg.get("exclude_if_desc_contains", [])]
        if excl:
            before = len(accounts_df)
            mask = ~accounts_df[desc_col].str.lower().apply(lambda d: any(x in d for x in excl))
            accounts_df = accounts_df[mask]
            print(f"[Filter] Excluded {before - len(accounts_df)} accounts via exclude_if_desc_contains.")
        inc_only = acct_cfg.get("include_only_descriptions", [])
        if inc_only:
            inc_set = {s.lower() for s in inc_only}
            accounts_df = accounts_df[accounts_df[desc_col].str.lower().isin(inc_set)]
            print(f"[Filter] Applied include_only_descriptions; remaining={len(accounts_df)}.")

    if accounts_df.empty:
        print("[Warn] No accounts after filtering.")
        return
    if "accountIdKey" not in accounts_df.columns:
        print("[Error] accountIdKey missing; cannot proceed.")
        return

    # Build lookups (description + numeric)
    account_id_lookup = {}
    account_desc_lookup = {}
    if "accountId" in accounts_df.columns and "accountIdKey" in accounts_df.columns:
        for _, row in accounts_df.iterrows():
            key = str(row["accountIdKey"])
            account_id_lookup[key] = str(row["accountId"])
            desc = (row.get("accountDesc") or row.get("accountName") or "").strip()
            account_desc_lookup[key] = desc or key

    acct_keys = accounts_df.accountIdKey.astype(str).tolist()
    print(f"[Data] Using accountIdKey: {acct_keys}")

    # Gather positions
    symbols = set()
    acct_positions = {}
    for k in acct_keys:
        raw = client.portfolio_raw(k)
        df = extract_positions(raw)
        if df.empty:
            print(f"[Debug] No positions for accountIdKey={k}")
            continue
        acct_positions[k] = df
        symbols.update(df.symbol.dropna().tolist())
    if not acct_positions:
        print("[Warn] No positions across accounts.")
        return

    quotes = client.quotes(list(symbols))
    if quotes.empty:
        print("[Warn] No quotes.")
        return

    symbol_to_class = {v: k for k, v in mapping.items()}
    prices = quotes["last"].to_dict()

    # Current thresholds
    min_drift = float(rb.get("min_percent_drift", 0.5))
    min_notional = float(rb.get("min_notional_trade", 100))
    max_notional_trade = float(rb.get("max_notional_trade", 0)) or None
    account_min_turnover = float(rb.get("account_min_turnover", 0))

    results = []
    total_val_all = 0.0
    print("[Rebalance] Computing trades...")
    for k, pos_df in acct_positions.items():
        weights, invested_total = current_weights(pos_df, quotes, symbol_to_class)
        bal = client.balance(k, account_id_lookup.get(k))
        cash_now = float(bal.get("cash", 0.0))
        total_account_value = invested_total + cash_now
        total_val_all += total_account_value

        # Build current shares map (symbol -> qty)
        current_shares = {}
        for _, row in pos_df.iterrows():
            sym = row["symbol"]
            try:
                current_shares[sym] = int(row["quantity"])
            except Exception:
                current_shares[sym] = 0

        if use_int_opt:
            trades = optimize_integer_portfolio(
                targets_pct=targets,
                prices=prices,
                class_to_symbol=mapping,
                current_shares=current_shares,
                total_account_value=total_account_value,
                window=opt_window,
                turnover_penalty=opt_turnover_penalty,
                min_notional_trade=min_notional,
                account_min_turnover=account_min_turnover,
                cash_buffer=opt_cash_buffer
            )
        else:
            # Original heuristic
            trades = build_trades(
                targets, weights, invested_total, prices, mapping,
                min_drift=min_drift,
                min_notional=min_notional,
                max_notional_trade=max_notional_trade
            )
            # Apply turnover gate
            turnover = sum(t["est_notional"] for t in trades)
            if trades and turnover < account_min_turnover:
                trades = []

        # (Optional) Still blocking buy-only? Keep logic only for heuristic path.
        if not use_int_opt:
            sells = [t for t in trades if t["action"] == "SELL"]
            buys  = [t for t in trades if t["action"] == "BUY"]
            if buys and not sells:
                trades = []

        results.append({
            "accountIdKey": k,
            "accountValue": round(total_account_value, 2),
            "weights": weights,
            "trades": trades
        })

    print(f"[Summary] Aggregate value: {round(total_val_all,2)}")
    print(json.dumps([
        {"accountIdKey": r["accountIdKey"], "accountValue": r["accountValue"], "trades": r["trades"]}
        for r in results
    ], indent=2))

    total_trades = sum(len(r["trades"]) for r in results)
    if total_trades == 0:
        print("[Result] No trades.")
        return

    # Always produce plan first
    show_detailed = os.getenv("REBALANCER_DETAILED_PLAN") == "1"
    plan = _generate_plan(
        results,
        client,
        account_id_lookup,
        account_desc_lookup,
        acct_positions,
        prices,
        targets,
        mapping
    )
    _print_plan(plan, detailed=show_detailed)

    # EXECUTION CONTROL (simplified):
    if mode == "preview":
        print("[Plan] Mode=preview -> no execution.")
        return
    elif mode == "confirm":
        if not _prompt_user_confirm(plan):
            print("[Plan] User declined execution. Exiting.")
            return
        print("[Order] User confirmed. Executing...")
    elif mode == "auto":
        print("[Order] Mode=auto -> executing without prompt.")
    else:
        print(f"[Error] Unknown trading.mode '{mode}'. Exiting.")
        return

    # Proceed with existing SELL then BUY execution block (remove any dry_run / require_confirmation checks):
    for acct in plan["accounts"]:
        k = acct["accountIdKey"]
        desc = acct.get("accountDesc", k)
        sells = acct["sells"]
        buys  = acct["buys"]
        all_trades = sells + buys
        if not all_trades:
            continue
        if len(all_trades) > max_orders_per_acct:
            print(f"[Order][{desc}] Skipped (trade count {len(all_trades)} > {max_orders_per_acct}).")
            continue

        # SELL phase
        print(f"[Execute][{desc}] SELL phase ({len(sells)})")
        for s in sells:
            pv = client.preview_equity_order(k, s["symbol"], "SELL", s["quantity"])
            if not pv["ok"]:
                print(f"[Execute][{desc}] SELL preview FAIL {s['symbol']} status={pv.get('status')}")
                continue
            print(f"[Execute][{desc}] SELL preview OK {s['symbol']} id={pv['preview_id']}")
            pl = client.place_equity_order(k, s["symbol"], "SELL", s["quantity"], pv["preview_id"], pv["client_order_id"])
            if pl["ok"]:
                print(f"[Execute][{desc}] SELL placed {s['symbol']}")
            else:
                print(f"[Execute][{desc}] SELL place FAIL {s['symbol']} status={pl.get('status')}")

        # Refresh cash after sells
        bal = client.balance(k, account_id_lookup.get(k))
        cash_after = float(bal.get("cash", 0.0))  # fixed variable name (was bal_after)
        desired_buy_total = sum(b["est_notional"] for b in buys)
        print(f"[Execute][{desc}] BUY phase ({len(buys)}) cash_after_sells={cash_after} desired_total={desired_buy_total}")

        if not buys:
            continue

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
                print(f"[Execute][{desc}] Scaled buys scale={scale:.4f} final_count={len(exec_buys)}")
            else:
                print(f"[Execute][{desc}] Skipping all buys (insufficient cash).")
                exec_buys = []

        for b in exec_buys:
            pv = client.preview_equity_order(k, b["symbol"], "BUY", b["quantity"])
            if not pv["ok"]:
                print(f"[Execute][{desc}] BUY preview FAIL {b['symbol']} status={pv.get('status')}")
                continue
            print(f"[Execute][{desc}] BUY preview OK {b['symbol']} id={pv['preview_id']}")
            pl = client.place_equity_order(k, b["symbol"], "BUY", b["quantity"], pv["preview_id"], pv["client_order_id"])
            if pl["ok"]:
                print(f"[Execute][{desc}] BUY placed {b['symbol']}")
            else:
                print(f"[Execute][{desc}] BUY place FAIL {b['symbol']} status={pl.get('status')}")
    print("[Order] Done.")

if __name__ == "__main__":
    run()