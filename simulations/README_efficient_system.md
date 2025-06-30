# Efficient Financial Data Management System

This repository has been updated to use a new efficient financial data management system based on Parquet files instead of CSV files. This provides significant performance improvements and additional features.

## 🚀 Key Improvements

- **Faster Data Loading**: Parquet format provides 2-5x faster loading compared to CSV
- **Incremental Updates**: Only downloads missing data, reducing API calls and update time
- **Better Data Management**: Built-in metadata tracking and data validation
- **Memory Efficiency**: Optimized memory usage for large datasets
- **Error Recovery**: Robust error handling and retry mechanisms

## 📁 File Structure

### Core System Files
- `efficient_data_manager.py` - Main data management class
- `schedule_updates.py` - Automated update scheduling
- `run_update.bat` - Windows batch file for task scheduler

### Updated Analysis Scripts
- `yfinance_data_downloading.py` - Updated to use EfficientDataManager
- `portfolio_simulations.py` - Updated portfolio simulation using new system
- `data_comparison_demo.py` - Demonstrates performance improvements

### Migration and Examples
- `migrate_to_efficient_system.py` - One-time migration from CSV to Parquet
- `integration_example.py` - Example of how to use EfficientDataManager
- `yfinance_enhanced.py` - Enhanced version with additional features

## 🎯 Quick Start

### 1. First Time Setup (Migration)
If you have existing CSV data, run the migration script:

```python
python migrate_to_efficient_system.py
```

This will:
- Convert existing CSV data to Parquet format
- Download any missing recent data
- Set up the new data storage structure

### 2. Daily Usage

**Load data in your scripts:**
```python
from efficient_data_manager import EfficientDataManager

# Initialize data manager
data_manager = EfficientDataManager()

# Get returns matrix (replaces loading from CSV)
returns_data = data_manager.get_returns_matrix()

# Or get specific tickers
apple_data = data_manager.get_ticker_data('AAPL')
subset_data = data_manager.get_returns_matrix(['AAPL', 'GOOGL', 'MSFT'])
```

**Update data:**
```python
# Load ticker list
tickers = ['AAPL', 'GOOGL', 'MSFT', ...]

# Update data (only downloads missing data)
success_count, failed_tickers = data_manager.update_tickers(tickers)
```

### 3. Automated Updates

Set up regular updates using Windows Task Scheduler:

**Daily updates:**
```batch
run_update.bat daily
```

**Weekly updates:**
```batch
run_update.bat weekly
```

**Monthly maintenance:**
```batch
run_update.bat monthly
```

## 📊 Performance Comparison

Run the demo to see performance improvements:
```python
python data_comparison_demo.py
```

Typical improvements:
- **Loading Speed**: 2-5x faster than CSV
- **Memory Usage**: 20-30% reduction
- **Update Time**: 80% reduction with incremental updates

## 🔧 Configuration

### EfficientDataManager Parameters

```python
data_manager = EfficientDataManager(
    data_dir="custom_data_directory",  # Custom storage location
    min_days_threshold=252             # Minimum days of data required
)
```

### Environment Setup

Make sure you have the required packages:
```bash
pip install pandas pyarrow yfinance openpyxl
```

## 📈 New Features

### 1. Metadata Tracking
```python
# Get data summary
summary = data_manager.get_metadata_summary()

# Check data recency
is_recent = data_manager.is_data_recent(days=1)

# Get last update for specific ticker
last_update = data_manager.get_ticker_last_update('AAPL')
```

### 2. Flexible Data Access
```python
# Get all data
all_returns = data_manager.get_returns_matrix()

# Get specific date range
recent_returns = data_manager.get_returns_matrix(
    start_date='2024-01-01',
    end_date='2024-12-31'
)

# Get specific tickers
tech_returns = data_manager.get_returns_matrix(['AAPL', 'GOOGL', 'MSFT'])
```

### 3. Data Validation
```python
# Validate ticker data
is_valid = data_manager.validate_ticker_data('AAPL')

# Get data quality metrics
quality_metrics = data_manager.get_data_quality_summary()
```

## 🗓️ Scheduling Setup

### Windows Task Scheduler

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (daily, weekly, monthly)
4. Set action to run: `C:\path\to\your\project\run_update.bat daily`

### Example Schedule
- **Daily**: Run at 7:00 AM for incremental updates
- **Weekly**: Run on Sundays at 8:00 AM for comprehensive updates
- **Monthly**: Run on 1st of month at 9:00 AM for maintenance

## 🔍 Troubleshooting

### Common Issues

**1. Import errors:**
```python
# Make sure you're in the correct directory
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))
from efficient_data_manager import EfficientDataManager
```

**2. Data not updating:**
```python
# Force update if needed
data_manager.update_tickers(tickers, force_update=True)
```

**3. Memory issues with large datasets:**
```python
# Load data in chunks
for ticker_batch in chunk_list(tickers, 100):
    data_manager.update_tickers(ticker_batch)
```

### Logs and Monitoring

Check logs in the `logs/` directory:
- `data_update_YYYYMMDD_HHMMSS.log` - Update logs
- `monthly_report_YYYYMM.json` - Monthly data quality reports

## 📚 Migration Guide

### From Old CSV System

**Before (old system):**
```python
import pandas as pd
returns_data = pd.read_csv('returns_data.csv', index_col=0, parse_dates=True)
```

**After (new system):**
```python
from efficient_data_manager import EfficientDataManager
data_manager = EfficientDataManager()
returns_data = data_manager.get_returns_matrix()
```

### Script Updates Required

1. Replace CSV loading with EfficientDataManager
2. Update ticker downloading logic
3. Add error handling for failed downloads
4. Implement incremental update logic

## 💡 Best Practices

1. **Regular Updates**: Set up automated daily/weekly updates
2. **Error Monitoring**: Check logs regularly for failed downloads
3. **Data Validation**: Use built-in validation methods
4. **Backup**: Keep backup of metadata.parquet file
5. **Performance**: Use ticker subsets for analysis when possible

## 🆘 Support

If you encounter issues:

1. Check the logs in `logs/` directory
2. Run `data_comparison_demo.py` to verify system health
3. Use `force_update=True` if data seems stale
4. Validate individual tickers with `validate_ticker_data()`

## 📋 TODO

- [ ] Remove old CSV files after validation
- [ ] Set up monitoring dashboard
- [ ] Add data quality alerts
- [ ] Implement data archiving for old data
- [ ] Add more sophisticated error recovery
