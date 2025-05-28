import os
import pandas as pd
import yfinance as yf
import openpyxl
from datetime import datetime, timedelta


# ---set paramters---
recency_threshold = 5 #days to check for recency of data
min_days_threshold = 252 * 1 #minimum number of days of data required for a ticker to be considered


# Define the directory containing the data files
ticker_file = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\ticker_data\processed\tickers.xlsx'
returns_csv_file = r'C:\Users\jonat\OneDrive\Documents\GitHub\balancer\simulations\returns_data.csv'

# Import the table of tickers from the excel file
ticker_data = pd.read_excel(
    ticker_file,
    sheet_name='Sheet1',
    header=0,
    usecols='A:B'
)

# Convert the Ticker column to a string
ticker_data['Ticker'] = ticker_data['Ticker'].astype(str)

# Find unique values in the 'Ticker' column
unique_tickers = ticker_data['Ticker'].unique()

#test ticker - comment this line out if running full download
unique_tickers = unique_tickers[:5] # Use only the first five for testing

# Function to check if the CSV file is up-to-date
def is_csv_up_to_date(returns_csv_file):
    if not os.path.exists(returns_csv_file):
        return False
    csv_data = pd.read_csv(returns_csv_file, index_col=0, parse_dates=True)
    if csv_data.empty:
        return False
    last_date = csv_data.index.max()
    if pd.isnull(last_date):
        return False
    return (datetime.now() - last_date) < timedelta(days=recency_threshold)

# Check if the CSV file exists and is up-to-date
if is_csv_up_to_date(returns_csv_file):
    # Load data from the CSV file
    returns_data = pd.read_csv(returns_csv_file, index_col=0, parse_dates=True)
else:
    # Create an empty DataFrame to store the percentage change data
    returns_data = pd.DataFrame()

    # Loop through each ticker to download data, calculate percentage change, and add to DataFrame
    for ticker in unique_tickers:
        print(f"Processing ticker: {ticker}")
        data = yf.download(
            tickers=ticker,
            period='max',
            auto_adjust=True
        )
        print(f"Data for {ticker}:")
        print(data.tail())  # Print the last few rows of the data to check if it's being downloaded correctly
        if len(data) >= min_days_threshold and not pd.isnull(data['Close'].iloc[-1].item()):
            returns_data[ticker] = data['Close'].pct_change()
        else:
            print(f"Skipping ticker {ticker} due to insufficient data or not being currently traded.")

    # Save the returns data to a CSV file
    returns_data.to_csv(returns_csv_file)

print("Final returns data:")
print(returns_data)