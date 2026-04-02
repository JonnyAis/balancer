# filepath: balancer/backtest.py
"""
Backtest engine for basket + TLH strategy.

Replays historical market data, simulating:
  - Initial basket construction at start date
  - Weekly TLH scans and swap execution
  - Portfolio tracking vs benchmark

Maintains full lot-level state so that wash sale rules, holding periods,
and cost basis are realistic.

Usage:
    from balancer.backtest import run_backtest
    results = run_backtest(config, start_date, end_date)
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from .basket_construction import optimize_basket, weights_to_shares
from .basket_data import compute_covariance, compute_returns
from .swap_clusters import build_swap_clusters
from .tlh_scanner import scan_for_tlh


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------
@dataclass
class SimLot:
    """A single tax lot in the simulated portfolio."""
    symbol: str
    lot_id: str
    shares: int
    cost_basis_per_share: float
    acquisition_date: datetime


@dataclass
class SimPortfolio:
    """Full simulated portfolio state."""
    lots: list = field(default_factory=list)
    cash: float = 0.0
    lot_counter: int = 0

    def add_lot(self, symbol: str, shares: int, price: float, date: datetime):
        self.lot_counter += 1
        self.lots.append(SimLot(
            symbol=symbol,
            lot_id=f"sim-{self.lot_counter}",
            shares=shares,
            cost_basis_per_share=price,
            acquisition_date=date,
        ))

    def remove_lot(self, lot_id: str) -> Optional[SimLot]:
        for i, lot in enumerate(self.lots):
            if lot.lot_id == lot_id:
                return self.lots.pop(i)
        return None

    def positions(self) -> dict:
        """Aggregate shares by symbol. Returns {symbol: total_shares}."""
        pos = {}
        for lot in self.lots:
            pos[lot.symbol] = pos.get(lot.symbol, 0) + lot.shares
        return pos

    def market_value(self, prices: dict) -> float:
        total = self.cash
        for lot in self.lots:
            total += lot.shares * prices.get(lot.symbol, 0)
        return total

    def lots_as_dicts(self) -> list[dict]:
        """Convert lots to the format expected by tlh_scanner."""
        return [
            {
                "symbol": lot.symbol,
                "lot_id": lot.lot_id,
                "shares": lot.shares,
                "cost_basis_per_share": lot.cost_basis_per_share,
                "acquisition_date": lot.acquisition_date.strftime("%Y-%m-%d"),
            }
            for lot in self.lots
        ]

    def current_weights(self, prices: dict) -> dict:
        """Return {symbol: weight_fraction} based on current market values."""
        pos = self.positions()
        total = sum(shares * prices.get(sym, 0) for sym, shares in pos.items())
        if total <= 0:
            return {}
        return {
            sym: (shares * prices.get(sym, 0)) / total
            for sym, shares in pos.items()
            if prices.get(sym, 0) > 0
        }


# ---------------------------------------------------------------------------
# Snapshot (recorded at each step)
# ---------------------------------------------------------------------------
@dataclass
class StepSnapshot:
    date: datetime
    portfolio_value: float
    benchmark_value: float
    cash: float
    num_positions: int
    tracking_error_bps: float
    cumulative_harvested_loss: float
    cumulative_tax_savings: float
    step_harvested_loss: float
    step_tax_savings: float
    step_trades: int
    step_turnover: float
    cumulative_trades: int
    cumulative_turnover: float


# ---------------------------------------------------------------------------
# Core backtest
# ---------------------------------------------------------------------------
def run_backtest(
    prices_full: pd.DataFrame,
    benchmark_symbol: str,
    candidate_symbols: list,
    sector_map: dict,
    benchmark_weights: dict,
    benchmark_sector_weights: dict,
    external_values: dict,
    basket_cfg: dict,
    opt_cfg: dict,
    tlh_cfg: dict,
    total_basket_value: float = 200_000,
    lookback_days: int = 252,
    rebalance_frequency_days: int = 7,
    tlh_frequency_days: int = 7,
    restricted_symbols: set = None,
    swap_scores=None,
    swappability_weight: float = 0.0002,
) -> dict:
    """
    Run a full backtest.

    Args:
        prices_full: DataFrame of daily prices, indexed by date.
                     Must contain all candidate symbols + benchmark.
                     Should span lookback_days + desired backtest period.
        benchmark_symbol: e.g. "VOO"
        candidate_symbols: Stocks eligible for the basket.
        sector_map: {symbol: sector}
        benchmark_weights: {symbol: weight_pct}
        benchmark_sector_weights: {sector: weight_pct}
        external_values: {symbol: dollar_value} of external positions.
        basket_cfg: Basket config section from YAML.
        opt_cfg: Optimizer config section.
        tlh_cfg: TLH config section.
        total_basket_value: Starting portfolio value.
        lookback_days: Days of history for covariance estimation.
        rebalance_frequency_days: How often to rebalance basket (0 = never).
        tlh_frequency_days: How often to run TLH scanner.
        restricted_symbols: Symbols to exclude.

    Returns:
        {
            "snapshots": [StepSnapshot, ...],
            "summary": dict,
            "trade_log": [dict, ...],
            "final_portfolio": SimPortfolio,
        }
    """
    restricted = restricted_symbols or set()

    # Validate data
    all_dates = prices_full.index.tolist()
    if len(all_dates) < lookback_days + 20:
        raise ValueError(
            f"Need at least {lookback_days + 20} days of price data, "
            f"got {len(all_dates)}"
        )

    # The backtest starts after the lookback window
    backtest_start_idx = lookback_days
    backtest_dates = all_dates[backtest_start_idx:]

    print(f"[Backtest] Price data: {all_dates[0].date()} to {all_dates[-1].date()}")
    print(f"[Backtest] Lookback: {lookback_days} days")
    print(f"[Backtest] Backtest period: {backtest_dates[0].date()} to {backtest_dates[-1].date()}")
    print(f"[Backtest] {len(backtest_dates)} trading days")
    print()

    # -----------------------------------------------------------------------
    # Initialize: build first basket using lookback window
    # -----------------------------------------------------------------------
    init_prices_window = prices_full.iloc[:backtest_start_idx]
    init_day_prices = prices_full.iloc[backtest_start_idx]

    init_returns = compute_returns(init_prices_window)
    init_cov = compute_covariance(init_returns, annualize=True)

    # Filter candidates to those with data
    valid_candidates = [s for s in candidate_symbols if s in init_cov.index]

    print(f"[Backtest] Building initial basket with {len(valid_candidates)} candidates...")
    init_result = optimize_basket(
        candidate_symbols=valid_candidates,
        benchmark_symbol=benchmark_symbol,
        covariance=init_cov,
        returns=init_returns,
        benchmark_weights=benchmark_weights,
        sector_map=sector_map,
        benchmark_sector_weights=benchmark_sector_weights,
        external_values=external_values,
        total_basket_value=total_basket_value,
        current_weights=None,
        min_weight_pct=basket_cfg.get("min_weight_pct", 0.5),
        max_weight_pct=basket_cfg.get("max_weight_pct", 8.0),
        max_stocks=basket_cfg.get("max_stocks", 40),
        turnover_penalty=basket_cfg.get("turnover_penalty", 0.005),
        sector_max_deviation_pct=basket_cfg.get("sector_max_deviation_pct", 5.0),
        swap_scores=swap_scores,
        swappability_weight=swappability_weight,
        solver=opt_cfg.get("solver", "CLARABEL"),
        max_iterations=opt_cfg.get("max_iterations", 10000),
    )

    if init_result["solver_status"] not in ("optimal", "optimal_inaccurate"):
        raise RuntimeError(f"Initial basket optimization failed: {init_result['solver_status']}")

    latest_prices = init_day_prices.to_dict()
    init_shares = weights_to_shares(init_result["weights"], total_basket_value, latest_prices)

    # Build initial portfolio
    portfolio = SimPortfolio(cash=total_basket_value)
    start_date = backtest_dates[0]

    for sym, shares in init_shares.items():
        px = latest_prices.get(sym, 0)
        if px > 0 and shares > 0:
            portfolio.add_lot(sym, shares, px, start_date)
            portfolio.cash -= shares * px

    print(f"[Backtest] Initial portfolio: {len(init_shares)} positions, "
          f"${portfolio.market_value(latest_prices):,.0f} value, "
          f"${portfolio.cash:,.0f} cash")
    print()

    # Benchmark tracking: buy-and-hold VOO equivalent
    benchmark_start_price = latest_prices.get(benchmark_symbol, 0)
    if benchmark_start_price <= 0:
        raise ValueError(f"No price for benchmark {benchmark_symbol}")

    # -----------------------------------------------------------------------
    # Step through time
    # -----------------------------------------------------------------------
    snapshots = []
    trade_log = []
    transaction_history = []  # For wash sale tracking

    cumulative_harvested = 0.0
    cumulative_tax_savings = 0.0
    cumulative_trades = 0
    cumulative_turnover = 0.0

    last_tlh_date = start_date

    for step_idx, current_date in enumerate(backtest_dates):
        day_prices = prices_full.loc[current_date].to_dict()

        # Portfolio value
        port_value = portfolio.market_value(day_prices)

        # Benchmark value (buy and hold)
        bm_price = day_prices.get(benchmark_symbol, benchmark_start_price)
        bm_value = total_basket_value * (bm_price / benchmark_start_price)

        step_harvested = 0.0
        step_tax_savings = 0.0
        step_trades = 0
        step_turnover = 0.0

        # ---------------------------------------------------------------
        # TLH scan (at configured frequency)
        # ---------------------------------------------------------------
        days_since_tlh = (current_date - last_tlh_date).days
        if days_since_tlh >= tlh_frequency_days and step_idx > 0:
            # Build covariance from rolling window
            window_end = step_idx + backtest_start_idx
            window_start = max(0, window_end - lookback_days)
            window_prices = prices_full.iloc[window_start:window_end]
            window_returns = compute_returns(window_prices)
            correlation = window_returns.corr()

            clusters = build_swap_clusters(
                correlation=correlation,
                sector_map=sector_map,
                min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
                cluster_size=tlh_cfg.get("swap_cluster_size", 4),
                candidate_symbols=valid_candidates,
            )

            lots_data = portfolio.lots_as_dicts()
            if lots_data:
                proposals = scan_for_tlh(
                    lots=lots_data,
                    prices=day_prices,
                    transaction_history=transaction_history,
                    swap_clusters=clusters,
                    restricted_symbols=restricted,
                    min_loss=tlh_cfg.get("min_loss_to_harvest", 200),
                    wash_sale_window_days=tlh_cfg.get("wash_sale_window_days", 30),
                    min_holding_days=tlh_cfg.get("min_holding_days", 2),
                    min_correlation=tlh_cfg.get("min_correlation_for_swap", 0.85),
                    tax_rate_short=tlh_cfg.get("tax_rate_short_term", 0.37),
                    tax_rate_long=tlh_cfg.get("tax_rate_long_term", 0.20),
                    state_tax_rate=tlh_cfg.get("state_tax_rate", 0.0),
                    today=current_date,
                )

                # Execute TLH swaps
                for prop in proposals:
                    # Sell
                    sold_lot = portfolio.remove_lot(prop.sell_lot_id)
                    if sold_lot is None:
                        continue

                    sell_proceeds = sold_lot.shares * day_prices.get(sold_lot.symbol, 0)
                    portfolio.cash += sell_proceeds

                    # Record sell transaction
                    transaction_history.append({
                        "symbol": sold_lot.symbol,
                        "action": "SELL",
                        "date": current_date.strftime("%Y-%m-%d"),
                        "shares": sold_lot.shares,
                    })

                    # Buy replacement
                    buy_price = day_prices.get(prop.buy_symbol, 0)
                    if buy_price > 0:
                        buy_shares = int(sell_proceeds / buy_price)
                        if buy_shares > 0:
                            buy_cost = buy_shares * buy_price
                            portfolio.add_lot(
                                prop.buy_symbol, buy_shares, buy_price, current_date
                            )
                            portfolio.cash -= buy_cost

                            transaction_history.append({
                                "symbol": prop.buy_symbol,
                                "action": "BUY",
                                "date": current_date.strftime("%Y-%m-%d"),
                                "shares": buy_shares,
                            })

                    # Track metrics
                    loss = sold_lot.shares * (
                        day_prices.get(sold_lot.symbol, 0) - sold_lot.cost_basis_per_share
                    )
                    step_harvested += loss
                    step_tax_savings += prop.estimated_tax_savings
                    step_trades += 2  # sell + buy
                    step_turnover += sell_proceeds

                    trade_log.append({
                        "date": current_date.strftime("%Y-%m-%d"),
                        "action": "TLH_SWAP",
                        "sell_symbol": sold_lot.symbol,
                        "sell_shares": sold_lot.shares,
                        "sell_price": day_prices.get(sold_lot.symbol, 0),
                        "buy_symbol": prop.buy_symbol,
                        "buy_shares": buy_shares if buy_price > 0 else 0,
                        "buy_price": buy_price,
                        "harvested_loss": round(loss, 2),
                        "tax_savings": round(prop.estimated_tax_savings, 2),
                        "correlation": prop.replacement_correlation,
                    })

            last_tlh_date = current_date

        # Update cumulative
        cumulative_harvested += step_harvested
        cumulative_tax_savings += step_tax_savings
        cumulative_trades += step_trades
        cumulative_turnover += step_turnover

        # Tracking error estimate (simplified: realized return difference)
        # We compute rolling realized TE every snapshot
        te_bps = _estimate_realized_te(snapshots, port_value, bm_value, total_basket_value)

        snapshots.append(StepSnapshot(
            date=current_date,
            portfolio_value=round(port_value, 2),
            benchmark_value=round(bm_value, 2),
            cash=round(portfolio.cash, 2),
            num_positions=len(portfolio.positions()),
            tracking_error_bps=round(te_bps, 1),
            cumulative_harvested_loss=round(cumulative_harvested, 2),
            cumulative_tax_savings=round(cumulative_tax_savings, 2),
            step_harvested_loss=round(step_harvested, 2),
            step_tax_savings=round(step_tax_savings, 2),
            step_trades=step_trades,
            step_turnover=round(step_turnover, 2),
            cumulative_trades=cumulative_trades,
            cumulative_turnover=round(cumulative_turnover, 2),
        ))

        # Progress indicator
        if step_idx > 0 and step_idx % 50 == 0:
            print(f"[Backtest] Day {step_idx}/{len(backtest_dates)} "
                  f"({current_date.date()}) "
                  f"PV=${port_value:,.0f} BM=${bm_value:,.0f} "
                  f"TLH=${abs(cumulative_harvested):,.0f}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    final_pv = snapshots[-1].portfolio_value
    final_bm = snapshots[-1].benchmark_value

    port_return = (final_pv / total_basket_value - 1) * 100
    bm_return = (final_bm / total_basket_value - 1) * 100

    # Tax-adjusted return: add back tax savings as if reinvested
    tax_adj_value = final_pv + cumulative_tax_savings
    tax_adj_return = (tax_adj_value / total_basket_value - 1) * 100

    summary = {
        "start_date": backtest_dates[0].strftime("%Y-%m-%d"),
        "end_date": backtest_dates[-1].strftime("%Y-%m-%d"),
        "trading_days": len(backtest_dates),
        "initial_value": total_basket_value,
        "final_portfolio_value": round(final_pv, 2),
        "final_benchmark_value": round(final_bm, 2),
        "portfolio_return_pct": round(port_return, 2),
        "benchmark_return_pct": round(bm_return, 2),
        "tax_adjusted_return_pct": round(tax_adj_return, 2),
        "excess_return_pct": round(port_return - bm_return, 2),
        "tax_alpha_pct": round(tax_adj_return - bm_return, 2),
        "total_harvested_loss": round(abs(cumulative_harvested), 2),
        "total_tax_savings": round(cumulative_tax_savings, 2),
        "total_trades": cumulative_trades,
        "total_turnover": round(cumulative_turnover, 2),
        "turnover_pct": round(cumulative_turnover / total_basket_value * 100, 1),
        "avg_tracking_error_bps": round(
            np.mean([s.tracking_error_bps for s in snapshots if s.tracking_error_bps > 0] or [0]), 1
        ),
        "final_positions": snapshots[-1].num_positions,
        "final_cash": round(portfolio.cash, 2),
    }

    print("\n[Backtest] Complete.")
    print(f"  Portfolio return: {port_return:+.2f}%")
    print(f"  Benchmark return: {bm_return:+.2f}%")
    print(f"  Tax-adjusted return: {tax_adj_return:+.2f}%")
    print(f"  Harvested losses: ${abs(cumulative_harvested):,.0f}")
    print(f"  Est. tax savings: ${cumulative_tax_savings:,.0f}")
    print(f"  Total trades: {cumulative_trades}")

    return {
        "snapshots": snapshots,
        "summary": summary,
        "trade_log": trade_log,
        "final_portfolio": portfolio,
    }


def _estimate_realized_te(
    snapshots: list,
    current_pv: float,
    current_bm: float,
    initial_value: float,
    window: int = 20,
) -> float:
    """
    Estimate realized tracking error from recent return differences.
    Returns annualized TE in basis points.
    """
    if len(snapshots) < window:
        return 0.0

    recent = snapshots[-window:]
    diffs = []
    for i in range(1, len(recent)):
        prev_pv = recent[i - 1].portfolio_value
        prev_bm = recent[i - 1].benchmark_value
        if prev_pv > 0 and prev_bm > 0:
            r_port = (recent[i].portfolio_value / prev_pv) - 1
            r_bm = (recent[i].benchmark_value / prev_bm) - 1
            diffs.append(r_port - r_bm)

    if not diffs:
        return 0.0

    daily_te = np.std(diffs)
    annual_te = daily_te * math.sqrt(252)
    return annual_te * 10000  # Convert to bps
