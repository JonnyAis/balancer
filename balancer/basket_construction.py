# filepath: balancer/basket_construction.py
"""
Basket construction via convex optimization (cvxpy).

Given a benchmark (VOO), a pool of candidate stocks, and external positions,
find weights for ~40 stocks that minimize tracking error subject to constraints.

Key design choices:
  - External positions enter as fixed constants. The optimizer minimizes tracking
    error of the *combined* portfolio (basket + external), adjusting only basket weights.
  - A "swappability" bonus biases the optimizer toward stocks that have viable
    same-sector swap partners for tax-loss harvesting. Without this, the optimizer
    picks idiosyncratic names that minimize TE but can never be harvested.
  - Sector constraints are applied to the basket's contribution only, with targets
    adjusted for what external positions already provide. This prevents infeasibility
    when external positions (like concentrated RSU holdings) dominate a sector.
"""

import math
from datetime import datetime

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
    """Convert external_positions config into {symbol: dollar_value}."""
    if today is None:
        today = datetime.now()

    external = {}
    for pos in config.get("external_positions", []):
        sym = pos["symbol"]
        shares = pos.get("shares", 0)
        anticipated = pos.get("anticipated_shares", 0)
        effective_date_str = pos.get("effective_date")

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
# Swappability scoring
# ---------------------------------------------------------------------------
def compute_swap_scores(
    candidate_symbols: list,
    correlation: pd.DataFrame,
    sector_map: dict,
    min_correlation: float = 0.85,
) -> np.ndarray:
    """
    For each candidate, compute a swappability score: sum of excess correlation
    above the threshold with same-sector peers. Normalized to [0, 1].
    """
    n = len(candidate_symbols)
    scores = np.zeros(n)

    for i, sym in enumerate(candidate_symbols):
        if sym not in correlation.index:
            continue
        sym_sector = sector_map.get(sym)
        if not sym_sector:
            continue

        for j, peer in enumerate(candidate_symbols):
            if i == j or peer not in correlation.index:
                continue
            if sector_map.get(peer) != sym_sector:
                continue

            corr = correlation.loc[sym, peer]
            if not np.isnan(corr) and corr >= min_correlation:
                scores[i] += (corr - min_correlation)

    max_score = scores.max()
    if max_score > 0:
        scores = scores / max_score
    return scores


