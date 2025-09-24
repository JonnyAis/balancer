import os, webbrowser, time, json, datetime
import sys
from rauth import OAuth1Service
from dotenv import load_dotenv

_BASE_LIVE = "https://api.etrade.com"
_BASE_SB = "https://apisb.etrade.com"
_TOKENS_PATH = os.path.join(os.path.dirname(__file__), "tokens.json")

def _load_creds():
    # Load .env file if it exists
    load_dotenv()
    
    key = os.getenv("ETRADE_CONSUMER_KEY")
    sec = os.getenv("ETRADE_CONSUMER_SECRET")
    
    if key and sec:
        return key, sec
    
    # Fallback to credentials.py for backward compatibility
    try:
        from . import credentials as pkg_creds
        return pkg_creds.etrade_key, pkg_creds.etrade_secret
    except Exception:
        pass
    
    raise RuntimeError("Missing credentials: set ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET in .env file or environment")

def _mask(v: str) -> str:
    if not v:
        return "<missing>"
    return "*" * (len(v) - 4) + v[-4:]

def _service(sandbox: bool, key: str, sec: str):
    host = _BASE_SB if sandbox else _BASE_LIVE
    return OAuth1Service(
        name="etrade",
        consumer_key=key,
        consumer_secret=sec,
        request_token_url=f"{host}/oauth/request_token",
        access_token_url=f"{host}/oauth/access_token",
        authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
        base_url=host,
    )

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

def start_session(sandbox: bool):
    key, sec = _load_creds()
    svc = _service(sandbox, key, sec)
    print(f"[Auth] Interactive flow env={'SANDBOX' if sandbox else 'LIVE'} key={_mask(key)}")
    req_token, req_secret = svc.get_request_token(params={"oauth_callback": "oob", "format": "json"})
    url = svc.authorize_url.format(svc.consumer_key, req_token)
    webbrowser.open(url)
    time.sleep(1)
    verifier = input("Enter E*TRADE verifier: ").strip()
    session = svc.get_auth_session(req_token, req_secret, params={"oauth_verifier": verifier})
    _save_cached(sandbox, session.access_token, session.access_token_secret)
    print("[Auth] Session established (cached).")
    return session

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
        key, sec = _load_creds()
        svc = _service(sandbox, key, sec)
        session = svc.get_session((access_token, access_secret))
        # E*TRADE renew endpoint (documented) -> /oauth/renew_access_token
        r = session.get(f"{svc.base_url}/oauth/renew_access_token", params={"oauth_token": access_token}, timeout=8)
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

def get_session(sandbox: bool, force=False, interactive=True, mode: str = "preview"):
    """
    Returns authenticated session or None.
    - Tries cached token.
    - On 401:
        * If interactive (or auto + TTY) -> full OAuth flow.
        * Else returns None.
    """
    key, sec = _load_creds()
    if force and interactive:
        return refresh_session(sandbox)

    cached = _load_cached(sandbox)
    if cached:
        tok, tok_sec = cached
        svc = _service(sandbox, key, sec)
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

        # Need new auth
        auto_tty_ok = (mode == "auto" and sys.stdin.isatty())
        if interactive or auto_tty_ok:
            print(f"[Auth] Starting interactive OAuth (mode={mode}, interactive={interactive}, auto_tty_ok={auto_tty_ok})")
            return start_session(sandbox)
        print("[Auth] Non-interactive cycle -> auth deferred.")
        return None

    # No cached token at all
    auto_tty_ok = (mode == "auto" and sys.stdin.isatty())
    if interactive or auto_tty_ok:
        print(f"[Auth] No cache -> interactive OAuth (mode={mode}, interactive={interactive}, auto_tty_ok={auto_tty_ok})")
        return start_session(sandbox)
    print("[Auth] No cached token and non-interactive; returning None.")
    return None