# filepath: balancer/main.py
import os, json, yaml, pandas as pd
from .auth import start_session
from .etrade_client import ETradeClient
from .portfolio import extract_positions
from .rebalance import current_weights, build_trades

def load_config():
    path = os.path.join(os.path.dirname(__file__), "asset_classes.yaml")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    classes = cfg["classes"]
    targets = {k: v["target_percent"] for k, v in classes.items()}
    mapping = {k: v["preferred_symbol"] for k, v in classes.items()}
    rules = cfg["rebalance"]
    return targets, mapping, rules

def run():
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"
    dry = os.getenv("REBALANCER_DRY_RUN", "true").lower() == "true"
    print(f"[Init] Sandbox={sandbox} DryRun={dry}")
    session = start_session(sandbox)
    client = ETradeClient(session)

    print("[Data] Fetching accounts...")
    accounts = client.accounts()
    acct_keys = accounts.accountIdKey.tolist()
    print(f"[Data] Accounts found: {len(acct_keys)}")

    print("[Data] Fetching portfolios...")
    pos_frames = [extract_positions(client.portfolio_raw(k)) for k in acct_keys]
    positions = pd.concat(pos_frames, ignore_index=True) if pos_frames else pd.DataFrame(columns=["symbol","quantity","marketValue"])
    symbols = positions.symbol.dropna().unique().tolist()
    print(f"[Data] Symbols in positions: {len(symbols)}")

    quotes = client.quotes(symbols)
    targets, mapping, rules = load_config()
    symbol_to_class = {v: k for k, v in mapping.items()}

    weights, total_val = current_weights(positions, quotes, symbol_to_class)
    prices = quotes["last"].to_dict()
    trades = build_trades(
        targets, weights, total_val, prices, mapping,
        min_drift=rules["min_percent_drift"],
        min_notional=rules["min_notional_trade"]
    )

    print("Current weights:", json.dumps(weights, indent=2))
    print(f"Total portfolio value: {round(total_val,2)}")
    print("Proposed trades:", json.dumps(trades, indent=2))

    if not trades:
        print("[Result] No trades needed.")
        return

    if dry:
        print("[Result] Dry run only. Set REBALANCER_DRY_RUN=false to enable live order logic (not yet implemented).")
        return

    print("[Order] Live order placement not implemented yet (safeguard).")

if __name__ == "__main__":
    run()