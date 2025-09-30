"""
Test sandbox OAuth with the correct E*TRADE endpoints
"""
from dotenv import load_dotenv
import os
from rauth import OAuth1Service

def test_sandbox_oauth():
    load_dotenv()
    
    key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
    secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
    
    print("🧪 Testing E*TRADE Sandbox OAuth Flow")
    print(f"   Using key ending: ...{key[-4:]}")
    
    # Test different authorize URL patterns
    oauth_configs = [
        ("Standard Sandbox", {
            "request_token_url": "https://apisb.etrade.com/oauth/request_token",
            "access_token_url": "https://apisb.etrade.com/oauth/access_token", 
            "authorize_url": "https://us.etrade.com/e/t/etws/authorize"
        }),
        ("Alternative 1", {
            "request_token_url": "https://apisb.etrade.com/oauth/request_token",
            "access_token_url": "https://apisb.etrade.com/oauth/access_token",
            "authorize_url": "https://etrade.com/logins/Authorize"
        }),
        ("Alternative 2", {
            "request_token_url": "https://apisb.etrade.com/oauth/request_token", 
            "access_token_url": "https://apisb.etrade.com/oauth/access_token",
            "authorize_url": "https://apisb.etrade.com/oauth/authorize"
        })
    ]
    
    for name, config in oauth_configs:
        print(f"\n🔍 Testing {name}:")
        print(f"   Authorize URL: {config['authorize_url']}")
        
        try:
            service = OAuth1Service(
                name="etrade",
                consumer_key=key,
                consumer_secret=secret,
                **config,
                base_url="https://apisb.etrade.com"
            )
            
            req_token, req_secret = service.get_request_token(
                params={"oauth_callback": "oob", "format": "json"}
            )
            
            auth_url = f"{config['authorize_url']}?oauth_token={req_token}"
            print(f"   ✅ Request token obtained: {req_token[:20]}...")
            print(f"   🌐 Test this URL: {auth_url}")
            
            # Don't actually open browser for testing - just show the URL
            return auth_url
            
        except Exception as e:
            print(f"   ❌ Failed: {e}")
    
    return None

if __name__ == "__main__":
    test_url = test_sandbox_oauth()
    if test_url:
        print(f"\n✅ Found working config! Test the URL manually in your browser.")
    else:
        print(f"\n❌ All OAuth configs failed. Contact E*TRADE support.")