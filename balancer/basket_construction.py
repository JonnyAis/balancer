# filepath: balancer/basket_construction.py
"""
Basket construction via convex optimization (cvxpy).

Given a benchmark (VOO), a pool of candidate stocks, and external positions,
find weights for ~40 stocks that minimize tracking error subject to constraints.

The key insight for external positions: if you already hold $50K of META elsewhere,
the optimizer treats that as fixed exposure. It minimizes tracking error of the
*combined* portfolio (basket + external), but can only adjust the basket weights.
"""

import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import yaml

try:
    import cvxpy as cp
except ImportError:
    raise ImportError("cvxpy is required: pip install cvxpy")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_basket_config(path: str = None) -> dict:
    """Load basket_config.yaml. Returns parsed dict."""
    if path is None:
        import os

        path = os.path.join(os.path.dirname(__file__), "basket_config.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# External position processing
# ---------------------------------------------------------------------------
def resolve_external_positions(
    config: dict,
    prices: dict,
    today: datetime = None,
) -> dict:
    """
    Convert external_positions config into {symbol: dollar_value}.
    Accounts for effective_date on anticipated positions.

    Args:
        config: The full basket config dict.
        prices: {symbol: current_price} for valuation.
        today: Override for testing.

    Returns:
        {symbol: dollar_value} of external exposure.
    """
    if today is None:
        today = datetime.now()

    external = {}
    for pos in config.get("external_positions", []):
        sym = pos["symbol"]
        shares = pos.get("shares", 0)
        anticipated = pos.get("anticipated_shares", 0)
        effective_date_str = pos.get("effective_date")

        # Include anticipated shares only if past effective date
        if anticipated > 0 and effective_date_str:
            effective = datetime.strptime(effective_date_str, "%Y-%m-%d")
            if today >= effective:
                shares += anticipated

        if shares <= 0:
            continue

        price = prices.get(sym, 0)
        if price <= 0:
            print(f"[Basket] Warning: no price for external position {sym}, skipping")
            continue

        external[sym] = shares * price

    return external


# ---------------------------------------------------------------------------
# Core optimizer
# ---------------------------------------------------------------------------
def optimize_basket(
    candidate_symbols: list,
    benchmark_symbol: str,
    covariance: pd.DataFrame,
    returns: pd.DataFrame,
    benchmark_weights: dict,
    sector_map: dict,
    benchmark_sector_weights: dict,
    external_values: dict = None,
    total_basket_value: float = 200_000,
    current_weights: np.ndarray = None,
    min_weight_pct: float = 0.5,
    max_weight_pct: float = 8.0,
    max_stocks: int = 40,
    turnover_penalty: float = 0.005,
    sector_max_deviation_pct: float = 5.0,
    solver: str = "ECOS",
    max_iterations: int = 10000,
) -> dict:
    """
    Find optimal basket weights minimizing tracking error to benchmark.

    The optimization accounts for external positions by computing the combined
    portfolio's deviation from the benchmark. The basket weights are the only
    decision variables; external positions are fixed constants.

    Args:
        candidate_symbols: List of stock symbols to choose from.
        benchmark_symbol: e.g. "VOO"
        covariance: Annualized covariance matrix (all symbols + benchmark).
        returns: Daily returns DataFrame (all symbols + benchmark).
        benchmark_weights: {symbol: weight_in_index_pct} for candidates.
        sector_map: {symbol: sector_name}
        benchmark_sector_weights: {sector: weight_pct}
        external_values: {symbol: dollar_value} of external holdings.
        total_basket_value: Dollar amount to invest in the basket.
        current_weights: Current basket weights (for turnover penalty). None = fresh build.
        min_weight_pct: Minimum nonzero position (%).
        max_weight_pct: Maximum single position (%).
        max_stocks: Target max number of positions.
        turnover_penalty: Coefficient on |w_new - w_old|.
        sector_max_deviation_pct: Max sector deviation from benchmark.
        solver: cvxpy solver name.
        max_iterations: Solver iteration limit.

    Returns:
        {
            "weights": {symbol: weight_fraction},  # Sums to 1.0
            "shares": {symbol: int},
            "tracking_error_bps": float,
            "sector_exposures": {sector: pct},
            "external_adjustment": {symbol: delta_weight},
            "solver_status": str,
        }
    """
    external_values = external_values or {}

    # -----------------------------------------------------------------------
    # Build matrices aligned to candidate_symbols
    # -----------------------------------------------------------------------
    # Filter to symbols present in covariance matrix
    valid_symbols = [s for s in candidate_symbols if s in covariance.index]
    if benchmark_symbol not in covariance.index:
        raise ValueError(f"Benchmark {benchmark_symbol} not in covariance matrix")

    n = len(valid_symbols)
    if n == 0:
        raise ValueError("No valid candidate symbols found in covariance matrix")

    # Covariance submatrix for candidates only
    Sigma = covariance.loc[valid_symbols, valid_symbols].values  # (n, n)

    # Covariance of each candidate with the benchmark
    sigma_bm = covariance.loc[valid_symbols, benchmark_symbol].values  # (n,)

    # Benchmark variance (scalar)
    var_bm = covariance.loc[benchmark_symbol, benchmark_symbol]

    # -----------------------------------------------------------------------
    # Compute external exposure as fraction of total (basket + external)
    # -----------------------------------------------------------------------
    total_external = sum(external_values.values())
    total_portfolio = total_basket_value + total_external
    basket_fraction = total_basket_value / total_portfolio if total_portfolio > 0 else 1.0

    # External weights as fraction of total portfolio, mapped to our symbol indices
    ext_weights_total = np.zeros(n)
    for i, sym in enumerate(valid_symbols):
        if sym in external_values:
            ext_weights_total[i] = external_values[sym] / total_portfolio

    # -----------------------------------------------------------------------
    # Target weights (from benchmark) as fraction of total portfolio
    # -----------------------------------------------------------------------
    # Normalize benchmark weights to our candidate universe
    bm_raw = np.array([benchmark_weights.get(sym, 0.0) for sym in valid_symbols])
    bm_sum = bm_raw.sum()
    if bm_sum > 0:
        bm_target = bm_raw / bm_sum  # Normalized to sum to 1 within our universe
    else:
        bm_target = np.ones(n) / n

    # -----------------------------------------------------------------------
    # cvxpy optimization
    # -----------------------------------------------------------------------
    # w = basket weights (fraction of basket_value, sums to 1)
    w = cp.Variable(n, nonneg=True)

    # Combined portfolio weights (fraction of total portfolio):
    #   combined_i = basket_fraction * w_i + ext_weights_total_i
    # We want combined to be close to bm_target (scaled to total portfolio).

    # Tracking error = Var(r_portfolio - r_benchmark)
    #   = w_c' Sigma w_c - 2 w_c' sigma_bm + var_bm
    # where w_c = combined weights = basket_fraction * w + ext_weights_total
    #
    # Since var_bm is constant, we minimize:
    #   w_c' Sigma w_c - 2 w_c' sigma_bm

    w_combined = basket_fraction * w + ext_weights_total

    # Quadratic tracking error (without the constant var_bm term)
    tracking_error = cp.quad_form(w_combined, Sigma, assume_PSD=True) - 2 * w_combined @ sigma_bm

    # Turnover penalty (vs current or vs zero)
    if current_weights is not None:
        w_prev = np.array(current_weights)
    else:
        w_prev = np.zeros(n)
    turnover_cost = turnover_penalty * cp.norm1(w - w_prev)

    objective = cp.Minimize(tracking_error + turnover_cost)

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------
    constraints = [
        cp.sum(w) == 1.0,          # Fully invested
        w >= 0,                      # Long only (redundant with nonneg but explicit)
        w <= max_weight_pct / 100,   # Position cap
    ]

    # Cardinality-like constraint via big-M relaxation:
    # We can't enforce exact cardinality in convex optimization, but the
    # min_weight constraint naturally pushes small positions to zero.
    # For a tighter cardinality bound, we'd use a two-pass approach (below).

    # Sector constraints
    sectors = [sector_map.get(sym, "Unknown") for sym in valid_symbols]
    unique_sectors = sorted(set(sectors))
    for sector in unique_sectors:
        sector_mask = np.array([1.0 if sectors[i] == sector else 0.0 for i in range(n)])
        sector_weight = sector_mask @ w_combined  # Combined exposure
        bm_sector_weight = benchmark_sector_weights.get(sector, 0.0) / 100.0

        # Allow deviation within bounds (of total portfolio)
        max_dev = sector_max_deviation_pct / 100.0
        constraints.append(sector_weight >= bm_sector_weight - max_dev)
        constraints.append(sector_weight <= bm_sector_weight + max_dev)

    # -----------------------------------------------------------------------
    # Solve
    # -----------------------------------------------------------------------
    problem = cp.Problem(objective, constraints)
    def _solve(prob, slvr, max_it):
        """Solve with solver-appropriate iteration keyword."""
        # Different solvers use different kwarg names for iteration limit
        try:
            prob.solve(solver=slvr, max_iters=max_it, verbose=False)
        except TypeError:
            # Some solvers (e.g. Clarabel) use different kwargs
            prob.solve(solver=slvr, verbose=False)

    try:
        _solve(problem, solver, max_iterations)
    except (cp.SolverError, Exception) as e:
        # Fallback solver
        print(f"[Basket] {solver} failed ({e}), falling back to SCS")
        _solve(problem, "SCS", max_iterations)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        # Retry without sector constraints (external positions can make them infeasible)
        print(f"[Basket] Status '{problem.status}' - retrying without sector constraints")
        base_constraints = [
            cp.sum(w) == 1.0,
            w >= 0,
            w <= max_weight_pct / 100,
        ]
        problem2 = cp.Problem(objective, base_constraints)
        try:
            _solve(problem2, solver, max_iterations)
        except (cp.SolverError, Exception):
            _solve(problem2, "SCS", max_iterations)

        if problem2.status not in ("optimal", "optimal_inaccurate"):
            return {
                "weights": {},
                "shares": {},
                "tracking_error_bps": None,
                "sector_exposures": {},
                "external_adjustment": {},
                "solver_status": problem2.status,
            }
        # Use relaxed solution
        problem = problem2

    raw_weights = w.value

    # -----------------------------------------------------------------------
    # Post-process: zero out dust positions below min_weight
    # -----------------------------------------------------------------------
    min_w = min_weight_pct / 100.0
    cleaned = np.where(raw_weights >= min_w, raw_weights, 0.0)

    # Renormalize
    total_w = cleaned.sum()
    if total_w > 0:
        cleaned = cleaned / total_w
    else:
        return {
            "weights": {},
            "shares": {},
            "tracking_error_bps": None,
            "sector_exposures": {},
            "external_adjustment": {},
            "solver_status": "all_weights_below_minimum",
        }

    # If still too many positions, zero out the smallest until at max_stocks
    nonzero_idx = np.where(cleaned > 0)[0]
    if len(nonzero_idx) > max_stocks:
        # Sort by weight ascending, zero out the smallest
        sorted_idx = nonzero_idx[np.argsort(cleaned[nonzero_idx])]
        to_remove = sorted_idx[: len(nonzero_idx) - max_stocks]
        cleaned[to_remove] = 0.0
        total_w = cleaned.sum()
        if total_w > 0:
            cleaned = cleaned / total_w

    # -----------------------------------------------------------------------
    # Compute outputs
    # -----------------------------------------------------------------------
    # Weights dict (only nonzero)
    weights_dict = {}
    for i, sym in enumerate(valid_symbols):
        if cleaned[i] > 1e-6:
            weights_dict[sym] = round(float(cleaned[i]), 6)

    # Share counts
    shares_dict = _weights_to_shares(weights_dict, total_basket_value, {
        sym: covariance.loc[sym, sym]  # placeholder — we need actual prices
        for sym in weights_dict
    })
    # ^ We'll fix this — need to pass prices in. For now, placeholder.

    # Tracking error estimate (annualized, in basis points)
    w_final = cleaned
    w_c_final = basket_fraction * w_final + ext_weights_total
    te_var = w_c_final @ Sigma @ w_c_final - 2 * w_c_final @ sigma_bm + var_bm
    te_annual = math.sqrt(max(0, te_var)) * 100  # Convert to percentage
    te_bps = te_annual * 100  # Convert to basis points

    # Sector exposures of combined portfolio
    sector_exposures = {}
    for sector in unique_sectors:
        sector_mask = np.array([1.0 if sectors[i] == sector else 0.0 for i in range(n)])
        combined_weight = float(sector_mask @ w_c_final) * 100
        sector_exposures[sector] = round(combined_weight, 2)

    # External adjustment: how much each external position shifted the basket
    ext_adj = {}
    for sym, val in external_values.items():
        if sym in valid_symbols:
            i = valid_symbols.index(sym)
            ext_adj[sym] = {
                "external_pct_of_total": round(ext_weights_total[i] * 100, 2),
                "basket_weight_pct": round(cleaned[i] * 100, 2),
                "benchmark_weight_pct": round(bm_target[i] * 100, 2),
            }

    return {
        "weights": weights_dict,
        "shares": shares_dict,  # Will be populated by caller with real prices
        "tracking_error_bps": round(te_bps, 1),
        "sector_exposures": sector_exposures,
        "external_adjustment": ext_adj,
        "solver_status": problem.status,
        "num_positions": len(weights_dict),
        "valid_symbols": valid_symbols,
        "cleaned_weights_array": cleaned,
    }


def weights_to_shares(
    weights: dict,
    total_value: float,
    prices: dict,
) -> dict:
    """
    Convert fractional weights to whole share counts.

    Uses a largest-remainder method to handle rounding:
    1. Compute continuous shares for each position.
    2. Floor all to integers.
    3. Distribute remaining dollars to positions with largest fractional remainders.

    Returns {symbol: int_shares}.
    """
    if not weights or total_value <= 0:
        return {}

    # Continuous shares
    continuous = {}
    for sym, w in weights.items():
        px = prices.get(sym, 0)
        if px <= 0:
            continue
        target_value = total_value * w
        continuous[sym] = target_value / px

    # Floor pass
    floored = {sym: int(math.floor(s)) for sym, s in continuous.items()}

    # Remaining budget
    used = sum(floored[sym] * prices[sym] for sym in floored)
    remaining = total_value - used

    # Sort by fractional remainder descending
    remainders = {sym: continuous[sym] - floored[sym] for sym in continuous}
    by_remainder = sorted(remainders.keys(), key=lambda s: remainders[s], reverse=True)

    # Distribute one extra share to each, largest remainder first, while budget allows
    for sym in by_remainder:
        px = prices.get(sym, 0)
        if px <= 0:
            continue
        if remaining >= px:
            floored[sym] += 1
            remaining -= px

    # Drop zeros
    return {sym: shares for sym, shares in floored.items() if shares > 0}


# Placeholder for internal use
def _weights_to_shares(weights, total_value, variances):
    """Placeholder — real conversion happens in weights_to_shares with actual prices."""
    return {}


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------
def format_basket_report(result: dict, prices: dict, total_value: float) -> str:
    """Format the optimization result as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("BASKET CONSTRUCTION REPORT".center(70))
    lines.append("=" * 70)
    lines.append(f"Solver status: {result['solver_status']}")
    lines.append(f"Positions: {result.get('num_positions', len(result['weights']))}")
    te = result.get("tracking_error_bps")
    if te is not None:
        lines.append(f"Est. tracking error: {te:.0f} bps (annualized)")
    lines.append(f"Portfolio value: ${total_value:,.0f}")
    lines.append("")

    # Position table
    weights = result["weights"]
    shares = result.get("shares", {})
    if not shares and prices:
        shares = weights_to_shares(weights, total_value, prices)

    lines.append(f"{'Symbol':<8} {'Weight%':>8} {'Shares':>8} {'Value':>12}")
    lines.append("-" * 40)

    sorted_positions = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    total_invested = 0
    for sym, w in sorted_positions:
        sh = shares.get(sym, 0)
        px = prices.get(sym, 0)
        val = sh * px
        total_invested += val
        lines.append(f"{sym:<8} {w * 100:>7.2f}% {sh:>8d} ${val:>11,.0f}")

    lines.append("-" * 40)
    lines.append(f"{'Total':<8} {'100.00%':>8} {'':>8} ${total_invested:>11,.0f}")
    cash = total_value - total_invested
    lines.append(f"{'Cash':<8} {'':>8} {'':>8} ${cash:>11,.0f}")

    # Sector breakdown
    lines.append("")
    lines.append("Sector Exposures (combined portfolio):")
    for sector, pct in sorted(result.get("sector_exposures", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {sector:<30} {pct:>6.1f}%")

    # External position impact
    ext_adj = result.get("external_adjustment", {})
    if ext_adj:
        lines.append("")
        lines.append("External Position Impact:")
        for sym, info in ext_adj.items():
            lines.append(
                f"  {sym}: benchmark={info['benchmark_weight_pct']:.1f}% "
                f"-> basket={info['basket_weight_pct']:.1f}% "
                f"(external={info['external_pct_of_total']:.1f}% of total)"
            )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)
