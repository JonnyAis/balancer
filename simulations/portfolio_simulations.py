"""
Portfolio simulation using the efficient Parquet-based data system.
"""

import random
import numpy as np
import pandas as pd
from efficient_data_manager import EfficientDataManager
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize data manager and load returns data
data_manager = EfficientDataManager()
returns_data = data_manager.get_returns_matrix()

logger.info(f"Loaded returns data with shape: {returns_data.shape}")

# Generate correlation matrix and mean returns
cov_matrix = returns_data.cov()
mean_returns = returns_data.mean()

# Define parameters for simulation
num_portfolios = 10
num_stocks_per_portfolio = 10
time_horizon_years = 10
num_simulations = 252 * time_horizon_years  # Assuming 252 trading days per year

# Function to generate simulated returns
def generate_simulated_returns(mean_returns, cov_matrix, num_simulations):
    simulated_returns = np.random.multivariate_normal(mean_returns, cov_matrix, num_simulations)
    return pd.DataFrame(simulated_returns, columns=mean_returns.index)

# Function to calculate portfolio returns
def calculate_portfolio_returns(stocks, simulated_returns):
    portfolio_returns = simulated_returns[stocks].mean(axis=1)
    cumulative_returns = (1 + portfolio_returns).cumprod() - 1
    return cumulative_returns

# Simulate multiple portfolios
portfolio_results = {}
for i in range(num_portfolios):
    available_stocks = list(returns_data.columns)
    if len(available_stocks) < num_stocks_per_portfolio:
        logger.warning(f"Only {len(available_stocks)} stocks available, adjusting portfolio size")
        num_stocks_per_portfolio = len(available_stocks)
    
    selected_stocks = random.sample(available_stocks, num_stocks_per_portfolio)
    
    # Determine the overlapping date range for the selected stocks
    selected_data = returns_data[selected_stocks].dropna(how='any')
    
    if selected_data.empty:
        logger.warning(f"No overlapping data for portfolio {i+1}, skipping")
        continue
    
    # Calculate historical mean returns and covariance matrix for the overlapping date range
    mean_returns = selected_data.mean()
    cov_matrix = selected_data.cov()
    
    # Regularize the covariance matrix by adding a small value to the diagonal
    cov_matrix += np.eye(cov_matrix.shape[0]) * 1e-6
    
    # Generate simulated returns
    simulated_returns = generate_simulated_returns(mean_returns, cov_matrix, num_simulations)
    
    # Calculate portfolio returns
    portfolio_returns = calculate_portfolio_returns(selected_stocks, simulated_returns)
    portfolio_results[f'Portfolio_{i+1}'] = portfolio_returns

# Convert results to DataFrame
portfolio_results_df = pd.DataFrame(portfolio_results)

logger.info(f"Portfolio simulation complete. Generated {len(portfolio_results_df.columns)} portfolios")
print("Portfolio simulation results:")
print(portfolio_results_df.head())
print(f"\nFinal portfolio values after {time_horizon_years} years:")
print(portfolio_results_df.iloc[-1])