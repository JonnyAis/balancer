"""
Test quote retrieval to debug pricing issues
"""
import os
from dotenv import load_dotenv
from balancer.auth import get_session
from balancer.etrade_client import ETradeClient

def test_quotes():
    load_dotenv()
    
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"
    print(f"🧪 Testing quote retrieval (sandbox={sandbox})")
    
    # Get cached session to avoid OAuth flow
    session = get_session(sandbox, interactive=False)
    if not session:
        print("❌ No cached session available. Run the main program first to authenticate.")
        return
    
    client = ETradeClient(session, sandbox=sandbox)
    
    # Test with your configured symbols
    symbols = ["VOO", "VBR", "IXUS", "BND"]
    print(f"📋 Testing symbols: {symbols}")
    
    try:
        quotes = client.quotes(symbols)
        print(f"\n✅ Raw quotes response:")
        for sym, data in quotes.items():
            print(f"   {sym}: {data}")
        
        # Test the price extraction logic
        prices = {}
        for sym in symbols:
            quote_data = quotes.get(sym, {})
            price = quote_data.get("lastPrice") or quote_data.get("last") or quote_data.get("close") or 0
            prices[sym] = float(price) if price > 0 else 0
            
        print(f"\n💰 Extracted prices:")
        for sym, price in prices.items():
            status = "✅" if price > 0 else "❌"
            print(f"   {status} {sym}: ${price}")
            
    except Exception as e:
        print(f"❌ Error testing quotes: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_quotes()