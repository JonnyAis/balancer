# Tests

This directory contains test scripts for debugging and validating the E*TRADE integration.

## Authentication Tests
- `test_creds.py` - Verify .env credentials are loaded correctly
- `test_connection.py` - Test E*TRADE API connection with OAuth flow
- `test_sandbox_oauth.py` - Test different OAuth URL patterns for sandbox

## Market Data Tests  
- `test_quotes.py` - Test quote retrieval and price extraction

## Running Tests

All tests should be run from the project root using the virtual environment:

```powershell
# From the balancer/ root directory:
.\.venv\Scripts\python.exe tests\test_quotes.py
.\.venv\Scripts\python.exe tests\test_connection.py
```

## Environment Variables

Some tests support debug flags:
- `REBALANCER_DEBUG_QUOTES=1` - Show raw quote responses
- `REBALANCER_DEBUG_CASH=1` - Show cash balance debugging

## Notes

- Most tests require a cached OAuth token (run the main program first)
- Sandbox tests may return fake/demo data instead of real market quotes
- Production tests are safer to run since they use real current market data