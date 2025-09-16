"""
Portfolio simulation using the efficient Parquet-based data system.

Enhancements:
- Reproducible seeding
- Rich portfolio metrics (CAGR, vol, Sharpe, max drawdown, Calmar)
- Optional parallel execution
- Metadata persistence (tickers per portfolio, run params, metrics)
- Minor performance tweaks
"""
from __future__ import annotations

import os
import json
import math
import time
import random
import logging
import datetime as dt
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Tuple

import numpy as np
import pandas as pd

from efficient_data_manager import EfficientDataManager

try:
    from joblib import Parallel, delayed
    _HAVE_JOBLIB = True
except ImportError:
    _HAVE_JOBLIB = False
    from multiprocessing import Pool, cpu_count

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "global_seed": 42,
    "num_portfolios": 10,
    "num_stocks_per_portfolio": 10,
    "time_horizon_years": 10,
    "trading_days_per_year": 252,
    "risk_free_rate": 0.0,    # annualized RF for Sharpe
    "cov_diag_regularization": 1e-6,
    "use_parallel": True,
    "n_jobs": -1,             # -1 => all cores (joblib); multiprocessing uses cpu_count()
    "output_dir": "simulation_runs",
    "run_label": None         # optional custom label
}

# Derived
NUM_SIMULATIONS = CONFIG["time_horizon_years"] * CONFIG["trading_days_per_year"]

@dataclass
class PortfolioResult:
    portfolio_id: str
    tickers: List[str]
    metrics: Dict[str, Any]
    daily_returns: List[float]  # store as list for JSON safety

def set_global_seeds(seed: int):
    random.seed(seed)
    np.random.seed(seed)

def compute_metrics(daily_returns: pd.Series, trading_days_per_year: int, risk_free_rate: float) -> Dict[str, Any]:
    # Daily stats
    mean_daily = daily_returns.mean()
    std_daily = daily_returns.std(ddof=1)
    ann_ret = (1 + daily_returns.add(1).prod() - 1)  # redundant expression; we compute CAGR below anyway
    cumulative = (1 + daily_returns).cumprod()
    total_return = cumulative.iloc[-1] - 1
    n_days = len(daily_returns)
    years = n_days / trading_days_per_year
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else float("nan")
    ann_vol = std_daily * math.sqrt(trading_days_per_year) if std_daily == std_daily else float("nan")  # NaN guard
    ann_mean = mean_daily * trading_days_per_year
    sharpe = (ann_mean - risk_free_rate) / ann_vol if ann_vol and ann_vol != 0 else float("nan")

    # Drawdowns
    running_max = cumulative.cummax()
    drawdowns = cumulative / running_max - 1
    max_drawdown = drawdowns.min()
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else float("nan")

    # Downside deviation (semi-vol)
    downside = daily_returns[daily_returns < 0]
    downside_dev = downside.std(ddof=1) * math.sqrt(trading_days_per_year) if not downside.empty else 0.0

    return {
        "total_return": total_return,
        "CAGR": cagr,
        "annualized_volatility": ann_vol,
        "annualized_return": ann_mean,
        "Sharpe": sharpe,
        "MaxDrawdown": max_drawdown,
        "Calmar": calmar,
        "DownsideDeviation": downside_dev,
        "NumDays": n_days
    }

def simulate_single_portfolio(
    idx: int,
    base_seed: int,
    returns_data: pd.DataFrame,
    num_stocks: int,
    num_simulations: int,
    trading_days_per_year: int,
    risk_free_rate: float,
    cov_diag_regularization: float
) -> PortfolioResult | None:
    # Deterministic seed per portfolio
    seed = base_seed + idx
    rng = np.random.default_rng(seed)
    random.seed(seed)

    available = list(returns_data.columns)
    if len(available) < num_stocks:
        num_stocks = len(available)
    tickers = random.sample(available, num_stocks)

    selected = returns_data[tickers].dropna(how="any")
    if selected.empty:
        logger.warning(f"Portfolio {idx+1}: no overlapping data, skipped")
        return None

    mean_returns = selected.mean()
    cov_matrix = selected.cov().values
    cov_matrix[np.diag_indices_from(cov_matrix)] += cov_diag_regularization

    try:
        simulated_matrix = rng.multivariate_normal(mean_returns.values, cov_matrix, size=num_simulations)
    except np.linalg.LinAlgError:
        logger.warning(f"Portfolio {idx+1}: covariance not PSD, applying eigenvalue clip")
        # Nearest PSD via eigen clipping (simple)
        eigvals, eigvecs = np.linalg.eigh(cov_matrix)
        eigvals_clipped = np.clip(eigvals, 1e-10, None)
        cov_psd = (eigvecs @ np.diag(eigvals_clipped) @ eigvecs.T)
        simulated_matrix = rng.multivariate_normal(mean_returns.values, cov_psd, size=num_simulations)

    simulated_df = pd.DataFrame(simulated_matrix, columns=mean_returns.index)
    portfolio_daily = simulated_df[tickers].mean(axis=1)

    metrics = compute_metrics(
        daily_returns=portfolio_daily,
        trading_days_per_year=trading_days_per_year,
        risk_free_rate=risk_free_rate
    )

    return PortfolioResult(
        portfolio_id=f"Portfolio_{idx+1}",
        tickers=tickers,
        metrics=metrics,
        daily_returns=portfolio_daily.tolist()
    )

