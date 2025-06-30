import os
import pandas as pd
import yfinance as yf
import numpy as np
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
import logging
from pathlib import Path
import warnings

# Suppress yfinance warnings
warnings.filterwarnings("ignore", category=FutureWarning)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class EfficientDataManager:
    """
    Efficient financial data manager using Parquet files for storage
    and incremental updates to minimize API calls.
    """
    
    def __init__(self, data_dir: str = None, min_days_threshold: int = 252):
        """
        Initialize the data manager.
        
        Args:
            data_dir: Directory to store Parquet files. Defaults to 'data_storage'
            min_days_threshold: Minimum number of days of data required for a ticker
        """
        if data_dir is None:
            # Use a subdirectory in the current simulations folder
            self.data_dir = Path(__file__).parent / "data_storage"
        else:
            self.data_dir = Path(data_dir)
        
        # Create data directory if it doesn't exist
        self.data_dir.mkdir(exist_ok=True)
        
        self.min_days_threshold = min_days_threshold
        self.metadata_file = self.data_dir / "metadata.parquet"
        
        # Load or create metadata
        self._load_metadata()
    
    def _load_metadata(self):
        """Load metadata about stored tickers or create empty metadata."""
        if self.metadata_file.exists():
            self.metadata = pd.read_parquet(self.metadata_file)
        else:
            self.metadata = pd.DataFrame(columns=[
                'ticker', 'first_date', 'last_date', 'total_days', 'last_updated'
            ]).set_index('ticker')
    
    def _save_metadata(self):
        """Save metadata to Parquet file."""
        self.metadata.to_parquet(self.metadata_file)
    
    def _get_ticker_file_path(self, ticker: str) -> Path:
        """Get the file path for a ticker's data."""
        return self.data_dir / f"{ticker}.parquet"
    
    def get_last_date(self, ticker: str) -> Optional[pd.Timestamp]:
        """
        Get the last available date for a ticker.
        
        Args:
            ticker: Stock ticker symbol
            
        Returns:
            Last available date or None if no data exists
        """
        if ticker in self.metadata.index:
            return pd.to_datetime(self.metadata.loc[ticker, 'last_date'])
        return None
    
    def get_data_summary(self) -> pd.DataFrame:
        """Get a summary of all stored data."""
        return self.metadata.copy()
    
    def download_ticker_data(self, ticker: str, start_date: str = None, 
                           force_full_download: bool = False) -> bool:
        """
        Download and store data for a single ticker with incremental updates.
        
        Args:
            ticker: Stock ticker symbol
            start_date: Start date for download (YYYY-MM-DD). If None, uses last available date + 1
            force_full_download: If True, downloads all available data from scratch
            
        Returns:
            True if successful, False otherwise
        """
        try:
            ticker = ticker.upper().strip()
            file_path = self._get_ticker_file_path(ticker)
            
            # Determine start date for download
            if force_full_download or not file_path.exists():
                # Full download
                start = start_date or "2000-01-01"
                logger.info(f"Full download for {ticker} starting from {start}")
            else:
                # Incremental update
                last_date = self.get_last_date(ticker)
                if last_date is None:
                    start = start_date or "2000-01-01"
                else:
                    # Start from the day after the last available date
                    start = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                    
                # Check if we need to update (skip if already up to date)
                today = datetime.now().date()
                last_date_date = last_date.date() if last_date else None
                if last_date_date and last_date_date >= today - timedelta(days=1):
                    logger.info(f"{ticker} is already up to date")
                    return True
                
                logger.info(f"Incremental update for {ticker} starting from {start}")
            
            # Download data from yfinance
            data = yf.download(
                tickers=ticker,
                start=start,
                auto_adjust=True,
                progress=False
            )
            
            if data.empty:
                logger.warning(f"No data downloaded for {ticker}")
                return False
            
            # Prepare the data
            if len(data.columns.levels) > 1:  # Multi-level columns (when downloading multiple tickers)
                data = data.xs(ticker, level=1, axis=1)
            
            # Keep only Close price and calculate returns
            df_new = pd.DataFrame({
                'Close': data['Close'],
                'Volume': data.get('Volume', np.nan),
                'Return': data['Close'].pct_change()
            })
            
            # Remove any rows with NaN Close prices
            df_new = df_new.dropna(subset=['Close'])
            
            if len(df_new) < self.min_days_threshold and not file_path.exists():
                logger.warning(f"Insufficient data for {ticker}: {len(df_new)} days (minimum: {self.min_days_threshold})")
                return False
            
            # Handle existing data
            if file_path.exists() and not force_full_download:
                # Load existing data and append new data
                df_existing = pd.read_parquet(file_path)
                
                # Combine and remove duplicates (keep latest)
                df_combined = pd.concat([df_existing, df_new])
                df_combined = df_combined[~df_combined.index.duplicated(keep='last')]
                df_combined = df_combined.sort_index()
                
                # Recalculate returns for the entire series to ensure consistency
                df_combined['Return'] = df_combined['Close'].pct_change()
                
                df_final = df_combined
            else:
                df_final = df_new
            
            # Save to Parquet
            df_final.to_parquet(file_path)
            
            # Update metadata
            self.metadata.loc[ticker] = {
                'first_date': df_final.index.min(),
                'last_date': df_final.index.max(),
                'total_days': len(df_final),
                'last_updated': datetime.now()
            }
            self._save_metadata()
            
            logger.info(f"Successfully updated {ticker}: {len(df_final)} days of data")
            return True
            
        except Exception as e:
            logger.error(f"Error downloading data for {ticker}: {str(e)}")
            return False
    
    def update_multiple_tickers(self, tickers: List[str], 
                              start_date: str = None,
                              force_full_download: bool = False) -> Dict[str, bool]:
        """
        Update data for multiple tickers.
        
        Args:
            tickers: List of ticker symbols
            start_date: Start date for download (YYYY-MM-DD)
            force_full_download: If True, downloads all data from scratch
            
        Returns:
            Dictionary mapping ticker to success status
        """
        results = {}
        
        for ticker in tickers:
            logger.info(f"Processing {ticker}...")
            results[ticker] = self.download_ticker_data(
                ticker, start_date, force_full_download
            )
        
        return results
    
    def get_ticker_data(self, ticker: str, 
                       start_date: str = None, 
                       end_date: str = None) -> Optional[pd.DataFrame]:
        """
        Retrieve data for a single ticker.
        
        Args:
            ticker: Stock ticker symbol
            start_date: Start date filter (YYYY-MM-DD)
            end_date: End date filter (YYYY-MM-DD)
            
        Returns:
            DataFrame with ticker data or None if not found
        """
        ticker = ticker.upper().strip()
        file_path = self._get_ticker_file_path(ticker)
        
        if not file_path.exists():
            logger.warning(f"No data found for ticker {ticker}")
            return None
        
        try:
            df = pd.read_parquet(file_path)
            
            # Apply date filters if provided
            if start_date:
                df = df[df.index >= start_date]
            if end_date:
                df = df[df.index <= end_date]
            
            return df
            
        except Exception as e:
            logger.error(f"Error reading data for {ticker}: {str(e)}")
            return None
    
    def build_returns_matrix(self, tickers: List[str] = None,
                           start_date: str = None,
                           end_date: str = None,
                           fill_method: str = 'forward') -> pd.DataFrame:
        """
        Build a returns matrix from stored data.
        
        Args:
            tickers: List of tickers to include. If None, uses all available tickers
            start_date: Start date filter (YYYY-MM-DD)
            end_date: End date filter (YYYY-MM-DD)
            fill_method: Method to handle missing values ('forward', 'backward', 'drop', None)
            
        Returns:
            DataFrame with returns for all tickers
        """
        if tickers is None:
            tickers = list(self.metadata.index)
        
        returns_data = {}
        
        for ticker in tickers:
            df = self.get_ticker_data(ticker, start_date, end_date)
            if df is not None and not df.empty:
                returns_data[ticker] = df['Return']
        
        if not returns_data:
            logger.warning("No data found for any tickers")
            return pd.DataFrame()
        
        # Combine all returns into a single DataFrame
        returns_matrix = pd.DataFrame(returns_data)
        
        # Handle missing values
        if fill_method == 'forward':
            returns_matrix = returns_matrix.fillna(method='ffill')
        elif fill_method == 'backward':
            returns_matrix = returns_matrix.fillna(method='bfill')
        elif fill_method == 'drop':
            returns_matrix = returns_matrix.dropna()
        # If fill_method is None, leave NaN values as-is
        
        return returns_matrix
    
    def get_missing_data_summary(self) -> pd.DataFrame:
        """
        Get a summary of data gaps and missing recent data.
        
        Returns:
            DataFrame with information about data gaps
        """
        summary_data = []
        today = datetime.now().date()
        
        for ticker in self.metadata.index:
            last_date = pd.to_datetime(self.metadata.loc[ticker, 'last_date']).date()
            days_behind = (today - last_date).days
            
            summary_data.append({
                'ticker': ticker,
                'last_date': last_date,
                'days_behind': days_behind,
                'needs_update': days_behind > 1,
                'total_days': self.metadata.loc[ticker, 'total_days']
            })
        
        return pd.DataFrame(summary_data).set_index('ticker')
    
    def cleanup_old_data(self, days_to_keep: int = 365*5):
        """
        Remove old data beyond a certain number of days to save space.
        
        Args:
            days_to_keep: Number of days of data to keep
        """
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        for ticker in self.metadata.index:
            file_path = self._get_ticker_file_path(ticker)
            if file_path.exists():
                try:
                    df = pd.read_parquet(file_path)
                    df_filtered = df[df.index >= cutoff_date]
                    
                    if len(df_filtered) < len(df):
                        df_filtered.to_parquet(file_path)
                        
                        # Update metadata
                        self.metadata.loc[ticker, 'first_date'] = df_filtered.index.min()
                        self.metadata.loc[ticker, 'total_days'] = len(df_filtered)
                        
                        logger.info(f"Cleaned up {ticker}: removed {len(df) - len(df_filtered)} old records")
                
                except Exception as e:
                    logger.error(f"Error cleaning up {ticker}: {str(e)}")
        
        self._save_metadata()

    def is_data_recent(self, days: int = 1) -> bool:
        """
        Check if the data is recent (within specified days).
        
        Args:
            days: Number of days to consider "recent"
            
        Returns:
            True if data is recent, False otherwise
        """
        if self.metadata.empty:
            return False
        
        # Get the most recent date across all tickers
        try:
            latest_dates = pd.to_datetime(self.metadata['last_date'])
            most_recent = latest_dates.max()
            
            if pd.isna(most_recent):
                return False
            
            # Check if the most recent data is within the threshold
            cutoff_date = pd.Timestamp.now() - pd.Timedelta(days=days)
            return most_recent >= cutoff_date
            
        except Exception as e:
            logger.error(f"Error checking data recency: {e}")
            return False
    
    def get_metadata_summary(self) -> dict:
        """
        Get a summary of metadata information.
        
        Returns:
            Dictionary with metadata summary
        """
        if self.metadata.empty:
            return {'total_tickers': 0, 'date_range': None}
        
        try:
            dates = pd.to_datetime(self.metadata['last_date'])
            return {
                'total_tickers': len(self.metadata),
                'date_range': {
                    'earliest': dates.min().isoformat() if not dates.empty else None,
                    'latest': dates.max().isoformat() if not dates.empty else None
                },
                'avg_days_data': self.metadata['total_days'].mean() if 'total_days' in self.metadata.columns else None
            }
        except Exception as e:
            logger.error(f"Error getting metadata summary: {e}")
            return {'total_tickers': 0, 'date_range': None}
    
    def update_tickers(self, tickers: List[str], max_workers: int = 5, force_update: bool = False) -> Tuple[int, List[str]]:
        """
        Update multiple tickers with improved interface.
        
        Args:
            tickers: List of ticker symbols
            max_workers: Number of parallel workers
            force_update: Force full download even if data exists
            
        Returns:
            Tuple of (success_count, failed_tickers)
        """
        results = self.update_multiple_tickers(tickers, force_full_download=force_update)
        
        success_count = sum(1 for success in results.values() if success)
        failed_tickers = [ticker for ticker, success in results.items() if not success]
        
        return success_count, failed_tickers
    
    def get_returns_matrix(self, tickers: List[str] = None, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """
        Get returns matrix with simplified interface.
        
        Args:
            tickers: List of tickers (None for all)
            start_date: Start date filter
            end_date: End date filter
            
        Returns:
            DataFrame with returns data
        """
        return self.build_returns_matrix(tickers, start_date, end_date)
    
    def get_ticker_last_update(self, ticker: str) -> Optional[str]:
        """
        Get the last update date for a specific ticker.
        
        Args:
            ticker: Ticker symbol
            
        Returns:
            Last update date as ISO string or None
        """
        last_date = self.get_last_date(ticker)
        return last_date.isoformat() if last_date else None

    def validate_ticker_data(self, ticker: str) -> bool:
        """
        Validate that ticker data meets minimum requirements.
        
        Args:
            ticker: Ticker symbol
            
        Returns:
            True if data is valid
        """
        try:
            data = self.get_ticker_data(ticker)
            if data is None or data.empty:
                return False
            
            return len(data) >= self.min_days_threshold
            
        except Exception:
            return False
    
    def get_data_quality_summary(self) -> dict:
        """
        Get a summary of data quality metrics.
        
        Returns:
            Dictionary with quality metrics
        """
        try:
            returns_matrix = self.get_returns_matrix()
            if returns_matrix.empty:
                return {'status': 'no_data'}
            
            total_cells = returns_matrix.size
            null_cells = returns_matrix.isnull().sum().sum()
            
            return {
                'total_tickers': len(returns_matrix.columns),
                'total_observations': total_cells,
                'null_percentage': (null_cells / total_cells * 100) if total_cells > 0 else 0,
                'date_range': {
                    'start': returns_matrix.index.min().isoformat(),
                    'end': returns_matrix.index.max().isoformat()
                },
                'complete_series_count': (returns_matrix.count() == len(returns_matrix)).sum()
            }
            
        except Exception as e:
            logger.error(f"Error getting data quality summary: {e}")
            return {'status': 'error', 'message': str(e)}

def migrate_from_csv(csv_file_path: str, data_manager: EfficientDataManager):
    """
    Migrate existing CSV data to the new Parquet-based system.
    
    Args:
        csv_file_path: Path to existing returns CSV file
        data_manager: Instance of EfficientDataManager
    """
    try:
        logger.info("Starting migration from CSV...")
        
        # Read existing CSV
        returns_data = pd.read_csv(csv_file_path, index_col=0, parse_dates=True)
        
        for ticker in returns_data.columns:
            ticker_data = returns_data[ticker].dropna()
            
            if len(ticker_data) < data_manager.min_days_threshold:
                logger.warning(f"Skipping {ticker}: insufficient data ({len(ticker_data)} days)")
                continue
            
            # Create DataFrame with required structure
            df = pd.DataFrame({
                'Close': np.nan,  # We don't have close prices in the CSV
                'Volume': np.nan,  # We don't have volume in the CSV
                'Return': ticker_data
            })
            
            # Save to Parquet
            file_path = data_manager._get_ticker_file_path(ticker)
            df.to_parquet(file_path)
            
            # Update metadata
            data_manager.metadata.loc[ticker] = {
                'first_date': df.index.min(),
                'last_date': df.index.max(),
                'total_days': len(df),
                'last_updated': datetime.now()
            }
            
            logger.info(f"Migrated {ticker}: {len(df)} days of data")
        
        data_manager._save_metadata()
        logger.info("Migration completed successfully!")
        
    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")


# Example usage and testing functions
if __name__ == "__main__":
    # Initialize the data manager
    dm = EfficientDataManager()
    
    # Example ticker list (you can load this from your Excel file)
    example_tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']
    
    # Update data for tickers
    print("Updating ticker data...")
    results = dm.update_multiple_tickers(example_tickers)
    
    # Show results
    for ticker, success in results.items():
        status = "SUCCESS" if success else "FAILED"
        print(f"{ticker}: {status}")
    
    # Show data summary
    print("\nData Summary:")
    print(dm.get_data_summary())
    
    # Build returns matrix
    print("\nBuilding returns matrix...")
    returns_matrix = dm.build_returns_matrix(example_tickers)
    print(f"Returns matrix shape: {returns_matrix.shape}")
    print(returns_matrix.head())
    
    # Show missing data summary
    print("\nMissing Data Summary:")
    missing_summary = dm.get_missing_data_summary()
    print(missing_summary)