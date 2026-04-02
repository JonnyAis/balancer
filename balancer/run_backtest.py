# filepath: balancer/run_backtest.py
"""
Backtest runner for basket + TLH strategy.

Usage:
    python -m balancer.run_backtest                           # Default: 1 year backtest
    python -m balancer.run_backtest --months 18               # 18-month backtest
    python -m balancer.run_backtest --months 24 --refresh     # 2 years, fresh data
    python -m balancer.run_backtest --no-report               # Skip HTML report

Outputs:
    - Terminal summary
    - HTML report at ~/.balancer/reports/backtest_YYYYMMDD_HHMMSS.html
"""

import argparse
import os
import sys
import webbrowser

from .backtest import run_backtest
from .backtest_report import generate_html_report
from .basket_construction import load_basket_config, resolve_external_positions
from .basket_data import (
    compute_benchmark_sector_weights,
    fetch_prices,
    fetch_sp500_constituents,
)


def main():
    parser = argparse.ArgumentParser(description="Backtest basket + TLH strategy")
    parser.add_argument(
        "--months", type=int, default=12,
        help="Backtest duration in months (default: 12)",
    )
    parser.add_argument("--refresh", action="store_true", help="Force re-download data")
    parser.add_argument("--value", type=float, default=None, help="Override portfolio value")
    parser.add_argument("--config", type=str, default=None, help="Path to basket_config.yaml")
    parser.add_argument("--top-n", type=int, default=None, help="Override candidate pool size")
    parser.add_argument(
        "--tlh-freq", type=int, default=7,
        help="TLH scan frequency in days (default: 7)",
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip HTML report generation",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't auto-open the HTML report in browser",
    )
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # 1. Load config
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

    # For backtest, we need lookback + backtest period of price data
    # Approximate trading days: months * 21
    backtest_trading_days = args.months * 21
    total_days_needed = lookback_days + backtest_trading_days
    # Fetch with buffer for weekends/holidays
    fetch_calendar_days = int(total_days_needed * 1.5)

    print(f"[RunBacktest] Benchmark: {benchmark_symbol}")
    print(f"[RunBacktest] Backtest period: ~{args.months} months")
    print(f"[RunBacktest] Portfolio value: ${total_value:,.0f}")
    print(f"[RunBacktest] TLH frequency: every {args.tlh_freq} days")
    print()

    # -----------------------------------------------------------------------
    # 2. Fetch data (larger window than normal)
    # -----------------------------------------------------------------------
    constituents = fetch_sp500_constituents(top_n=candidate_pool, refresh=args.refresh)
    candidate_symbols = constituents["symbol"].tolist()
    sector_map = dict(zip(constituents["symbol"], constituents["sector"]))
    benchmark_weights = dict(zip(constituents["symbol"], constituents["weight"]))
    benchmark_sector_weights = compute_benchmark_sector_weights(constituents)

    print(f"[RunBacktest] Fetching {fetch_calendar_days} calendar days of price data...")
    prices_full = fetch_prices(
        candidate_symbols,
        benchmark=benchmark_symbol,
        lookback_days=total_days_needed,
        refresh=args.refresh,
    )
    print(f"[RunBacktest] Price matrix: {prices_full.shape[0]} days x "
          f"{prices_full.shape[1]} symbols")

    # Filter candidates to those with data
    available = [s for s in candidate_symbols if s in prices_full.columns]
    candidate_symbols = available
    print(f"[RunBacktest] {len(candidate_symbols)} candidates with full price data")
    print()

    # -----------------------------------------------------------------------
    # 3. Resolve external positions
    # -----------------------------------------------------------------------
    latest_prices = prices_full.iloc[-1].to_dict()
    external_values = resolve_external_positions(config, latest_prices)
    if external_values:
        total_ext = sum(external_values.values())
        print(f"[RunBacktest] External positions: ${total_ext:,.0f}")
        for sym, val in external_values.items():
            print(f"  {sym}: ${val:,.0f}")
        print()

    # -----------------------------------------------------------------------
    # 4. Run backtest
    # -----------------------------------------------------------------------
    restricted = set(config.get("restricted_symbols", []) or [])

    results = run_backtest(
        prices_full=prices_full,
        benchmark_symbol=benchmark_symbol,
        candidate_symbols=candidate_symbols,
        sector_map=sector_map,
        benchmark_weights=benchmark_weights,
        benchmark_sector_weights=benchmark_sector_weights,
        external_values=external_values,
        basket_cfg=basket_cfg,
        opt_cfg=opt_cfg,
        tlh_cfg=tlh_cfg,
        total_basket_value=total_value,
        lookback_days=lookback_days,
        rebalance_frequency_days=0,  # No periodic rebalance for now
        tlh_frequency_days=args.tlh_freq,
        restricted_symbols=restricted,
    )

    # -----------------------------------------------------------------------
    # 5. Generate HTML report
    # -----------------------------------------------------------------------
    if not args.no_report:
        report_path = generate_html_report(
            results,
            title=f"Balancer Backtest: {args.months}-Month TLH Strategy",
        )

        if not args.no_open:
            try:
                webbrowser.open(f"file://{os.path.abspath(report_path)}")
                print("[RunBacktest] Report opened in browser")
            except Exception:
                print(f"[RunBacktest] Open the report manually: {report_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
