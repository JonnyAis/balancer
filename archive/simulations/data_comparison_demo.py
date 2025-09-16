"""
Demo comparing the old CSV-based system with the new efficient Parquet-based system.
This demonstrates performance improvements and feature differences.
"""

import time
import pandas as pd
import os
from efficient_data_manager import EfficientDataManager
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def benchmark_old_csv_system():
    """Benchmark the old CSV-based data loading."""
    csv_file = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\returns_data.csv'
    
    if not os.path.exists(csv_file):
        logger.warning("Legacy CSV file not found, skipping CSV benchmark")
        return None, None
    
    logger.info("Benchmarking old CSV system...")
    start_time = time.time()
    
    try:
        returns_data = pd.read_csv(csv_file, index_col=0, parse_dates=True)
        load_time = time.time() - start_time
        
        # Calculate some basic statistics
        stats = {
            'shape': returns_data.shape,
            'memory_usage': returns_data.memory_usage(deep=True).sum() / 1024**2,  # MB
            'date_range': (returns_data.index.min(), returns_data.index.max()),
            'has_nulls': returns_data.isnull().any().any()
        }
        
        logger.info(f"CSV loading completed in {load_time:.2f} seconds")
        return load_time, stats
        
    except Exception as e:
        logger.error(f"Error loading CSV data: {e}")
        return None, None

def benchmark_new_parquet_system():
    """Benchmark the new Parquet-based data system."""
    logger.info("Benchmarking new Parquet system...")
    start_time = time.time()
    
    try:
        data_manager = EfficientDataManager()
        returns_data = data_manager.get_returns_matrix()
        load_time = time.time() - start_time
        
        # Calculate some basic statistics
        stats = {
            'shape': returns_data.shape,
            'memory_usage': returns_data.memory_usage(deep=True).sum() / 1024**2,  # MB
            'date_range': (returns_data.index.min(), returns_data.index.max()),
            'has_nulls': returns_data.isnull().any().any(),
            'metadata_available': len(data_manager.metadata)
        }
        
        logger.info(f"Parquet loading completed in {load_time:.2f} seconds")
        return load_time, stats
        
    except Exception as e:
        logger.error(f"Error loading Parquet data: {e}")
        return None, None

def compare_data_access_patterns():
    """Compare different data access patterns between systems."""
    logger.info("Comparing data access patterns...")
    
    # Test single ticker access
    test_ticker = 'AAPL'
    
    # CSV approach (if available)
    csv_file = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\returns_data.csv'
    if os.path.exists(csv_file):
        start_time = time.time()
        csv_data = pd.read_csv(csv_file, index_col=0, parse_dates=True)
        if test_ticker in csv_data.columns:
            single_ticker_csv = csv_data[test_ticker]
            csv_single_access_time = time.time() - start_time
        else:
            csv_single_access_time = None
    else:
        csv_single_access_time = None
    
    # Parquet approach
    start_time = time.time()
    data_manager = EfficientDataManager()
    try:
        single_ticker_parquet = data_manager.get_ticker_data(test_ticker)
        parquet_single_access_time = time.time() - start_time
    except:
        parquet_single_access_time = None
    
    # Test subset access (10 random tickers)
    data_manager = EfficientDataManager()
    returns_data = data_manager.get_returns_matrix()
    available_tickers = list(returns_data.columns)[:10]  # First 10 tickers
    
    # CSV subset access
    if os.path.exists(csv_file):
        start_time = time.time()
        csv_data = pd.read_csv(csv_file, index_col=0, parse_dates=True)
        csv_subset = csv_data[available_tickers]
        csv_subset_time = time.time() - start_time
    else:
        csv_subset_time = None
    
    # Parquet subset access
    start_time = time.time()
    parquet_subset = data_manager.get_returns_matrix(tickers=available_tickers)
    parquet_subset_time = time.time() - start_time
    
    return {
        'csv_single_access': csv_single_access_time,
        'parquet_single_access': parquet_single_access_time,
        'csv_subset_access': csv_subset_time,
        'parquet_subset_access': parquet_subset_time
    }

def demonstrate_new_features():
    """Demonstrate features only available in the new system."""
    logger.info("Demonstrating new system features...")
    
    data_manager = EfficientDataManager()
    
    # Feature 1: Metadata access
    metadata = data_manager.get_metadata_summary()
    logger.info(f"Metadata summary: {metadata}")
    
    # Feature 2: Data recency check
    is_recent = data_manager.is_data_recent(days=1)
    logger.info(f"Data is recent (within 1 day): {is_recent}")
    
    # Feature 3: Individual ticker last update
    sample_ticker = list(data_manager.metadata['ticker'])[0] if len(data_manager.metadata) > 0 else None
    if sample_ticker:
        last_update = data_manager.get_ticker_last_update(sample_ticker)
        logger.info(f"Last update for {sample_ticker}: {last_update}")
    
    # Feature 4: Incremental update capability
    logger.info("New system supports incremental updates - only downloads missing data")
    
    # Feature 5: Better error handling and validation
    logger.info("New system includes automatic data validation and error recovery")

def main():
    """Run the complete comparison demo."""
    print("=" * 60)
    print("DATA SYSTEM COMPARISON DEMO")
    print("=" * 60)
    
    # Benchmark both systems
    csv_time, csv_stats = benchmark_old_csv_system()
    parquet_time, parquet_stats = benchmark_new_parquet_system()
    
    # Display loading performance comparison
    print("\n📊 LOADING PERFORMANCE COMPARISON:")
    print("-" * 40)
    if csv_time is not None:
        print(f"CSV System:     {csv_time:.3f} seconds")
    else:
        print("CSV System:     Not available")
    
    if parquet_time is not None:
        print(f"Parquet System: {parquet_time:.3f} seconds")
        if csv_time is not None:
            speedup = csv_time / parquet_time
            print(f"Speedup:        {speedup:.2f}x faster")
    
    # Display data comparison
    print("\n📈 DATA COMPARISON:")
    print("-" * 40)
    if csv_stats and parquet_stats:
        print(f"CSV shape:      {csv_stats['shape']}")
        print(f"Parquet shape:  {parquet_stats['shape']}")
        print(f"CSV memory:     {csv_stats['memory_usage']:.1f} MB")
        print(f"Parquet memory: {parquet_stats['memory_usage']:.1f} MB")
    
    # Compare access patterns
    access_times = compare_data_access_patterns()
    print("\n⚡ ACCESS PATTERN COMPARISON:")
    print("-" * 40)
    if access_times['csv_single_access'] is not None and access_times['parquet_single_access'] is not None:
        print(f"Single ticker - CSV:     {access_times['csv_single_access']:.3f}s")
        print(f"Single ticker - Parquet: {access_times['parquet_single_access']:.3f}s")
    
    if access_times['csv_subset_access'] is not None and access_times['parquet_subset_access'] is not None:
        print(f"Subset access - CSV:     {access_times['csv_subset_access']:.3f}s")
        print(f"Subset access - Parquet: {access_times['parquet_subset_access']:.3f}s")
    
    # Demonstrate new features
    print("\n🚀 NEW SYSTEM FEATURES:")
    print("-" * 40)
    demonstrate_new_features()
    
    print("\n✅ Demo completed!")
    print("\nKey advantages of the new system:")
    print("• Faster data loading with Parquet format")
    print("• Incremental updates (only download missing data)")
    print("• Built-in metadata tracking")
    print("• Better error handling and validation")
    print("• Memory efficient data access")
    print("• Support for individual ticker queries")

if __name__ == "__main__":
    main()
