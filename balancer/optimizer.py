import math
from itertools import product


def optimize_integer_portfolio(
    targets_pct: dict,
    prices: dict,
    class_to_symbol: dict,
    current_shares: dict,
    total_account_value: float,
    window: int = 4,
    turnover_penalty: float = 0.0,
    min_notional_trade: float = 0.0,
    account_min_turnover: float = 0.0,
    cash_buffer: float = 0.0,
):
    """
    Enumerate whole-share allocations near target to minimize:
        objective = sum( (value_c - target_value_c)^2 ) + turnover_penalty * sum(|delta_shares_c|)
    Constraints:
      sum(value_c) <= total_account_value - cash_buffer
      shares_c >= 0
    Returns list of trade dicts (BUY/SELL), or [] if no action passes turnover/min_notional filters.

    Args:
      targets_pct: {Class: percent (0-100)}
      prices: {symbol: last_price}
      class_to_symbol: {Class: preferred_symbol}
      current_shares: {symbol: int}
      total_account_value: invested + cash
      window: integer share search +/- around continuous target shares
      turnover_penalty: adds cost per absolute share change (stability)
      min_notional_trade: per-trade minimum dollar size
      account_min_turnover: minimum aggregate turnover or return []
      cash_buffer: reserve to leave uninvested
    """
    classes = list(targets_pct.keys())
    # Build target values and continuous target shares
    target_values = {}
    target_shares_cont = {}
    for cls in classes:
        pct = targets_pct[cls]
        sym = class_to_symbol.get(cls)
        if sym not in prices or prices[sym] <= 0:
            return []  # missing price → bail (fallback later if desired)
        tv = total_account_value * (pct / 100.0)
        target_values[cls] = tv
        target_shares_cont[cls] = tv / prices[sym]

    # Candidate share ranges per class
    share_ranges = {}
    for cls in classes:
        tgt = target_shares_cont[cls]
        base = math.floor(tgt)
        low = max(0, base - window)
        high = base + window
        # Ensure current shares are within range (add if outside)
        sym = class_to_symbol[cls]
        curr = int(current_shares.get(sym, 0))
        low = min(low, curr)
        high = max(high, curr)
        share_ranges[cls] = range(low, high + 1)

    best = None
    best_obj = float("inf")
    budget = max(0.0, total_account_value - cash_buffer)

    # Pre-fetch prices per class symbol
    symbol_prices = {class_to_symbol[c]: prices[class_to_symbol[c]] for c in classes}
    current_class_shares = {
        cls: int(current_shares.get(class_to_symbol[cls], 0)) for cls in classes
    }

    # Enumerate Cartesian product
    ranges = [share_ranges[c] for c in classes]
    for combo in product(*ranges):
        alloc_shares = dict(zip(classes, combo))
        total_val = 0.0
        class_values = {}
        for cls in classes:
            val = alloc_shares[cls] * symbol_prices[class_to_symbol[cls]]
            class_values[cls] = val
            total_val += val
            if total_val > budget:  # early prune
                break
        if total_val > budget:
            continue

        # Objective
        sq_err = 0.0
        turnover = 0
        for cls in classes:
            diff_val = class_values[cls] - target_values[cls]
            sq_err += diff_val * diff_val
            turnover += abs(alloc_shares[cls] - current_class_shares[cls])
        obj = sq_err + turnover_penalty * turnover
        if obj < best_obj:
            best_obj = obj
            best = alloc_shares

    if best is None:
        return []

    # Build trades from delta
    trades = []
    total_turnover_notional = 0.0
    for cls in classes:
        sym = class_to_symbol[cls]
        price = symbol_prices[sym]
        curr = current_class_shares[cls]
        new = best[cls]
        delta = new - curr
        if delta == 0:
            continue
        action = "BUY" if delta > 0 else "SELL"
        qty = abs(delta)
        notional = round(qty * price, 2)
        if notional < min_notional_trade:
            continue
        trades.append(
            {
                "asset_class": cls,
                "symbol": sym,
                "action": action,
                "quantity": qty,
                "est_notional": notional,
                "delta_shares": delta,
            }
        )
        total_turnover_notional += notional

    if total_turnover_notional < account_min_turnover:
        return []

    return trades
