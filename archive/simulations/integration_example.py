
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

