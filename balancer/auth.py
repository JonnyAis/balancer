import os, webbrowser, time, json, datetime
from rauth import OAuth1Service

_BASE_LIVE = "https://api.etrade.com"
_BASE_SB = "https://apisb.etrade.com"
_TOKENS_PATH = os.path.join(os.path.dirname(__file__), "tokens.json")

def _load_creds():
    key = os.getenv("ETRADE_CONSUMER_KEY")
    sec = os.getenv("ETRADE_CONSUMER_SECRET")
    if key and sec:
        return key, sec
    # Try package credentials
    try:
        from . import credentials as pkg_creds
        return pkg_creds.etrade_key, pkg_creds.etrade_secret
    except Exception:
        pass
    # Try root-level credentials (after move this is redundant safeguard)
    try:
        import credentials as root_creds
        return root_creds.etrade_key, root_creds.etrade_secret
    except Exception:
        pass
    raise RuntimeError("Missing credentials: set env vars or create balancer/credentials.py")

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

def get_session(sandbox: bool, force=False):
    if force:
        return refresh_session(sandbox)
    key, sec = _load_creds()
    cached = _load_cached(sandbox)
    if cached:
        tok, tok_sec = cached
        svc = _service(sandbox, key, sec)
        try:
            session = svc.get_session((tok, tok_sec))
            r = session.get("v1/accounts/list.json")
            if r.status_code == 200:
                print(f"[Auth] Reused cached token (sandbox={sandbox})")
                return session
            print(f"[Auth] Cached token invalid status={r.status_code}; refreshing...")
        except Exception:
            print("[Auth] Cached token unusable; refreshing...")
    return start_session(sandbox)