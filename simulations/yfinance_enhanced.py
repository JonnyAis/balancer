"""
Updated version of yfinance_data_downloading.py using the efficient Parquet-based system.

This replaces your existing CSV-based approach with:
1. Parquet storage for faster I/O
2. Incremental updates (only download missing data)
3. Better error handling and logging
4. Metadata tracking
"""

import os
import pandas as pd
from datetime import datetime, timedelta
from efficient_data_manager import EfficientDataManager
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---set parameters---
RECENCY_THRESHOLD = 1  # days to check for recency of data
MIN_DAYS_THRESHOLD = 252 * 1  # minimum number of days of data required for a ticker

# Define file paths
TICKER_FILE = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\ticker_data\processed\tickers.xlsx'
DATA_STORAGE_DIR = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\efficient_data_storage'
LEGACY_CSV_FILE = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\returns_data.csv'

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
        
        logger.info(f"Loaded {len(cleaned_tickers)} unique tickers from {ticker_file}")
        return cleaned_tickers
        
    except Exception as e:
        logger.error(f"Error loading ticker file {ticker_file}: {str(e)}")
        return []

def is_data_up_to_date(dm: EfficientDataManager, ticker: str) -> bool:
    """Check if a ticker's data is up to date within the recency threshold."""
    last_date = dm.get_last_date(ticker)
    if last_date is None:
        return False
    
    days_behind = (datetime.now().date() - last_date.date()).days
    return days_behind <= RECENCY_THRESHOLD

def main():
    """Main function to download and update ticker data efficiently."""
    
    print("=" * 60)
    print("EFFICIENT YFINANCE DATA DOWNLOADING")
    print("=" * 60)
    
    # Initialize the efficient data manager
    dm = EfficientDataManager(
        data_dir=DATA_STORAGE_DIR,
        min_days_threshold=MIN_DAYS_THRESHOLD
    )
    
    # Load ticker list
    logger.info("Loading ticker list...")
    unique_tickers = load_ticker_list(TICKER_FILE)
    
    if not unique_tickers:
        logger.error("No tickers loaded. Exiting.")
        return
    
    # Test with subset for debugging (uncomment next line for testing)
    # unique_tickers = unique_tickers[:10]  # Use only the first ten for testing
    
    logger.info(f"Processing {len(unique_tickers)} tickers...")
    
    # Check current data status
    summary_before = dm.get_data_summary()
    missing_summary = dm.get_missing_data_summary()
    
    if not summary_before.empty:
        up_to_date_count = sum(~missing_summary['needs_update'])
        outdated_count = sum(missing_summary['needs_update'])
        new_tickers = len(unique_tickers) - len(summary_before)
        
        logger.info(f"Current status: {up_to_date_count} up-to-date, {outdated_count} need updates, {new_tickers} new tickers")
    else:
        logger.info("No existing data found. Will download all tickers from scratch.")
    
    # Update all tickers (the system will automatically handle incremental updates)
    logger.info("Starting data updates...")
    results = dm.update_multiple_tickers(unique_tickers)
    
    # Report results
    successful = sum(results.values())
    failed = len(results) - successful
    
    print(f"\nUpdate Results:")
    print(f"✓ Successful: {successful}")
    print(f"✗ Failed: {failed}")
    
    if failed > 0:
        failed_tickers = [ticker for ticker, success in results.items() if not success]
        print(f"Failed tickers: {failed_tickers}")
    
    # Show final data summary
    final_summary = dm.get_data_summary()
    if not final_summary.empty:
        print(f"\nFinal Data Summary:")
        print(f"Total tickers: {len(final_summary)}")
        print(f"Date range: {final_summary['first_date'].min().strftime('%Y-%m-%d')} to {final_summary['last_date'].max().strftime('%Y-%m-%d')}")
        print(f"Average days per ticker: {final_summary['total_days'].mean():.0f}")
        
        # Show some examples
        print(f"\nSample of stored data:")
        display_cols = ['first_date', 'last_date', 'total_days', 'last_updated']
        print(final_summary[display_cols].head(10).to_string())
    
    # For compatibility with existing code, create a CSV export
    print(f"\nCreating compatibility CSV export...")
    try:
        returns_matrix = dm.build_returns_matrix(unique_tickers)
        
        if not returns_matrix.empty:
            # Save to the original CSV location for backward compatibility
            returns_matrix.to_csv(LEGACY_CSV_FILE)
            logger.info(f"Returns data exported to {LEGACY_CSV_FILE} for backward compatibility")
            print(f"Returns matrix shape: {returns_matrix.shape}")
            print("Note: CSV file created for backward compatibility, but consider updating your scripts to use the efficient data manager directly.")
        else:
            logger.warning("No returns data available to export")
            
    except Exception as e:
        logger.error(f"Error creating CSV export: {str(e)}")
    
    print("\n" + "=" * 60)
    print("DATA UPDATE COMPLETE!")
    print("=" * 60)
    
    # Show efficiency gains
    print(f"\nEfficiency Benefits:")
    print(f"• Data stored in compressed Parquet format")
    print(f"• Only downloaded missing/new data (incremental updates)")
    print(f"• Individual ticker files for faster partial loading")
    print(f"• Metadata tracking for better data management")
    
    return dm, results

def get_returns_data_efficient(ticker_file: str = TICKER_FILE) -> pd.DataFrame:
    """
    Efficient replacement for the original CSV loading function.
    
    This function can be used as a drop-in replacement in your existing scripts.
    
    Returns:
        DataFrame with returns data (same format as the original CSV approach)
    """
    # Load tickers
    tickers = load_ticker_list(ticker_file)
    
    # Initialize data manager
    dm = EfficientDataManager(
        data_dir=DATA_STORAGE_DIR,
        min_days_threshold=MIN_DAYS_THRESHOLD
    )
    
    # Update data (incremental - only downloads what's missing)
    dm.update_multiple_tickers(tickers)
    
    # Return the returns matrix
    return dm.build_returns_matrix(tickers)

def force_refresh_all_data():
    """Force a complete refresh of all data (use sparingly)."""
    tickers = load_ticker_list(TICKER_FILE)
    dm = EfficientDataManager(data_dir=DATA_STORAGE_DIR, min_days_threshold=MIN_DAYS_THRESHOLD)
    
    logger.info("Forcing complete data refresh...")
    results = dm.update_multiple_tickers(tickers, force_full_download=True)
    
    return results

if __name__ == "__main__":
    # Run the main update process
    data_manager, update_results = main()
    
    # Example of how to use the data
    print(f"\nExample Usage:")
    print(f"# Get returns matrix for analysis")
    print(f"returns_data = get_returns_data_efficient()")
    print(f"print(returns_data.shape)")
    
    # Demonstration
    sample_returns = get_returns_data_efficient()
    if not sample_returns.empty:
        print(f"\nActual returns matrix: {sample_returns.shape}")
        print("First few rows and columns:")
        print(sample_returns.iloc[:5, :5])
