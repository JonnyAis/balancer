"""
Migration script to transition from CSV-based data storage to efficient Parquet-based system.

This script:
1. Migrates existing CSV data to the new system
2. Updates data with missing information
3. Demonstrates the new workflow
"""

import pandas as pd
import os
from efficient_data_manager import EfficientDataManager, migrate_from_csv
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# File paths (update these to match your actual file paths)
TICKER_EXCEL_FILE = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\ticker_data\processed\tickers.xlsx'
OLD_CSV_FILE = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\returns_data.csv'

def load_ticker_list(excel_file_path: str) -> list:
    """Load the list of tickers from your Excel file."""
    try:
        ticker_data = pd.read_excel(
            excel_file_path,
            sheet_name='Sheet1',
            header=0,
            usecols='A:B'
        )
        ticker_data['Ticker'] = ticker_data['Ticker'].astype(str)
        unique_tickers = ticker_data['Ticker'].unique().tolist()
        
        # Clean up the ticker list
        unique_tickers = [ticker.strip().upper() for ticker in unique_tickers if ticker != 'nan']
        
        logger.info(f"Loaded {len(unique_tickers)} tickers from Excel file")
        return unique_tickers
        
    except Exception as e:
        logger.error(f"Error loading ticker list: {str(e)}")
        return []

def main():
    """Main migration and update process."""
    
    print("=" * 60)
    print("MIGRATING TO EFFICIENT DATA MANAGEMENT SYSTEM")
    print("=" * 60)
    
    # Initialize the efficient data manager
    dm = EfficientDataManager(
        data_dir=r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\efficient_data_storage',
        min_days_threshold=252
    )
    
    # Step 1: Load ticker list
    print("\n1. Loading ticker list...")
    tickers = load_ticker_list(TICKER_EXCEL_FILE)
    
    if not tickers:
        print("No tickers found. Exiting.")
        return
    
    print(f"Found {len(tickers)} tickers: {tickers[:10]}{'...' if len(tickers) > 10 else ''}")
    
    # Step 2: Migrate existing CSV data (if it exists)
    if os.path.exists(OLD_CSV_FILE):
        print(f"\n2. Migrating existing CSV data from {OLD_CSV_FILE}...")
        migrate_from_csv(OLD_CSV_FILE, dm)
    else:
        print("\n2. No existing CSV file found. Starting fresh.")
    
    # Step 3: Update all tickers with missing data
    print("\n3. Updating ticker data (incremental updates)...")
    results = dm.update_multiple_tickers(tickers)
    
    # Show results
    successful = sum(results.values())
    failed = len(results) - successful
    print(f"\nUpdate Results: {successful} successful, {failed} failed")
    
    if failed > 0:
        failed_tickers = [ticker for ticker, success in results.items() if not success]
        print(f"Failed tickers: {failed_tickers}")
    
    # Step 4: Show data summary
    print("\n4. Data Summary:")
    summary = dm.get_data_summary()
    if not summary.empty:
        print(f"Total tickers stored: {len(summary)}")
        print(f"Date range: {summary['first_date'].min()} to {summary['last_date'].max()}")
        print(f"Average days per ticker: {summary['total_days'].mean():.0f}")
        
        # Show sample of the summary
        print("\nSample of stored data:")
        print(summary.head(10))
    
    # Step 5: Check for missing/outdated data
    print("\n5. Missing Data Analysis:")
    missing_summary = dm.get_missing_data_summary()
    if not missing_summary.empty:
        outdated = missing_summary[missing_summary['needs_update']]
        if not outdated.empty:
            print(f"Tickers needing updates: {len(outdated)}")
            print(outdated[['last_date', 'days_behind']].head())
        else:
            print("All data is up to date!")
    
    # Step 6: Demonstrate building returns matrix
    print("\n6. Building Returns Matrix (sample)...")
    sample_tickers = tickers[:5]  # Use first 5 tickers for demo
    returns_matrix = dm.build_returns_matrix(
        tickers=sample_tickers,
        start_date='2023-01-01'  # Get data from 2023 onwards
    )
    
    if not returns_matrix.empty:
        print(f"Returns matrix shape: {returns_matrix.shape}")
        print("Sample returns data:")
        print(returns_matrix.head())
        
        # Save sample returns matrix as CSV for comparison
        sample_csv_path = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\sample_returns_matrix.csv'
        returns_matrix.to_csv(sample_csv_path)
        print(f"\nSample returns matrix saved to: {sample_csv_path}")
    
    print("\n" + "=" * 60)
    print("MIGRATION COMPLETE!")
    print("=" * 60)
    
    print("\nNext steps:")
    print("1. Update your existing scripts to use EfficientDataManager")
    print("2. Set up scheduled updates (daily/weekly)")
    print("3. Consider removing the old CSV file once you're satisfied")
    print("\nExample usage in your other scripts:")
    print("""
from efficient_data_manager import EfficientDataManager

# Initialize
dm = EfficientDataManager()

# Get latest data for analysis
returns_matrix = dm.build_returns_matrix()

# Update specific tickers
dm.update_multiple_tickers(['AAPL', 'MSFT'])
""")

def create_integration_example():
    """Create an example script showing how to integrate with existing code."""
    
    integration_script = '''
"""
Example: How to integrate EfficientDataManager with your existing portfolio optimization code.
Replace the CSV loading section with this more efficient approach.
"""

from efficient_data_manager import EfficientDataManager
import pandas as pd

def get_updated_returns_data(tickers_excel_path, force_update=False):
    """
    Replacement for your current CSV loading code.
    
    Args:
        tickers_excel_path: Path to your tickers Excel file
        force_update: Whether to force download new data
    
    Returns:
        DataFrame with returns data (same format as your CSV)
    """
    # Initialize data manager
    dm = EfficientDataManager()
    
    # Load ticker list from Excel
    ticker_data = pd.read_excel(tickers_excel_path, sheet_name='Sheet1', header=0, usecols='A:B')
    ticker_data['Ticker'] = ticker_data['Ticker'].astype(str)
    tickers = ticker_data['Ticker'].unique().tolist()
    tickers = [t.strip().upper() for t in tickers if t != 'nan']
    
    if force_update:
        # Force update all tickers
        dm.update_multiple_tickers(tickers, force_full_download=True)
    else:
        # Incremental update (only get missing data)
        dm.update_multiple_tickers(tickers)
    
    # Build and return the returns matrix
    returns_matrix = dm.build_returns_matrix(tickers)
    
    return returns_matrix

# Example usage in your existing script:
# Replace this line:
# returns_data = pd.read_csv(returns_csv_file, index_col=0, parse_dates=True)

# With this:
# returns_data = get_updated_returns_data(ticker_file)

'''
    
    with open(r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\integration_example.py', 'w') as f:
        f.write(integration_script)
    
    print("Integration example saved to: integration_example.py")

if __name__ == "__main__":
    main()
    create_integration_example()
