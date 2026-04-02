# filepath: balancer/paper_trader.py
"""
Forward paper trading for basket + TLH strategy.

Runs against live market data and records what trades *would* happen,
without touching E*TRADE. Maintains portfolio state in a JSON file
at ~/.balancer/paper_portfolio.json.

Designed to be run daily or weekly (e.g., via Task Scheduler or cron).

Usage:
    python -m balancer.paper_trader                 # Run one cycle
    python -m balancer.paper_trader --init          # Initialize a new paper portfolio
    python -m balancer.paper_trader --status        # Show current status
    python -m balancer.paper_trader --report        # Generate HTML report from history
"""

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime

from .backtest_report import generate_html_report
from .basket_construction import (
    compute_swap_scores,
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
from .swap_clusters import build_swap_clusters
from .tlh_scanner import scan_for_tlh

_STATE_DIR = os.path.join(os.path.expanduser("~"), ".balancer")
_PORTFOLIO_PATH = os.path.join(_STATE_DIR, "paper_portfolio.json")
_HISTORY_PATH = os.path.join(_STATE_DIR, "paper_history.jsonl")


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    """Load paper portfolio state from disk."""
    if not os.path.exists(_PORTFOLIO_PATH):
        return None
    with open(_PORTFOLIO_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: dict):
    """Save paper portfolio state to disk."""
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def _append_history(entry: dict):
    """Append a snapshot to the paper trading history."""
    os.makedirs(_STATE_DIR, exist_ok=True)
    with open(_HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_history() -> list[dict]:
    """Load all paper trading history snapshots."""
    if not os.path.exists(_HISTORY_PATH):
        return []
    entries = []
    with open(_HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Initialize
# ---------------------------------------------------------------------------
def init_paper_portfolio(config: dict, refresh: bool = False) -> dict:
    """
    Build an initial basket and create the paper portfolio state file.
    """
    basket_cfg = config.get("basket", {})
    benchmark_cfg = config.get("benchmark", {})
    opt_cfg = config.get("optimizer", {})
    tlh_cfg = config.get("tlh", {})

    benchmark_symbol = benchmark_cfg.get("symbol", "VOO")
    candidate_pool = basket_cfg.get("candidate_pool", 150)
    lookback_days = basket_cfg.get("lookback_days", 252)
    total_value = config.get("taxable_account_value", 200_000)

    # Fetch data
    constituents = fetch_sp500_constituents(top_n=candidate_pool, refresh=refresh)
    candidate_symbols = constituents["symbol"].tolist()
    sector_map = dict(zip(constituents["symbol"], constituents["sector"]))
    benchmark_weights = dict(zip(constituents["symbol"], constituents["weight"]))
    benchmark_sector_weights = compute_benchmark_sector_weights(constituents)

    prices_df = fetch_prices(
        candidate_symbols, benchmark=benchmark_symbol,
        lookback_days=lookback_days, refresh=refresh,
    )
    available = [s for s in candidate_symbols if s in prices_df.columns]

    returns = compute_returns(prices_df)
    covariance = compute_covariance(returns, annualize=True)
    latest_prices = prices_df.iloc[-1].to_dict()
    external_values = resolve_external_positions(config, latest_prices)

    # Swap scores — bias optimizer toward TLH-swappable stocks
    min_corr = tlh_cfg.get("min_correlation_for_swap", 0.85)
    correlation = returns.corr()
    swap_scores = compute_swap_scores(
        available, correlation, sector_map, min_correlation=min_corr,
    )
    swappability_weight = basket_cfg.get("swappability_weight", 0.0002)

    # Optimize
    result = optimize_basket(
        candidate_symbols=available,
        benchmark_symbol=benchmark_symbol,
        covariance=covariance,
        returns=returns,
        benchmark_weights=benchmark_weights,
        sector_map=sector_map,
        benchmark_sector_weights=benchmark_sector_weights,
        external_values=external_values,
        total_basket_value=total_value,
        min_weight_pct=basket_cfg.get("min_weight_pct", 0.5),
        max_weight_pct=basket_cfg.get("max_weight_pct", 8.0),
        max_stocks=basket_cfg.get("max_stocks", 40),
        turnover_penalty=basket_cfg.get("turnover_penalty", 0.005),
        sector_max_deviation_pct=basket_cfg.get("sector_max_deviation_pct", 5.0),
        swap_scores=swap_scores,
        swappability_weight=swappability_weight,
        solver=opt_cfg.get("solver", "CLARABEL"),
    )

    if result["solver_status"] not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Optimizer failed: {result['solver_status']}")

    shares = weights_to_shares(result["weights"], total_value, latest_prices)

    # Build state
    today = datetime.now()
    lots = []
    lot_counter = 0
    cash = total_value
    for sym, num_shares in shares.items():
        px = latest_prices.get(sym, 0)
        if px > 0 and num_shares > 0:
            lot_counter += 1
            lots.append({
                "symbol": sym,
                "lot_id": f"paper-{lot_counter}",
                "shares": num_shares,
                "cost_basis_per_share": round(px, 2),
                "acquisition_date": today.strftime("%Y-%m-%d"),
            })
            cash -= num_shares * px

    benchmark_price = latest_prices.get(benchmark_symbol, 0)

    state = {
        "created": today.isoformat(),
        "updated": today.isoformat(),
        "initial_value": total_value,
        "benchmark_symbol": benchmark_symbol,
        "benchmark_start_price": benchmark_price,
        "lots": lots,
        "cash": round(cash, 2),
        "lot_counter": lot_counter,
        "transaction_history": [],
        "cumulative_harvested_loss": 0.0,
        "cumulative_tax_savings": 0.0,
        "cumulative_trades": 0,
        "cumulative_turnover": 0.0,
    }

    _save_state(state)

    # Record initial snapshot
    port_value = sum(
        lot["shares"] * latest_prices.get(lot["symbol"], 0) for lot in lots
    ) + cash
    _append_history({
        "date": today.isoformat(),
        "portfolio_value": round(port_value, 2),
        "benchmark_value": total_value,
        "cash": round(cash, 2),
        "num_positions": len(shares),
        "tracking_error_bps": 0,
        "cumulative_harvested_loss": 0,
        "cumulative_tax_savings": 0,
        "step_trades": 0,
        "step_turnover": 0,
        "cumulative_trades": 0,
        "cumulative_turnover": 0,
    })

    print(f"[PaperTrader] Initialized with {len(shares)} positions, "
          f"${port_value:,.0f} invested, ${cash:,.0f} cash")
    return state


# ---------------------------------------------------------------------------
# Run one cycle
# ---------------------------------------------------------------------------
def run_cycle(config: dict, refresh: bool = False):
    """Run one paper trading cycle: update prices, scan for TLH, record state."""
    state = _load_state()
    if state is None:
        print("[PaperTrader] No portfolio found. Run with --init first.")
        return

    basket_cfg = config.get("basket", {})
    tlh_cfg = config.get("tlh", {})

    benchmark_symbol = state["benchmark_symbol"]
    candidate_pool = basket_cfg.get("candidate_pool", 80)
    lookback_days = basket_cfg.get("lookback_days", 252)

    # Fetch fresh data
    constituents = fetch_sp500_constituents(top_n=candidate_pool, refresh=refresh)
    candidate_symbols = [s for s in constituents["symbol"].tolist()]
    sector_map = dict(zip(constituents["symbol"], constituents["sector"]))

    prices_df = fetch_prices(
        candidate_symbols, benchmark=benchmark_symbol,
        lookback_days=lookback_days, refresh=True,  # Always fresh for paper trading
    )
    latest_prices = prices_df.iloc[-1].to_dict()
    returns = compute_returns(prices_df)
    correlation = returns.corr()

    # Swap clusters
    available = [s for s in candidate_symbols if s in correlation.index]
    clusters = build_swap_clusters(
        correlation=correlation,
        sector_map=sector_map,
        min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
        cluster_size=tlh_cfg.get("swap_cluster_size", 4),
        candidate_symbols=available,
    )

    # Run TLH scan
    restricted = set(config.get("restricted_symbols", []) or [])
    today = datetime.now()

    proposals = scan_for_tlh(
        lots=state["lots"],
        prices=latest_prices,
        transaction_history=state.get("transaction_history", []),
        swap_clusters=clusters,
        restricted_symbols=restricted,
        min_loss=tlh_cfg.get("min_loss_to_harvest", 200),
        wash_sale_window_days=tlh_cfg.get("wash_sale_window_days", 30),
        min_holding_days=tlh_cfg.get("min_holding_days", 2),
        min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
        tax_rate_short=tlh_cfg.get("tax_rate_short_term", 0.37),
        tax_rate_long=tlh_cfg.get("tax_rate_long_term", 0.20),
        state_tax_rate=tlh_cfg.get("state_tax_rate", 0.0),
        today=today,
    )

    # Execute proposals (on paper)
    step_trades = 0
    step_turnover = 0.0
    step_harvested = 0.0
    step_tax_savings = 0.0

    for prop in proposals:
        # Find and remove the lot
        lot_idx = None
        for i, lot in enumerate(state["lots"]):
            if lot["lot_id"] == prop.sell_lot_id:
                lot_idx = i
                break
        if lot_idx is None:
            continue

        sold_lot = state["lots"].pop(lot_idx)
        sell_price = latest_prices.get(sold_lot["symbol"], 0)
        sell_proceeds = sold_lot["shares"] * sell_price
        state["cash"] += sell_proceeds

        # Record sell
        state["transaction_history"].append({
            "symbol": sold_lot["symbol"],
            "action": "SELL",
            "date": today.strftime("%Y-%m-%d"),
            "shares": sold_lot["shares"],
        })

        # Buy replacement
        buy_price = latest_prices.get(prop.buy_symbol, 0)
        if buy_price > 0:
            buy_shares = int(sell_proceeds / buy_price)
            if buy_shares > 0:
                state["lot_counter"] += 1
                state["lots"].append({
                    "symbol": prop.buy_symbol,
                    "lot_id": f"paper-{state['lot_counter']}",
                    "shares": buy_shares,
                    "cost_basis_per_share": round(buy_price, 2),
                    "acquisition_date": today.strftime("%Y-%m-%d"),
                })
                state["cash"] -= buy_shares * buy_price

                state["transaction_history"].append({
                    "symbol": prop.buy_symbol,
                    "action": "BUY",
                    "date": today.strftime("%Y-%m-%d"),
                    "shares": buy_shares,
                })

        # Track
        loss = sold_lot["shares"] * (sell_price - sold_lot["cost_basis_per_share"])
        step_harvested += loss
        step_tax_savings += prop.estimated_tax_savings
        step_trades += 2
        step_turnover += sell_proceeds

        print(f"  [Paper] SELL {sold_lot['shares']} {sold_lot['symbol']} "
              f"-> BUY {prop.buy_symbol} | loss=${abs(loss):,.0f}")

    # Update cumulative
    state["cumulative_harvested_loss"] += step_harvested
    state["cumulative_tax_savings"] += step_tax_savings
    state["cumulative_trades"] += step_trades
    state["cumulative_turnover"] += step_turnover
    state["updated"] = today.isoformat()

    # Portfolio value
    port_value = state["cash"]
    for lot in state["lots"]:
        port_value += lot["shares"] * latest_prices.get(lot["symbol"], 0)

    # Benchmark value
    bm_start = state["benchmark_start_price"]
    bm_now = latest_prices.get(benchmark_symbol, bm_start)
    bm_value = state["initial_value"] * (bm_now / bm_start) if bm_start > 0 else 0

    # Unique positions
    positions = len(set(lot["symbol"] for lot in state["lots"]))

    # Save state
    _save_state(state)

    # Record snapshot
    _append_history({
        "date": today.isoformat(),
        "portfolio_value": round(port_value, 2),
        "benchmark_value": round(bm_value, 2),
        "cash": round(state["cash"], 2),
        "num_positions": positions,
        "tracking_error_bps": 0,  # Computed later from history
        "cumulative_harvested_loss": round(abs(state["cumulative_harvested_loss"]), 2),
        "cumulative_tax_savings": round(state["cumulative_tax_savings"], 2),
        "step_trades": step_trades,
        "step_turnover": round(step_turnover, 2),
        "cumulative_trades": state["cumulative_trades"],
        "cumulative_turnover": round(state["cumulative_turnover"], 2),
    })

    # Print summary
    port_return = (port_value / state["initial_value"] - 1) * 100
    bm_return = (bm_value / state["initial_value"] - 1) * 100

    print(f"\n[PaperTrader] Cycle complete ({today.strftime('%Y-%m-%d')})")
    print(f"  Portfolio: ${port_value:,.0f} ({port_return:+.2f}%)")
    print(f"  Benchmark: ${bm_value:,.0f} ({bm_return:+.2f}%)")
    print(f"  Harvested (session): ${abs(step_harvested):,.0f}")
    print(f"  Harvested (cumulative): ${abs(state['cumulative_harvested_loss']):,.0f}")
    print(f"  Tax savings (cumulative): ${state['cumulative_tax_savings']:,.0f}")
    print(f"  Trades (session): {step_trades}")
    if not proposals:
        print("  No TLH opportunities found this cycle")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def show_status(config: dict):
    """Print current paper portfolio status."""
    state = _load_state()
    if state is None:
        print("[PaperTrader] No portfolio found. Run with --init first.")
        return

    # Need fresh prices
    basket_cfg = config.get("basket", {})
    candidate_pool = basket_cfg.get("candidate_pool", 80)
    benchmark_symbol = state["benchmark_symbol"]

    constituents = fetch_sp500_constituents(top_n=candidate_pool)
    candidate_symbols = constituents["symbol"].tolist()
    prices_df = fetch_prices(
        candidate_symbols, benchmark=benchmark_symbol,
        lookback_days=5, refresh=True,
    )
    latest_prices = prices_df.iloc[-1].to_dict()

    # Portfolio value
    port_value = state["cash"]
    positions = {}
    for lot in state["lots"]:
        sym = lot["symbol"]
        positions.setdefault(sym, {"shares": 0, "cost": 0})
        positions[sym]["shares"] += lot["shares"]
        positions[sym]["cost"] += lot["shares"] * lot["cost_basis_per_share"]
        port_value += lot["shares"] * latest_prices.get(sym, 0)

    bm_start = state["benchmark_start_price"]
    bm_now = latest_prices.get(benchmark_symbol, bm_start)
    bm_value = state["initial_value"] * (bm_now / bm_start) if bm_start > 0 else 0

    print(f"\n{'='*60}")
    print("PAPER PORTFOLIO STATUS".center(60))
    print(f"{'='*60}")
    print(f"Created:    {state['created'][:10]}")
    print(f"Updated:    {state['updated'][:10]}")
    print(f"Value:      ${port_value:,.0f}")
    print(f"Benchmark:  ${bm_value:,.0f}")
    print(f"Cash:       ${state['cash']:,.0f}")
    print(f"Positions:  {len(positions)}")
    print(f"Harvested:  ${abs(state['cumulative_harvested_loss']):,.0f}")
    print(f"Tax saved:  ${state['cumulative_tax_savings']:,.0f}")
    print(f"Trades:     {state['cumulative_trades']}")
    print()

    # Top positions
    sorted_pos = sorted(
        positions.items(),
        key=lambda x: x[1]["shares"] * latest_prices.get(x[0], 0),
        reverse=True,
    )
    print(f"{'Symbol':<8} {'Shares':>7} {'Value':>10} {'Gain/Loss':>10}")
    print("-" * 38)
    for sym, info in sorted_pos[:20]:
        px = latest_prices.get(sym, 0)
        val = info["shares"] * px
        gl = val - info["cost"]
        gl_str = f"${gl:+,.0f}"
        print(f"{sym:<8} {info['shares']:>7} ${val:>9,.0f} {gl_str:>10}")

    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Generate report from history
# ---------------------------------------------------------------------------
def generate_report_from_history():
    """Convert paper trading history into an HTML report."""
    history = _load_history()
    if not history:
        print("[PaperTrader] No history found. Run some cycles first.")
        return

    state = _load_state()
    initial_value = state["initial_value"] if state else 200000

    # Convert history entries to StepSnapshot-like objects for the report
    # We need to create a results dict compatible with generate_html_report
    from types import SimpleNamespace

    snapshots = []
    for entry in history:
        snapshots.append(SimpleNamespace(
            date=datetime.fromisoformat(entry["date"]),
            portfolio_value=entry["portfolio_value"],
            benchmark_value=entry["benchmark_value"],
            cash=entry["cash"],
            num_positions=entry["num_positions"],
            tracking_error_bps=entry.get("tracking_error_bps", 0),
            cumulative_harvested_loss=entry.get("cumulative_harvested_loss", 0),
            cumulative_tax_savings=entry.get("cumulative_tax_savings", 0),
            step_harvested_loss=0,
            step_tax_savings=0,
            step_trades=entry.get("step_trades", 0),
            step_turnover=entry.get("step_turnover", 0),
            cumulative_trades=entry.get("cumulative_trades", 0),
            cumulative_turnover=entry.get("cumulative_turnover", 0),
        ))

    if len(snapshots) < 2:
        print("[PaperTrader] Need at least 2 data points for a report.")
        return

    final = snapshots[-1]
    port_return = (final.portfolio_value / initial_value - 1) * 100
    bm_return = (final.benchmark_value / initial_value - 1) * 100
    tax_adj = final.portfolio_value + final.cumulative_tax_savings
    tax_adj_return = (tax_adj / initial_value - 1) * 100

    summary = {
        "start_date": snapshots[0].date.strftime("%Y-%m-%d"),
        "end_date": final.date.strftime("%Y-%m-%d"),
        "trading_days": len(snapshots),
        "initial_value": initial_value,
        "final_portfolio_value": final.portfolio_value,
        "final_benchmark_value": final.benchmark_value,
        "portfolio_return_pct": round(port_return, 2),
        "benchmark_return_pct": round(bm_return, 2),
        "tax_adjusted_return_pct": round(tax_adj_return, 2),
        "excess_return_pct": round(port_return - bm_return, 2),
        "tax_alpha_pct": round(tax_adj_return - bm_return, 2),
        "total_harvested_loss": round(final.cumulative_harvested_loss, 2),
        "total_tax_savings": round(final.cumulative_tax_savings, 2),
        "total_trades": final.cumulative_trades,
        "total_turnover": round(final.cumulative_turnover, 2),
        "turnover_pct": round(final.cumulative_turnover / initial_value * 100, 1),
        "avg_tracking_error_bps": 0,
        "final_positions": final.num_positions,
        "final_cash": final.cash,
    }

    results = {
        "snapshots": snapshots,
        "summary": summary,
        "trade_log": [],
        "final_portfolio": None,
    }

    report_path = generate_html_report(
        results,
        title="Balancer Paper Trading Report",
    )

    try:
        webbrowser.open(f"file://{os.path.abspath(report_path)}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Forward paper trading")
    parser.add_argument("--init", action="store_true", help="Initialize new paper portfolio")
    parser.add_argument("--status", action="store_true", help="Show portfolio status")
    parser.add_argument("--report", action="store_true", help="Generate HTML report from history")
    parser.add_argument("--refresh", action="store_true", help="Force data refresh")
    parser.add_argument("--config", type=str, default=None, help="Path to basket_config.yaml")
    args = parser.parse_args()

    config = load_basket_config(args.config)

    if args.init:
        if os.path.exists(_PORTFOLIO_PATH):
            ans = input("[PaperTrader] Portfolio exists. Overwrite? (y/N): ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted.")
                return 0
        init_paper_portfolio(config, refresh=args.refresh)
    elif args.status:
        show_status(config)
    elif args.report:
        generate_report_from_history()
    else:
        run_cycle(config, refresh=args.refresh)

    return 0


if __name__ == "__main__":
    sys.exit(main())
