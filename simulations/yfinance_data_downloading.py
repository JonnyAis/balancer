"""
Updated version using the efficient Parquet-based system.

This replaces the CSV-based approach with:
1. Parquet storage for faster I/O
2. Incremental updates (only download missing data)
3. Better error handling and logging
4. Metadata tracking
"""

import os
import pandas as pd
import openpyxl
from datetime import datetime, timedelta
from efficient_data_manager import EfficientDataManager
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---set parameters---
recency_threshold = 1  # days to check for recency of data
min_days_threshold = 252 * 1  # minimum number of days of data required for a ticker

# Define file paths
ticker_file = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\ticker_data\processed\tickers.xlsx'
data_storage_dir = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\data_storage'

def load_ticker_list(ticker_file: str) -> list:
    """Load and clean the ticker list from Excel file."""
    try:
        # Import the table of tickers from the excel file
        ticker_data = pd.read_excel(
            ticker_file,
            sheet_name='Sheet1',
            header=0,
            usecols='A:B'
        )
        
        # Convert the Ticker column to a string and clean
        ticker_data['Ticker'] = ticker_data['Ticker'].astype(str)
        unique_tickers = ticker_data['Ticker'].unique()
        
        # Clean up ticker list (remove nan, strip whitespace, uppercase)
        cleaned_tickers = []
        for ticker in unique_tickers:
            if ticker and ticker.lower() != 'nan':
                cleaned_tickers.append(ticker.strip().upper())
        
        return cleaned_tickers
    except Exception as e:
        logger.error(f"Error loading ticker list: {e}")
        return []

def main():
    """Main function to update financial data using EfficientDataManager."""
    logger.info("Starting data update process with EfficientDataManager")
    
    # Load ticker list
    unique_tickers = load_ticker_list(ticker_file)
    logger.info(f"Loaded {len(unique_tickers)} unique tickers")
    
    # Uncomment for testing with subset
    # unique_tickers = unique_tickers[:10]  # Use only the first ten for testing
    
    # Initialize the efficient data manager
    data_manager = EfficientDataManager(
        data_dir=data_storage_dir,
        min_days_threshold=min_days_threshold
    )
    
    # Check if data is up-to-date
    if data_manager.is_data_recent(days=recency_threshold):
        logger.info("Data is recent, loading existing data")
        returns_data = data_manager.get_returns_matrix()
    else:
        logger.info("Data needs updating, performing incremental update")
        
        # Update data (incremental updates only download missing data)
        success_count, failed_tickers = data_manager.update_tickers(unique_tickers, max_workers=5)
        logger.info(f"Updated {success_count} tickers successfully")
        if failed_tickers:
            logger.warning(f"Failed tickers: {failed_tickers}")
        
        # Get the updated returns matrix
        returns_data = data_manager.get_returns_matrix()
    
    # Display summary
    logger.info("Final returns data summary:")
    logger.info(f"Shape: {returns_data.shape}")
    logger.info(f"Date range: {returns_data.index.min()} to {returns_data.index.max()}")
    logger.info(f"Tickers: {len(returns_data.columns)}")
    
    print("Data update complete!")
    print(f"Returns matrix shape: {returns_data.shape}")
    print(f"Latest 5 rows:")
    print(returns_data.tail())
    
    return returns_data

if __name__ == "__main__":
    returns_data = main()