# ---------------------------------------------------------------------------
# Sector target computation (external-aware)
# ---------------------------------------------------------------------------
def _compute_basket_sector_targets(
    valid_symbols: list,
    sector_map: dict,
    benchmark_sector_weights: dict,
    ext_weights_total: np.ndarray,
    basket_fraction: float,
) -> dict:
    """
    Compute what each sector's weight should be *within the basket*, given
    that external positions already provide some sector exposure.

    For each sector:
        total_target = benchmark_sector_weight (as fraction of total portfolio)
        external_contribution = sum of ext_weights_total for symbols in that sector
        basket_target = (total_target - external_contribution) / basket_fraction

    If externals overshoot (basket_target < 0), clamp to 0.

    Returns {sector: basket_target_fraction}
    """
    sectors = [sector_map.get(sym, "Unknown") for sym in valid_symbols]
    unique_sectors = sorted(set(sectors))

    # External exposure by sector
    ext_by_sector = {}
    for i, sym in enumerate(valid_symbols):
        sector = sectors[i]
        ext_by_sector[sector] = ext_by_sector.get(sector, 0.0) + ext_weights_total[i]

    targets = {}
    for sector in unique_sectors:
        bm_target = benchmark_sector_weights.get(sector, 0.0) / 100.0
        ext_exposure = ext_by_sector.get(sector, 0.0)
        # What the basket needs to contribute
        if basket_fraction > 0:
            basket_target = max(0.0, (bm_target - ext_exposure) / basket_fraction)
        else:
            basket_target = 0.0
        targets[sector] = basket_target

    return targets


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
    swap_scores: np.ndarray = None,
    swappability_weight: float = 0.001,
    solver: str = "CLARABEL",
    max_iterations: int = 10000,
) -> dict:
    """
    Find optimal basket weights minimizing tracking error to benchmark,
    with a bonus for TLH-swappable stocks and external-position-aware
    sector constraints.
    """
    external_values = external_values or {}

    # -----------------------------------------------------------------------
    # Build matrices
    # -----------------------------------------------------------------------
    valid_symbols = [s for s in candidate_symbols if s in covariance.index]
    if benchmark_symbol not in covariance.index:
        raise ValueError(f"Benchmark {benchmark_symbol} not in covariance matrix")

    n = len(valid_symbols)
    if n == 0:
        raise ValueError("No valid candidate symbols found in covariance matrix")

    Sigma = covariance.loc[valid_symbols, valid_symbols].values
    sigma_bm = covariance.loc[valid_symbols, benchmark_symbol].values
    var_bm = covariance.loc[benchmark_symbol, benchmark_symbol]

    # -----------------------------------------------------------------------
    # External exposure
    # -----------------------------------------------------------------------
    total_external = sum(external_values.values())
    total_portfolio = total_basket_value + total_external
    basket_fraction = total_basket_value / total_portfolio if total_portfolio > 0 else 1.0

    ext_weights_total = np.zeros(n)
    for i, sym in enumerate(valid_symbols):
        if sym in external_values:
            ext_weights_total[i] = external_values[sym] / total_portfolio

    # -----------------------------------------------------------------------
    # Benchmark target weights
    # -----------------------------------------------------------------------
    bm_raw = np.array([benchmark_weights.get(sym, 0.0) for sym in valid_symbols])
    bm_sum = bm_raw.sum()
    bm_target = bm_raw / bm_sum if bm_sum > 0 else np.ones(n) / n

    # -----------------------------------------------------------------------
    # Swappability scores (align to valid_symbols)
    # -----------------------------------------------------------------------
    if swap_scores is not None and len(swap_scores) == len(candidate_symbols):
        valid_swap_scores = np.array([
            swap_scores[candidate_symbols.index(sym)] if sym in candidate_symbols else 0.0
            for sym in valid_symbols
        ])
    else:
        valid_swap_scores = np.zeros(n)

    has_swap_bonus = swappability_weight > 0 and valid_swap_scores.max() > 0

    # -----------------------------------------------------------------------
    # cvxpy optimization
    # -----------------------------------------------------------------------
    w = cp.Variable(n, nonneg=True)
    w_combined = basket_fraction * w + ext_weights_total

    # Tracking error (quadratic)
    tracking_error = cp.quad_form(w_combined, Sigma, assume_PSD=True) - 2 * w_combined @ sigma_bm

    # Turnover penalty
    w_prev = np.array(current_weights) if current_weights is not None else np.zeros(n)
    turnover_cost = turnover_penalty * cp.norm1(w - w_prev)

    # Swappability bonus
    swap_bonus = swappability_weight * (w @ valid_swap_scores) if has_swap_bonus else 0.0

    objective = cp.Minimize(tracking_error + turnover_cost - swap_bonus)

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------
    constraints = [
        cp.sum(w) == 1.0,
        w >= 0,
        w <= max_weight_pct / 100,
    ]

    # Sector constraints: applied to basket weights (w), not combined weights.
    # Targets are adjusted for what external positions already provide.
    sectors = [sector_map.get(sym, "Unknown") for sym in valid_symbols]
    unique_sectors = sorted(set(sectors))

    basket_sector_targets = _compute_basket_sector_targets(
        valid_symbols, sector_map, benchmark_sector_weights,
        ext_weights_total, basket_fraction,
    )

    max_dev = sector_max_deviation_pct / 100.0
    sector_bounds = {}
    for sector in unique_sectors:
        target = basket_sector_targets.get(sector, 0.0)
        lower = max(0.0, target - max_dev)
        upper = min(1.0, target + max_dev)
        if upper >= lower:
            sector_bounds[sector] = (lower, upper)

    # If sum of lower bounds > 1.0, the constraints are collectively
    # overconstrained (e.g. large external positions reduce basket_fraction,
    # amplifying individual sector targets). Drop lower bounds in that case
    # so the optimizer can still enforce upper bounds (concentration caps).
    sum_lower = sum(lb for lb, _ in sector_bounds.values())
    enforce_lower = sum_lower <= 1.0

    sector_constraints = []
    for sector, (lower, upper) in sector_bounds.items():
        sector_mask = np.array([1.0 if sectors[i] == sector else 0.0 for i in range(n)])
        basket_sector_weight = sector_mask @ w
        if enforce_lower and lower > 0.0:
            sector_constraints.append(basket_sector_weight >= lower)
        sector_constraints.append(basket_sector_weight <= upper)

    if not enforce_lower:
        print(
            f"[Basket] Sector lower bounds sum={sum_lower:.2f} > 1.0 "
            "(large externals reduce basket_fraction) - dropping lower bounds"
        )

    constraints.extend(sector_constraints)

    # -----------------------------------------------------------------------
    # Solve
    # -----------------------------------------------------------------------
    problem = cp.Problem(objective, constraints)

    def _solve(prob, slvr, max_it):
        try:
            prob.solve(solver=slvr, max_iters=max_it, verbose=False)
        except TypeError:
            prob.solve(solver=slvr, verbose=False)

    try:
        _solve(problem, solver, max_iterations)
    except (cp.SolverError, Exception) as e:
        print(f"[Basket] {solver} failed ({e}), falling back to SCS")
        _solve(problem, "SCS", max_iterations)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        # Retry without sector constraints as last resort
        print(f"[Basket] Status '{problem.status}' - retrying without sector constraints")
        base_constraints = [c for c in constraints if c not in sector_constraints]
        problem2 = cp.Problem(objective, base_constraints)
        try:
            _solve(problem2, solver, max_iterations)
        except (cp.SolverError, Exception):
            _solve(problem2, "SCS", max_iterations)

        if problem2.status not in ("optimal", "optimal_inaccurate"):
            return {
                "weights": {}, "shares": {}, "tracking_error_bps": None,
                "sector_exposures": {}, "external_adjustment": {},
                "solver_status": problem2.status,
            }
        problem = problem2

    raw_weights = w.value

    # -----------------------------------------------------------------------
    # Post-process: zero out dust, enforce max_stocks
    # -----------------------------------------------------------------------
    min_w = min_weight_pct / 100.0
    cleaned = np.where(raw_weights >= min_w, raw_weights, 0.0)

    total_w = cleaned.sum()
    if total_w > 0:
        cleaned = cleaned / total_w
    else:
        return {
            "weights": {}, "shares": {}, "tracking_error_bps": None,
            "sector_exposures": {}, "external_adjustment": {},
            "solver_status": "all_weights_below_minimum",
        }

    nonzero_idx = np.where(cleaned > 0)[0]
    if len(nonzero_idx) > max_stocks:
        sorted_idx = nonzero_idx[np.argsort(cleaned[nonzero_idx])]
        to_remove = sorted_idx[: len(nonzero_idx) - max_stocks]
        cleaned[to_remove] = 0.0
        total_w = cleaned.sum()
        if total_w > 0:
            cleaned = cleaned / total_w

    # -----------------------------------------------------------------------
    # Compute outputs
    # -----------------------------------------------------------------------
    weights_dict = {}
    for i, sym in enumerate(valid_symbols):
        if cleaned[i] > 1e-6:
            weights_dict[sym] = round(float(cleaned[i]), 6)

    shares_dict = {}

    w_final = cleaned
    w_c_final = basket_fraction * w_final + ext_weights_total
    te_var = w_c_final @ Sigma @ w_c_final - 2 * w_c_final @ sigma_bm + var_bm
    te_annual = math.sqrt(max(0, te_var)) * 100
    te_bps = te_annual * 100

    # Sector exposures of combined portfolio
    sector_exposures = {}
    for sector in unique_sectors:
        sector_mask = np.array([1.0 if sectors[i] == sector else 0.0 for i in range(n)])
        combined_weight = float(sector_mask @ w_c_final) * 100
        sector_exposures[sector] = round(combined_weight, 2)

    # External adjustment detail
    ext_adj = {}
    for sym, val in external_values.items():
        if sym in valid_symbols:
            i = valid_symbols.index(sym)
            ext_adj[sym] = {
                "external_pct_of_total": round(ext_weights_total[i] * 100, 2),
                "basket_weight_pct": round(cleaned[i] * 100, 2),
                "benchmark_weight_pct": round(bm_target[i] * 100, 2),
            }

    # Swappability diagnostics
    basket_symbols = set(weights_dict.keys())
    swappable_count = sum(
        1 for sym in basket_symbols
        if valid_swap_scores[valid_symbols.index(sym)] > 0
    ) if has_swap_bonus else 0

    return {
        "weights": weights_dict,
        "shares": shares_dict,
        "tracking_error_bps": round(te_bps, 1),
        "sector_exposures": sector_exposures,
        "external_adjustment": ext_adj,
        "solver_status": problem.status,
        "num_positions": len(weights_dict),
        "num_swappable": swappable_count,
        "valid_symbols": valid_symbols,
        "cleaned_weights_array": cleaned,
    }


