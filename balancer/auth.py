import os, webbrowser, time
from rauth import OAuth1Service

_BASE_LIVE = "https://api.etrade.com"
_BASE_SB = "https://apisb.etrade.com"

def _load_creds():
    key = os.getenv("ETRADE_CONSUMER_KEY")
    sec = os.getenv("ETRADE_CONSUMER_SECRET")
    if key and sec:
        return key, sec
    try:
        from config_file import etrade_key, etrade_secret
        return etrade_key, etrade_secret
    except Exception:
        raise RuntimeError("Missing credentials: set env vars or create config_file.py (see config_file_example.py)")

def _mask(v: str) -> str:
    if not v:
        return "<missing>"
    return "*" * (len(v) - 4) + v[-4:]  # mask all but last 4

def start_session(sandbox: bool):
    key, sec = _load_creds()
    print(f"[Auth] Credentials loaded (key={_mask(key)} secret={_mask(sec)})")
    base = _BASE_SB if sandbox else _BASE_LIVE
    svc = OAuth1Service(
        name="etrade",
        consumer_key=key,
        consumer_secret=sec,
        request_token_url=f"{_BASE_LIVE}/oauth/request_token",
        access_token_url=f"{_BASE_LIVE}/oauth/access_token",
        authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
        base_url=base,
    )
    print("[Auth] Requesting temporary token...")
    tok, tok_sec = svc.get_request_token(params={"oauth_callback": "oob", "format": "json"})
    url = svc.authorize_url.format(svc.consumer_key, tok)
    print("[Auth] Opening browser for authorization...")
    webbrowser.open(url)
    time.sleep(1)
    verifier = input("Enter E*TRADE verifier: ").strip()
    print("[Auth] Exchanging verifier...")
    session = svc.get_auth_session(tok, tok_sec, params={"oauth_verifier": verifier})
    print("[Auth] Session established.")
    return session