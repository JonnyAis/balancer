import os, webbrowser, time, json, datetime
import sys
from rauth import OAuth1Service
from dotenv import load_dotenv

_BASE_LIVE = "https://api.etrade.com"
_BASE_SB = "https://apisb.etrade.com"
_TOKENS_PATH = os.path.join(os.path.dirname(__file__), "tokens.json")

def _mask(v: str) -> str:
    if not v:
        return "<missing>"
    return "*" * (len(v) - 4) + v[-4:]

def _load_cached(sandbox: bool):
    if not os.path.exists(_TOKENS_PATH):
        return None
    try:
        with open(_TOKENS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = "sandbox" if sandbox else "live"
        entry = data.get(key)
        if not entry:
            return None
        return entry["oauth_token"], entry["oauth_token_secret"]
    except Exception:
        return None

def _save_cached(sandbox: bool, token: str, token_secret: str):
    data = {}
    if os.path.exists(_TOKENS_PATH):
        try:
            with open(_TOKENS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    key = "sandbox" if sandbox else "live"
    data[key] = {
        "oauth_token": token,
        "oauth_token_secret": token_secret,
        "ts": datetime.datetime.utcnow().isoformat()
    }
    with open(_TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def get_credentials():
    """
    Get E*TRADE credentials based on sandbox setting.
    Returns (consumer_key, consumer_secret, is_sandbox)
    """
    load_dotenv()
    
    # Determine if we're in sandbox mode
    sandbox = os.getenv("ETRADE_SANDBOX", "false").lower() == "true"
    
    if sandbox:
        # Use sandbox credentials
        consumer_key = os.getenv("ETRADE_SANDBOX_CONSUMER_KEY")
        consumer_secret = os.getenv("ETRADE_SANDBOX_CONSUMER_SECRET")
        env_name = "SANDBOX"
    else:
        # Use production credentials
        consumer_key = os.getenv("ETRADE_PROD_CONSUMER_KEY") 
        consumer_secret = os.getenv("ETRADE_PROD_CONSUMER_SECRET")
        env_name = "PRODUCTION"
    
    # Validate credentials exist
    if not consumer_key or not consumer_secret:
        missing_vars = []
        if sandbox:
            if not consumer_key: missing_vars.append("ETRADE_SANDBOX_CONSUMER_KEY")
            if not consumer_secret: missing_vars.append("ETRADE_SANDBOX_CONSUMER_SECRET")
        else:
            if not consumer_key: missing_vars.append("ETRADE_PROD_CONSUMER_KEY")
            if not consumer_secret: missing_vars.append("ETRADE_PROD_CONSUMER_SECRET")
        
        raise ValueError(f"Missing {env_name} credentials in .env file: {', '.join(missing_vars)}")
    
    print(f"[Auth] Using {env_name} credentials (key ending: ...{consumer_key[-4:]})")
    
    return consumer_key, consumer_secret, sandbox

def start_session(sandbox_override=None):
    """
    Start an E*TRADE OAuth session with correct sandbox OAuth endpoints.
    """
    consumer_key, consumer_secret, is_sandbox = get_credentials()
    
    if is_sandbox:
        request_token_url = "https://apisb.etrade.com/oauth/request_token"
        access_token_url = "https://apisb.etrade.com/oauth/access_token"
        base_url = "https://apisb.etrade.com"
    else:
        request_token_url = "https://api.etrade.com/oauth/request_token"
        access_token_url = "https://api.etrade.com/oauth/access_token"
        base_url = "https://api.etrade.com"
    
    # Use the correct authorization URL format from E*TRADE docs
    authorize_url_template = "https://us.etrade.com/e/t/etws/authorize?key={}&token={}"
    
    svc = OAuth1Service(
        name="etrade",
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        request_token_url=request_token_url,
        access_token_url=access_token_url,
        authorize_url="https://us.etrade.com/e/t/etws/authorize",  # OAuth1Service needs a base URL
        base_url=base_url
    )
    
    print(f"[Auth] Starting OAuth flow for {('SANDBOX' if is_sandbox else 'PRODUCTION')}")
    print(f"[Auth] API base URL: {base_url}")
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"[Auth] Attempt {attempt + 1}/{max_retries}: Getting request token...")
            req_token, req_secret = svc.get_request_token(params={"oauth_callback": "oob", "format": "json"})
            print(f"[Auth] Request token: {req_token}")
            
            # Build the correct authorization URL format per E*TRADE docs
            auth_url = authorize_url_template.format(consumer_key, req_token)
            print(f"[Auth] Please visit: {auth_url}")
            
            # Auto-open browser
            try:
                webbrowser.open(auth_url)
                print("[Auth] Opening browser automatically...")
            except Exception:
                print("[Auth] Could not auto-open browser, please copy URL manually")
            
            verifier = input(f"\n[Auth] Enter verification code (or 'retry' to get new token): ").strip()
            
            if verifier.lower() == 'retry':
                print(f"[Auth] Retrying with new request token...")
                continue
                
            if not verifier:
                raise ValueError("No verification code provided")
            
            print(f"[Auth] Exchanging verification code for access token...")
            session = svc.get_auth_session(req_token, req_secret, params={"oauth_verifier": verifier})
            print("[Auth] Session established successfully")
            
            # Cache the successful token
            if hasattr(session, 'access_token') and hasattr(session, 'access_token_secret'):
                _save_cached(is_sandbox, session.access_token, session.access_token_secret)
            
            return session
            
        except Exception as e:
            print(f"[Auth] OAuth attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                print(f"[Auth] Waiting 10 seconds before retry...")
                time.sleep(10)
            else:
                print(f"[Auth] All attempts failed")
                raise

def refresh_session(sandbox: bool):
    """Force a new interactive session (clears cached token)."""
    try:
        if os.path.exists(_TOKENS_PATH):
            os.remove(_TOKENS_PATH)
    except Exception:
        pass
    return start_session(sandbox)

def renew_access_token(sandbox: bool, access_token: str, access_secret: str):
    """
    Attempt silent renew (E*TRADE OAuth 1.0). Returns (new_token, new_secret) or None on failure.
    """
    try:
        # OLD: key, sec = _load_creds()
        # NEW: Use the new credential system
        consumer_key, consumer_secret, _ = get_credentials()
        
        # Build service with correct URLs
        if sandbox:
            base_url = "https://apisb.etrade.com"
        else:
            base_url = "https://api.etrade.com"
            
        svc = OAuth1Service(
            name="etrade",
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            request_token_url=f"{base_url}/oauth/request_token",
            access_token_url=f"{base_url}/oauth/access_token",
            authorize_url="https://us.etrade.com/e/t/etws/authorize",
            base_url=base_url
        )
        
        session = svc.get_session((access_token, access_secret))
        # E*TRADE renew endpoint (documented) -> /oauth/renew_access_token
        r = session.get(f"{base_url}/oauth/renew_access_token", params={"oauth_token": access_token}, timeout=8)
        if r.status_code == 200:
            # Response body: oauth_token=...&oauth_token_secret=...
            parts = dict(p.split("=",1) for p in r.text.split("&") if "=" in p)
            nt, ns = parts.get("oauth_token"), parts.get("oauth_token_secret")
            if nt and ns:
                _save_cached(sandbox, nt, ns)
                print("[Auth] Access token renewed silently.")
                return nt, ns
        else:
            print(f"[Auth] Renew attempt status={r.status_code}")
    except Exception as e:
        print(f"[Auth] Renew failed: {e}")
    return None

def get_session(sandbox: bool, force=False, interactive=True):
    """
    Returns authenticated session or None.
    - Tries cached token.
    - On 401:
        * If interactive (or auto + TTY) -> full OAuth flow.
        * Else returns None.
    """
    if force and interactive:
        return refresh_session(sandbox)

    cached = _load_cached(sandbox)
    if cached:
        tok, tok_sec = cached
        
        consumer_key, consumer_secret, _ = get_credentials()
        base_url = "https://apisb.etrade.com" if sandbox else "https://api.etrade.com"
        authorize_url = "https://us.etrade.com/e/t/etws/authorize"
        svc = OAuth1Service(
            name="etrade",
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            request_token_url=f"{base_url}/oauth/request_token",
            access_token_url=f"{base_url}/oauth/access_token",
            authorize_url=authorize_url,
            base_url=base_url
        )
        
        try:
            session = svc.get_session((tok, tok_sec))
            r = session.get("v1/accounts/list.json", timeout=8)
            if r.status_code == 200:
                print(f"[Auth] Reused cached token (sandbox={sandbox})")
                return session
            if r.status_code == 401:
                print("[Auth] Cached token unauthorized (401).")
            else:
                print(f"[Auth] Cached token test status={r.status_code}")
        except Exception as e:
            print(f"[Auth] Cached token error: {e}")

        # Only use interactive parameter now
        if interactive and sys.stdin.isatty():
            print(f"[Auth] Starting interactive OAuth (interactive={interactive})")
            return start_session(sandbox)
        print("[Auth] Non-interactive cycle -> auth deferred.")
        return None

    # No cached token at all
    if interactive and sys.stdin.isatty():
        print(f"[Auth] No cache -> interactive OAuth (interactive={interactive})")
        return start_session(sandbox)
    print("[Auth] No cached token and non-interactive; returning None.")
    return None