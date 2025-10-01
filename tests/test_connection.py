# Test E*TRADE API connection with new credentials

import os
from dotenv import load_dotenv
from rauth import OAuth1Service


def test_etrade_connection():
    load_dotenv()

    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"

    if sandbox:
        key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
        secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
        base_url = "https://apisb.etrade.com"
        env_name = "SANDBOX"
    else:
        key = os.getenv("ETRADE_PROD_CONSUMER_KEY")
        secret = os.getenv("ETRADE_PROD_CONSUMER_SECRET")
        base_url = "https://api.etrade.com"
        env_name = "PRODUCTION"

    assert key and secret, "Missing credentials"

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
        authorize_url="https://us.etrade.com/e/t/etws/authorize",
        base_url=base_url,
    )

    try:
        print("🔑 Requesting OAuth token...")
        req_token, req_secret = service.get_request_token(
            params={"oauth_callback": "oob", "format": "json"}
        )
        assert req_token, "No request token received"
        print("✅ SUCCESS! E*TRADE accepted your credentials")
    except Exception as e:
        error_str = str(e)
        print(f"❌ Connection failed: {error_str}")
        assert False, f"Connection failed: {error_str}"


if __name__ == "__main__":
    test_etrade_connection()