def weights_to_shares(weights: dict, total_value: float, prices: dict) -> dict:
    """Convert fractional weights to whole share counts (largest-remainder method)."""
    if not weights or total_value <= 0:
        return {}

    continuous = {}
    for sym, w in weights.items():
        px = prices.get(sym, 0)
        if px <= 0:
            continue
        continuous[sym] = (total_value * w) / px

    floored = {sym: int(math.floor(s)) for sym, s in continuous.items()}
    used = sum(floored[sym] * prices[sym] for sym in floored)
    remaining = total_value - used

    remainders = {sym: continuous[sym] - floored[sym] for sym in continuous}
    by_remainder = sorted(remainders.keys(), key=lambda s: remainders[s], reverse=True)

    for sym in by_remainder:
        px = prices.get(sym, 0)
        if px > 0 and remaining >= px:
            floored[sym] += 1
            remaining -= px

    return {sym: shares for sym, shares in floored.items() if shares > 0}


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
    swappable = result.get("num_swappable")
    if swappable is not None:
        lines.append(f"TLH-swappable: {swappable}/{result.get('num_positions', 0)}")
    te = result.get("tracking_error_bps")
    if te is not None:
        lines.append(f"Est. tracking error: {te:.0f} bps (annualized)")
    lines.append(f"Portfolio value: ${total_value:,.0f}")
    lines.append("")

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

    lines.append("")
    lines.append("Sector Exposures (combined portfolio):")
    for sector, pct in sorted(result.get("sector_exposures", {}).items(), key=lambda x: -x[1]):
        lines.append(f"  {sector:<30} {pct:>6.1f}%")

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
