"""
Automated scheduling script for regular data updates using EfficientDataManager.
This script can be run via Windows Task Scheduler or cron for regular updates.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
import argparse
from pathlib import Path

# Add the current directory to Python path for imports
sys.path.append(str(Path(__file__).parent))

from efficient_data_manager import EfficientDataManager
import pandas as pd

# Setup logging with file output
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"data_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_ticker_list():
    """Load the ticker list from the Excel file."""
    ticker_file = Path(__file__).parent / "ticker_data" / "processed" / "tickers.xlsx"
    
    try:
        ticker_data = pd.read_excel(
            ticker_file,
            sheet_name='Sheet1',
            header=0,
            usecols='A:B'
        )
        
        # Convert and clean ticker list
        ticker_data['Ticker'] = ticker_data['Ticker'].astype(str)
        unique_tickers = ticker_data['Ticker'].unique()
        
        cleaned_tickers = []
        for ticker in unique_tickers:
            if ticker and ticker.lower() != 'nan':
                cleaned_tickers.append(ticker.strip().upper())
        
        logger.info(f"Loaded {len(cleaned_tickers)} tickers from {ticker_file}")
        return cleaned_tickers
        
    except Exception as e:
        logger.error(f"Error loading ticker list: {e}")
        return []

def daily_update():
    """Perform daily data update - light incremental update."""
    logger.info("Starting daily update process")
    
    # Initialize data manager
    data_manager = EfficientDataManager()
    
    # Check if update is needed
    if data_manager.is_data_recent(days=1):
        logger.info("Data is already up-to-date for daily update")
        return True
    
    # Load ticker list
    tickers = load_ticker_list()
    if not tickers:
        logger.error("No tickers loaded, aborting update")
        return False
    
    # Perform incremental update for all tickers
    logger.info(f"Updating {len(tickers)} tickers")
    success_count, failed_tickers = data_manager.update_tickers(
        tickers, 
        max_workers=3,  # Conservative for daily updates
        force_update=False
    )
    
    logger.info(f"Daily update completed: {success_count} successful, {len(failed_tickers)} failed")
    
    if failed_tickers:
        logger.warning(f"Failed tickers: {failed_tickers[:10]}...")  # Log first 10
    
    # Log summary
    metadata_summary = data_manager.get_metadata_summary()
    logger.info(f"Current data summary: {metadata_summary}")
    
    return len(failed_tickers) < len(tickers) * 0.1  # Success if < 10% failure rate

def weekly_update():
    """Perform weekly data update - more comprehensive update with validation."""
    logger.info("Starting weekly update process")
    
    # Initialize data manager
    data_manager = EfficientDataManager()
    
    # Load ticker list
    tickers = load_ticker_list()
    if not tickers:
        logger.error("No tickers loaded, aborting update")
        return False
    
    # Perform more comprehensive update
    logger.info(f"Performing comprehensive update for {len(tickers)} tickers")
    success_count, failed_tickers = data_manager.update_tickers(
        tickers, 
        max_workers=5,  # More aggressive for weekly updates
        force_update=False
    )
    
    logger.info(f"Weekly update completed: {success_count} successful, {len(failed_tickers)} failed")
    
    # Validate data quality
    try:
        returns_matrix = data_manager.get_returns_matrix()
        logger.info(f"Returns matrix shape: {returns_matrix.shape}")
        logger.info(f"Date range: {returns_matrix.index.min()} to {returns_matrix.index.max()}")
        
        # Check for data quality issues
        null_percentage = returns_matrix.isnull().sum().sum() / returns_matrix.size * 100
        logger.info(f"Null data percentage: {null_percentage:.2f}%")
        
        if null_percentage > 50:
            logger.warning("High percentage of null data detected")
        
    except Exception as e:
        logger.error(f"Error validating data: {e}")
        return False
    
    # Log detailed metadata
    metadata_summary = data_manager.get_metadata_summary()
    logger.info(f"Current data summary: {metadata_summary}")
    
    # Cleanup old log files (keep last 30 days)
    cleanup_old_logs()
    
    return len(failed_tickers) < len(tickers) * 0.2  # Success if < 20% failure rate

def monthly_maintenance():
    """Perform monthly maintenance - full validation and cleanup."""
    logger.info("Starting monthly maintenance process")
    
    # Initialize data manager
    data_manager = EfficientDataManager()
    
    # Perform full data validation
    logger.info("Performing full data validation")
    try:
        # Check all stored tickers
        stored_tickers = list(data_manager.metadata['ticker'])
        logger.info(f"Validating {len(stored_tickers)} stored tickers")
        
        problematic_tickers = []
        for ticker in stored_tickers:
            try:
                ticker_data = data_manager.get_ticker_data(ticker)
                if ticker_data is None or len(ticker_data) < 100:  # Less than 100 days
                    problematic_tickers.append(ticker)
            except Exception as e:
                logger.warning(f"Issue with ticker {ticker}: {e}")
                problematic_tickers.append(ticker)
        
        if problematic_tickers:
            logger.warning(f"Found {len(problematic_tickers)} problematic tickers: {problematic_tickers[:10]}...")
        
        # Generate comprehensive report
        generate_monthly_report(data_manager)
        
    except Exception as e:
        logger.error(f"Error during monthly maintenance: {e}")
        return False
    
    return True

def generate_monthly_report(data_manager):
    """Generate a comprehensive monthly report."""
    logger.info("Generating monthly report")
    
    try:
        # Get metadata summary
        metadata = data_manager.get_metadata_summary()
        
        # Get returns matrix
        returns_matrix = data_manager.get_returns_matrix()
        
        report = {
            'report_date': datetime.now().isoformat(),
            'total_tickers': len(metadata.get('total_tickers', 0)),
            'data_shape': returns_matrix.shape,
            'date_range': {
                'start': returns_matrix.index.min().isoformat(),
                'end': returns_matrix.index.max().isoformat()
            },
            'data_quality': {
                'null_percentage': returns_matrix.isnull().sum().sum() / returns_matrix.size * 100,
                'complete_series': (returns_matrix.count() == len(returns_matrix)).sum()
            }
        }
        
        # Save report
        report_file = Path(__file__).parent / "logs" / f"monthly_report_{datetime.now().strftime('%Y%m')}.json"
        import json
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Monthly report saved to {report_file}")
        
    except Exception as e:
        logger.error(f"Error generating monthly report: {e}")

def cleanup_old_logs():
    """Cleanup log files older than 30 days."""
    log_dir = Path(__file__).parent / "logs"
    if not log_dir.exists():
        return
    
    cutoff_date = datetime.now() - timedelta(days=30)
    
    for log_file in log_dir.glob("*.log"):
        if log_file.stat().st_mtime < cutoff_date.timestamp():
            try:
                log_file.unlink()
                logger.info(f"Cleaned up old log file: {log_file}")
            except Exception as e:
                logger.warning(f"Could not delete {log_file}: {e}")

def main():
    """Main function with command line argument parsing."""
    parser = argparse.ArgumentParser(description="Automated data update scheduler")
    parser.add_argument(
        "update_type", 
        choices=["daily", "weekly", "monthly"],
        help="Type of update to perform"
    )
    parser.add_argument(
        "--force", 
        action="store_true",
        help="Force update even if data appears recent"
    )
    
    args = parser.parse_args()
    
    logger.info(f"Starting {args.update_type} update process")
    logger.info(f"Log file: {log_file}")
    
    try:
        if args.update_type == "daily":
            success = daily_update()
        elif args.update_type == "weekly":
            success = weekly_update()
        elif args.update_type == "monthly":
            success = monthly_maintenance()
        
        if success:
            logger.info(f"{args.update_type.title()} update completed successfully")
            sys.exit(0)
        else:
            logger.error(f"{args.update_type.title()} update failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Unexpected error during {args.update_type} update: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
