# filepath: balancer/run_tlh_scan.py
"""
Standalone runner: basket construction + TLH scan.

Builds the basket, then scans for tax-loss harvesting opportunities
using synthetic lot data (until E*TRADE taxable integration is wired up).

Usage:
    python -m balancer.run_tlh_scan                 # Full pipeline
    python -m balancer.run_tlh_scan --refresh       # Force data re-download
    python -m balancer.run_tlh_scan --lots lots.json # Use real lot data from file

Lot file format (JSON):
    [
        {"symbol": "AAPL", "lot_id": "1", "shares": 10,
         "cost_basis_per_share": 195.00, "acquisition_date": "2025-08-15"},
        ...
    ]

If no lot file is provided, generates synthetic lots from the basket
with random cost bases to demonstrate the TLH workflow.
"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta

from .basket_construction import (
    format_basket_report,
    load_basket_config,
    optimize_basket,
    resolve_external_positions,
    weights_to_shares,
)
from .basket_data import (
    compute_benchmark_sector_weights,
    compute_covariance,
    compute_returns,
    fetch_prices,
    fetch_sp500_constituents,
)
from .swap_clusters import build_swap_clusters, format_cluster_report
from .tlh_scanner import format_tlh_report, scan_for_tlh


def _generate_synthetic_lots(
    basket_shares: dict,
    prices: dict,
    num_lots_per_stock: int = 2,
    loss_fraction: float = 0.4,
    seed: int = 42,
) -> list[dict]:
    """
    Create synthetic lots for testing. Some lots have gains, some have losses.

    Args:
        basket_shares: {symbol: num_shares} from basket construction.
        prices: {symbol: current_price}
        num_lots_per_stock: Split each position into this many lots.
        loss_fraction: Fraction of lots that will have unrealized losses.
        seed: Random seed for reproducibility.

    Returns:
        List of lot dicts.
    """
    rng = random.Random(seed)
    lots = []
    today = datetime.now()

    for sym, total_shares in basket_shares.items():
        if total_shares <= 0:
            continue
        price = prices.get(sym, 0)
        if price <= 0:
            continue

        # Split into lots
        shares_per_lot = max(1, total_shares // num_lots_per_stock)
        remaining = total_shares

        for lot_num in range(num_lots_per_stock):
            if remaining <= 0:
                break
            lot_shares = min(shares_per_lot, remaining)
            remaining -= lot_shares

            # Decide if this lot is a winner or loser
            is_loss = rng.random() < loss_fraction

            if is_loss:
                # Cost basis 5-25% above current price (unrealized loss)
                markup = rng.uniform(0.05, 0.25)
                cost_basis = round(price * (1 + markup), 2)
            else:
                # Cost basis 5-30% below current price (unrealized gain)
                discount = rng.uniform(0.05, 0.30)
                cost_basis = round(price * (1 - discount), 2)

            # Random acquisition date 30-400 days ago
            days_ago = rng.randint(30, 400)
            acq_date = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")

            lots.append({
                "symbol": sym,
                "lot_id": f"{sym}-lot{lot_num + 1}",
                "shares": lot_shares,
                "cost_basis_per_share": cost_basis,
                "acquisition_date": acq_date,
            })

    return lots


def _generate_synthetic_transactions(
    lots: list[dict],
    loss_fraction: float = 0.1,
    seed: int = 99,
) -> list[dict]:
    """
    Generate synthetic transaction history for wash sale checking.
    Mostly old transactions, with a small chance of recent buys.
    """
    rng = random.Random(seed)
    today = datetime.now()
    transactions = []

    symbols = list({lot["symbol"] for lot in lots})
    for sym in symbols:
        # Original buy (always older than 30 days)
        days_ago = rng.randint(35, 300)
        transactions.append({
            "symbol": sym,
            "action": "BUY",
            "date": (today - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            "shares": rng.randint(5, 50),
        })

        # Small chance of a recent buy (creates wash sale restriction)
        if rng.random() < loss_fraction:
            recent_days = rng.randint(1, 28)
            transactions.append({
                "symbol": sym,
                "action": "BUY",
                "date": (today - timedelta(days=recent_days)).strftime("%Y-%m-%d"),
                "shares": rng.randint(1, 10),
            })

    return transactions


def main():
    parser = argparse.ArgumentParser(description="Basket construction + TLH scan")
    parser.add_argument("--refresh", action="store_true", help="Force re-download data")
    parser.add_argument("--value", type=float, default=None, help="Override portfolio value")
    parser.add_argument("--config", type=str, default=None, help="Path to basket_config.yaml")
    parser.add_argument("--lots", type=str, default=None, help="Path to real lot data (JSON)")
    parser.add_argument("--top-n", type=int, default=None, help="Override candidate pool size")
    parser.add_argument("--clusters-only", action="store_true", help="Only print swap clusters")
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # 1. Load config + build basket (reuse run_basket logic)
    # -----------------------------------------------------------------------
    config = load_basket_config(args.config)
    basket_cfg = config.get("basket", {})
    benchmark_cfg = config.get("benchmark", {})
    opt_cfg = config.get("optimizer", {})
    tlh_cfg = config.get("tlh", {})

    benchmark_symbol = benchmark_cfg.get("symbol", "VOO")
    candidate_pool = args.top_n or basket_cfg.get("candidate_pool", 80)
    lookback_days = basket_cfg.get("lookback_days", 252)
    total_value = args.value or config.get("taxable_account_value", 200_000)

    print(f"[TLH] Benchmark: {benchmark_symbol}")
    print(f"[TLH] Portfolio value: ${total_value:,.0f}")
    print()

    # Fetch data
    constituents = fetch_sp500_constituents(top_n=candidate_pool, refresh=args.refresh)
    candidate_symbols = constituents["symbol"].tolist()
    sector_map = dict(zip(constituents["symbol"], constituents["sector"]))
    benchmark_weights = dict(zip(constituents["symbol"], constituents["weight"]))
    benchmark_sector_weights = compute_benchmark_sector_weights(constituents)

    prices_df = fetch_prices(
        candidate_symbols, benchmark=benchmark_symbol,
        lookback_days=lookback_days, refresh=args.refresh,
    )
    available = [s for s in candidate_symbols if s in prices_df.columns]
    candidate_symbols = available

    returns = compute_returns(prices_df)
    covariance = compute_covariance(returns, annualize=True)
    latest_prices = prices_df.iloc[-1].to_dict()

    # -----------------------------------------------------------------------
    # 2. Build swap clusters
    # -----------------------------------------------------------------------
    print("[TLH] Computing swap clusters...")
    correlation = returns.corr()
    clusters = build_swap_clusters(
        correlation=correlation,
        sector_map=sector_map,
        min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
        cluster_size=tlh_cfg.get("swap_cluster_size", 4),
        candidate_symbols=candidate_symbols,
    )

    # Stats
    with_swaps = sum(1 for v in clusters.values() if v)
    print(f"[TLH] {len(clusters)} stocks analyzed, {with_swaps} have valid swap candidates")

    if args.clusters_only:
        print()
        print(format_cluster_report(clusters, sector_map))
        return 0

    # -----------------------------------------------------------------------
    # 3. Build basket
    # -----------------------------------------------------------------------
    print("[TLH] Running basket optimizer...")
    external_values = resolve_external_positions(config, latest_prices)
    if external_values:
        print(f"[TLH] External positions: {', '.join(f'{s}=${v:,.0f}' for s, v in external_values.items())}")

    result = optimize_basket(
        candidate_symbols=candidate_symbols,
        benchmark_symbol=benchmark_symbol,
        covariance=covariance,
        returns=returns,
        benchmark_weights=benchmark_weights,
        sector_map=sector_map,
        benchmark_sector_weights=benchmark_sector_weights,
        external_values=external_values,
        total_basket_value=total_value,
        current_weights=None,
        min_weight_pct=basket_cfg.get("min_weight_pct", 0.5),
        max_weight_pct=basket_cfg.get("max_weight_pct", 8.0),
        max_stocks=basket_cfg.get("max_stocks", 40),
        turnover_penalty=basket_cfg.get("turnover_penalty", 0.005),
        sector_max_deviation_pct=basket_cfg.get("sector_max_deviation_pct", 5.0),
        solver=opt_cfg.get("solver", "CLARABEL"),
        max_iterations=opt_cfg.get("max_iterations", 10000),
    )

    if result["solver_status"] not in ("optimal", "optimal_inaccurate"):
        print(f"[TLH] Basket optimizer failed: {result['solver_status']}")
        return 1

    basket_shares = weights_to_shares(result["weights"], total_value, latest_prices)
    result["shares"] = basket_shares

    print(f"[TLH] Basket: {len(basket_shares)} positions")
    print()

    # -----------------------------------------------------------------------
    # 4. Load or generate lots
    # -----------------------------------------------------------------------
    if args.lots:
        print(f"[TLH] Loading lots from {args.lots}")
        with open(args.lots, encoding="utf-8") as f:
            lots = json.load(f)
        transactions = []  # No synthetic transactions for real data
        print(f"[TLH] Loaded {len(lots)} lots")
    else:
        print("[TLH] Generating synthetic lots for demo (use --lots for real data)")
        lots = _generate_synthetic_lots(basket_shares, latest_prices)
        transactions = _generate_synthetic_transactions(lots)
        print(f"[TLH] Generated {len(lots)} synthetic lots, {len(transactions)} transactions")

    print()

    # -----------------------------------------------------------------------
    # 5. Run TLH scan
    # -----------------------------------------------------------------------
    restricted = set(config.get("restricted_symbols", []) or [])
    if restricted:
        print(f"[TLH] Restricted symbols: {restricted}")

    proposals = scan_for_tlh(
        lots=lots,
        prices=latest_prices,
        transaction_history=transactions,
        swap_clusters=clusters,
        restricted_symbols=restricted,
        min_loss=tlh_cfg.get("min_loss_to_harvest", 200),
        wash_sale_window_days=tlh_cfg.get("wash_sale_window_days", 30),
        min_holding_days=tlh_cfg.get("min_holding_days", 2),
        min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
        tax_rate_short=tlh_cfg.get("tax_rate_short_term", 0.37),
        tax_rate_long=tlh_cfg.get("tax_rate_long_term", 0.20),
        state_tax_rate=tlh_cfg.get("state_tax_rate", 0.0),
    )

    # -----------------------------------------------------------------------
    # 6. Print results
    # -----------------------------------------------------------------------
    print(format_basket_report(result, latest_prices, total_value))
    print()
    print(format_tlh_report(proposals, latest_prices))

    if proposals:
        # Summary
        total_loss = sum(p.harvested_loss for p in proposals)
        total_savings = sum(p.estimated_tax_savings for p in proposals)
        pct_of_portfolio = abs(total_loss) / total_value * 100

        print("\nTLH Summary:")
        print(f"  Harvestable loss:    ${abs(total_loss):>10,.0f} ({pct_of_portfolio:.1f}% of portfolio)")
        print(f"  Est. tax savings:    ${total_savings:>10,.0f}")
        print(f"  Swap trades needed:  {len(proposals)}")

        # Show which stocks would change
        sells = {p.sell_symbol for p in proposals}
        buys = {p.buy_symbol for p in proposals}
        print(f"  Selling: {', '.join(sorted(sells))}")
        print(f"  Buying:  {', '.join(sorted(buys))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