def run_simulation():
    set_global_seeds(CONFIG["global_seed"])
    run_ts = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    run_label = CONFIG["run_label"] or f"run_{run_ts.replace(':','-')}"
    output_dir = Path(CONFIG["output_dir"]) / run_label
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading return matrix...")
    data_manager = EfficientDataManager()
    returns_data = data_manager.get_returns_matrix()
    logger.info(f"Loaded returns shape={returns_data.shape}")

    t_start = time.time()

    args = dict(
        base_seed=CONFIG["global_seed"] * 10_000,
        returns_data=returns_data,
        num_stocks=CONFIG["num_stocks_per_portfolio"],
        num_simulations=NUM_SIMULATIONS,
        trading_days_per_year=CONFIG["trading_days_per_year"],
        risk_free_rate=CONFIG["risk_free_rate"],
        cov_diag_regularization=CONFIG["cov_diag_regularization"]
    )

    results: List[PortfolioResult] = []

    if CONFIG["use_parallel"] and CONFIG["num_portfolios"] > 1:
        logger.info("Parallel execution enabled")
        if _HAVE_JOBLIB:
            n_jobs = CONFIG["n_jobs"]
            results = Parallel(n_jobs=n_jobs, prefer="processes")(
                delayed(simulate_single_portfolio)(i, **args)
                for i in range(CONFIG["num_portfolios"])
            )
        else:
            logger.info("joblib not found; falling back to multiprocessing Pool")
            workers = os.cpu_count() if CONFIG["n_jobs"] in (-1, None) else CONFIG["n_jobs"]
            with Pool(processes=workers) as pool:
                multiple = [
                    pool.apply_async(simulate_single_portfolio, (i,), args)
                    for i in range(CONFIG["num_portfolios"])
                ]
                results = [r.get() for r in multiple]
    else:
        logger.info("Running sequentially")
        for i in range(CONFIG["num_portfolios"]):
            results.append(simulate_single_portfolio(i, **args))

    results = [r for r in results if r is not None]
    if not results:
        logger.error("No portfolios generated.")
        return

    # Assemble returns DataFrame
    returns_dict = {
        r.portfolio_id: pd.Series(r.daily_returns, name=r.portfolio_id)
        for r in results
    }
    returns_df = pd.DataFrame(returns_dict)
    # Save returns
    returns_path = output_dir / "portfolio_daily_returns.parquet"
    returns_df.to_parquet(returns_path)

    # Metrics and metadata
    metrics_list = []
    for r in results:
        row = {
            "portfolio_id": r.portfolio_id,
            "tickers": r.tickers,
            **r.metrics
        }
        metrics_list.append(row)
    metrics_df = pd.DataFrame(metrics_list).set_index("portfolio_id")
    metrics_path = output_dir / "portfolio_metrics.csv"
    metrics_df.to_csv(metrics_path)

    metadata = {
        "run_timestamp_utc": run_ts,
        "run_label": run_label,
        "config": CONFIG,
        "num_portfolios_generated": len(results),
        "returns_file": str(returns_path),
        "metrics_file": str(metrics_path),
        "tickers_per_portfolio": {r.portfolio_id: r.tickers for r in results},
        "runtime_seconds": round(time.time() - t_start, 3)
    }
    with open(output_dir / "run_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    logger.info(f"Simulation complete. Portfolios: {len(results)}")
    logger.info(f"Outputs saved under: {output_dir}")

    # Quick console preview
    print("Metrics summary:")
    print(metrics_df.head())
    print("\nFinal cumulative returns (last day):")
    final_vals = (1 + returns_df).cumprod().iloc[-1] - 1
    print(final_vals.sort_values(ascending=False))

if __name__ == "__main__":
    run_simulation()