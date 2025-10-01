#!/usr/bin/env python3
"""
Test script to verify E*TRADE credentials
"""

import os

from dotenv import load_dotenv


def test_credentials():
    load_dotenv()

    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"

    if sandbox:
        key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
        secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
        env_name = "SANDBOX"
    else:
        key = os.getenv("ETRADE_PROD_CONSUMER_KEY")
        secret = os.getenv("ETRADE_PROD_CONSUMER_SECRET")
        env_name = "PRODUCTION"

    print("🔍 Current Credential Configuration:")
    print(f"   Environment: {env_name}")
    print(f"   Consumer Key: {key[:10] + '...' + key[-10:] if key else 'NOT SET'}")
    print(f"   Consumer Secret: {secret[:10] + '...' + secret[-10:] if secret else 'NOT SET'}")
    print(f"   Sandbox Flag: {os.getenv('ETRADE_SANDBOX', 'not set')}")

    # Check both sets of credentials
    print("\n📋 Available Credentials:")

    sandbox_key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
    sandbox_secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
    prod_key = os.getenv("ETRADE_PROD_CONSUMER_KEY")
    prod_secret = os.getenv("ETRADE_PROD_CONSUMER_SECRET")

    print(
        f"   Sandbox: {'✅' if sandbox_key and sandbox_secret else '❌'} "
        f"({'Key: ...' + sandbox_key[-4:] + ', Secret: ...' + sandbox_secret[-4:] if sandbox_key and sandbox_secret else 'Missing'})"
    )
    print(
        f"   Production: {'✅' if prod_key and prod_secret else '❌'} "
        f"({'Key: ...' + prod_key[-4:] + ', Secret: ...' + prod_secret[-4:] if prod_key and prod_secret else 'Missing'})"
    )

    assert key and secret, f"Missing {env_name} credentials"
    print(f"\n✅ {env_name} credentials are configured")


if __name__ == "__main__":
    test_credentials()
