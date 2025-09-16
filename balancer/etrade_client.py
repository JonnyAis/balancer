import os, webbrowser, time
from rauth import OAuth1Service
import pandas as pd

_BASE_LIVE = "https://api.etrade.com"
_BASE_SB = "https://apisb.etrade.com"

def start_session(sandbox: bool):
    key = os.getenv("ETRADE_CONSUMER_KEY")
    sec = os.getenv("ETRADE_CONSUMER_SECRET")
    if not key or not sec:
        raise RuntimeError("Missing ETRADE_CONSUMER_KEY / ETRADE_CONSUMER_SECRET env vars")
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
    return svc.get_auth_session(tok, tok_sec, params={"oauth_verifier": verifier})

class ETradeClient:
    """
    Minimal E*TRADE API wrapper used by the rebalancer.

    Methods:
      - accounts()
      - portfolio_raw(account_key)
      - quotes(symbols)
    """
    def __init__(self, session):
        self.session = session
        self.base_url = session.service.base_url.rstrip("/")

    def accounts(self) -> pd.DataFrame:
        resp = self.session.get("v1/accounts/list.json")
        j = resp.json()
        accounts = (
            j.get("AccountListResponse", {})
             .get("Accounts", {})
             .get("Account", [])
        )
        if not accounts:
            return pd.DataFrame(columns=["accountIdKey"]).set_index(
                pd.Index([], name="accountId")
            )
        df = pd.DataFrame(accounts)
        if "accountId" not in df.columns:
            return pd.DataFrame(columns=["accountIdKey"]).set_index(
                pd.Index([], name="accountId")
            )
        return df.set_index("accountId")

    def portfolio_raw(self, account_key: str) -> dict:
        url = f"{self.base_url}/v1/accounts/{account_key}/portfolio.json"
        resp = self.session.get(url, params={"totalsRequired": True})
        return resp.json()

    def quotes(self, symbols) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=["last"])
        sym_str = ",".join(symbols)
        resp = self.session.get(
            f"https://api.etrade.com/v1/market/quote/{sym_str}.json",
            params={"detailFlag": "FUNDAMENTAL"}
        )
        j = resp.json()
        qdata = j.get("QuoteResponse", {}).get("QuoteData", [])
        rows = []
        for q in qdata:
            try:
                rows.append({
                    "symbol": q["Product"]["symbol"],
                    "last": q["Fundamental"]["lastTrade"]
                })
            except KeyError:
                continue
        if not rows:
            return pd.DataFrame(columns=["last"])
        return pd.DataFrame(rows).set_index("symbol")