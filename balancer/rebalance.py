# filepath: balancer/rebalance.py
def current_weights(positions_df, quotes_df, symbol_to_class: dict):
    df = positions_df.copy()
    if df.empty:
        return {}, 0.0
    df["asset_class"] = df["symbol"].map(symbol_to_class)
    df["price"] = df["symbol"].map(quotes_df["last"])
    df["value"] = df["quantity"] * df["price"]
    grouped = df.groupby("asset_class", dropna=True)["value"].sum()
    total = grouped.sum()
    if total == 0:
        return {}, 0.0
    weights = (grouped / total * 100).to_dict()
    return weights, float(total)

def build_trades(targets: dict, current: dict, total_value: float,
                 prices: dict, class_to_symbol: dict,
                 min_drift=0.5, min_notional=100.0):
    trades = []
    if total_value <= 0:
        return trades
    for cls, tgt in targets.items():
        curr = current.get(cls, 0.0)
        drift_pct = tgt - curr
        if abs(drift_pct) < min_drift:
            continue
        sym = class_to_symbol.get(cls)
        if sym not in prices:
            continue
        delta_value = total_value * (drift_pct / 100.0)
        if abs(delta_value) < min_notional:
            continue
        qty = int(delta_value / prices[sym])
        if qty == 0:
            continue
        trades.append({
            "asset_class": cls,
            "symbol": sym,
            "action": "BUY" if qty > 0 else "SELL",
            "quantity": abs(qty),
            "est_notional": abs(qty) * prices[sym]
        })
    return trades