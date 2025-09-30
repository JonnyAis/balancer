```python
// filepath: c:\Users\jonat\OneDrive\Documents\GitHub\balancer\test_connection.py
#!/usr/bin/env python3
"""
Test E*TRADE API connection with new credentials
"""
import os
from dotenv import load_dotenv
from rauth import OAuth1Service

def test_etrade_connection():
    load_dotenv()
    
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"
    
    if sandbox:
        key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
        secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
        base_url = "https://api.etgacct.com"
        env_name = "SANDBOX"
    else:
        key = os.getenv("ETRADE_PROD_CONSUMER_KEY")
        secret = os.getenv("ETRADE_PROD_CONSUMER_SECRET")
        base_url = "https://api.etrade.com"
        env_name = "PRODUCTION"
    
    if not key or not secret:
        print("❌ Missing credentials")
        return False
        
    print(f"🧪 Testing E*TRADE connection...")
    print(f"   Environment: {env_name}")
    print(f"   Base URL: {base_url}")
    print(f"   Key ending in: ...{key[-4:]}")
    
    # Set up OAuth service
    service = OAuth1Service(
        name="etrade",
        consumer_key=key,
        consumer_secret=secret,
        request_token_url=f"{base_url}/oauth/request_token",
        access_token_url=f"{base_url}/oauth/access_token", 
        authorize_url=f"{base_url}/oauth/authorize",
        base_url=base_url
    )
    
    try:
        print("🔑 Requesting OAuth token...")
        req_token, req_secret = service.get_request_token(
            params={"oauth_callback": "oob", "format": "json"}
        )
        print("✅ SUCCESS! E*TRADE accepted your credentials")
        print(f"   Request token received: {req_token[:10]}...")
        print(f"   You can now use these credentials with the balancer")
        return True
        
    except Exception as e:
        error_str = str(e)
        print(f"❌ Connection failed: {error_str}")
        
        # Parse the error for more details
        if "consumer_key_rejected" in error_str:
            print("\n🔍 Diagnosis: Consumer key rejected")
            print("   • The consumer key is not recognized by E*TRADE")
            print("   • Double-check the key is exactly as provided")
            print("   • Verify the credentials are activated in E*TRADE developer portal")
            
        elif "401" in error_str or "Unauthorized" in error_str:
            print("\n🔍 Diagnosis: Authentication failed (401 Unauthorized)")
            print("   • Check both consumer key AND secret are correct")
            print("   • Verify you're using the right environment credentials")
            print("   • The credentials might not be activated yet")
            
        elif "ssl" in error_str.lower() or "certificate" in error_str.lower():
            print("\n🔍 Diagnosis: SSL/Certificate issue")
            print("   • Network or firewall blocking the connection")
            
        else:
            print(f"\n🔍 Diagnosis: Unknown error")
            print(f"   • Full error: {error_str}")
            
        print(f"\n💡 Next steps:")
        print(f"   1. Double-check credentials in .env file")
        print(f"   2. Contact E*TRADE to verify credentials are activated")
        print(f"   3. Ask E*TRADE if sandbox uses https://api.etgacct.com")
        
        return False

if __name__ == "__main__":
    test_etrade_connection()
```