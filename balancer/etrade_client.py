import pandas as pd
import requests
import uuid
import json
import os

class ETradeClient:
    """
    Minimal E*TRADE API wrapper used by the rebalancer.
    Uses accountIdKey for account-specific endpoints (portfolio, balance, orders).
    """
    def __init__(self, session, sandbox: bool = False):
        self.session = session
        self.sandbox = sandbox
        self.base_url = "https://apisb.etrade.com" if sandbox else "https://api.etrade.com"

    def accounts(self) -> pd.DataFrame:
        resp = self.session.get(f"{self.base_url}/v1/accounts/list.json", params={"format": "json"})
        try:
            j = resp.json()
        except Exception:
            return pd.DataFrame()
        accounts = (
            j.get("AccountListResponse", {})
             .get("Accounts", {})
             .get("Account", [])
        )
        if not accounts:
            return pd.DataFrame()
        df = pd.DataFrame(accounts)
        # Preserve both ids; do NOT drop accountIdKey.
        # (We do NOT set index here to avoid losing columns in debug output.)
        return df

    def portfolio_raw(self, account_id_key: str) -> dict:
        url = f"{self.base_url}/v1/accounts/{account_id_key}/portfolio.json"
        resp = self.session.get(url, params={"totalsRequired": True, "format": "json"})
        try:
            return resp.json()
        except Exception:
            return {}

    def quotes(self, symbols) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame(columns=["last"])
        sym_str = ",".join(symbols)
        resp = self.session.get(
            f"{self.base_url}/v1/market/quote/{sym_str}.json",
            params={"detailFlag": "FUNDAMENTAL", "format": "json"}
        )
        try:
            j = resp.json()
        except Exception:
            return pd.DataFrame(columns=["last"])
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

    def balance(self, account_id_key: str, account_id_numeric: str = None) -> dict:
        """
        Simplified cash retrieval.
        Preference:
          1. Balance endpoint (numeric accountId first if provided, else accountIdKey)
          2. Portfolio Totals cash fields
          3. Derived (totalAccountValue - sum(position marketValue)) only if prior cash tiny
        Debug output only if REBALANCER_DEBUG_CASH=1.
        Returns: {cash, cash_source}
        """
        debug = os.getenv("REBALANCER_DEBUG_CASH") == "1"

        def _try_balance(fragment):
            url = f"{self.base_url}/v1/accounts/{fragment}/balance.json"
            r = self.session.get(url, params={"format": "json"})
            try:
                j = r.json()
            except Exception:
                return 0.0
            bal = j.get("AccountBalanceResponse", {}) or j.get("BalanceResponse", {}) or {}
            paths = [
                ("cashAvailableForInvestment",),
                ("cashAvailableForWithdrawal",),
                ("cashBalance",),
                ("cash",),
                ("netCash",),
                ("Computed","cashAvailableForInvestment"),
                ("Computed","cashBalance")
            ]
            for path in paths:
                cur = bal
                ok = True
                for part in path:
                    if isinstance(cur, dict) and part in cur:
                        cur = cur[part]
                    else:
                        ok = False
                        break
                if ok and isinstance(cur, (int,float)):
                    return float(cur)
            return 0.0

        # 1. Balance endpoint (numeric first)
        cash = 0.0
        source = ""
        if account_id_numeric:
            cash = _try_balance(account_id_numeric)
            if cash > 0:
                source = "balance_accountId"
        if cash == 0.0:
            c2 = _try_balance(account_id_key)
            if c2 > 0:
                cash = c2
                source = "balance_accountIdKey"

        # 2. Portfolio totals / derived if still zero
        if cash == 0.0:
            port = self.portfolio_raw(account_id_key)
            pr = port.get("PortfolioResponse", {}) or {}
            totals = pr.get("Totals", {}) or {}
            totals_cash = None
            for k in ("cashBalance","cash","netCash","totalCash"):
                v = totals.get(k)
                if isinstance(v,(int,float)):
                    totals_cash = float(v)
                    break
            acct_list = pr.get("AccountPortfolio", []) or []
            pos_val = 0.0
            for ap in acct_list:
                for p in (ap.get("Position") or []):
                    try:
                        pos_val += float(p.get("marketValue", 0.0) or 0.0)
                    except Exception:
                        pass
            total_guess = (
                totals.get("totalAccountValue")
                or totals.get("netMarketValue")
                or (acct_list and acct_list[0].get("accountValueTotal"))
                or (acct_list and acct_list[0].get("totalMarketValue"))
            )
            derived = None
            if isinstance(total_guess,(int,float)) and total_guess > 0:
                d = float(total_guess) - pos_val
                if d >= 0:
                    derived = d

            if totals_cash is not None:
                cash = totals_cash
                source = "portfolio_totals"
            if (cash < 1 or cash == 0) and derived and derived > 5:
                cash = derived
                source = "derived_positions"

        if debug and os.getenv("REBALANCER_DEBUG_CASH_VERBOSE") == "1":
            print(f"[Cash] acct={account_id_key} cash={cash} source={source}")

        return {"cash": round(cash, 2), "cash_source": source}

    def _order_post(self, url: str, payload: dict):
        """
        Use data=json_string + header_auth=True to match legacy working pattern.
        Rauth's session signs correctly when header_auth=True.
        """
        body = json.dumps(payload)
        return self.session.post(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            header_auth=True  # important for POST signing
        )

    def preview_equity_order(self, account_id_key: str, symbol: str, action: str, quantity: int):
        url = f"{self.base_url}/v1/accounts/{account_id_key}/orders/preview.json"
        client_order_id = str(uuid.uuid4())[:18]
        payload = {
            "PreviewOrderRequest": {
                "orderType": "EQ",
                "clientOrderId": client_order_id,
                "Order": [{
                    "allOrNone": "false",
                    "priceType": "MARKET",
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "stopPrice": "",
                    "limitPrice": "",
                    "Instrument": [{
                        "Product": {"securityType": "EQ", "symbol": symbol},
                        "orderAction": action,
                        "quantityType": "QUANTITY",
                        "quantity": quantity
                    }]
                }]
            }
        }
        resp = self._order_post(url, payload)
        status = resp.status_code
        try:
            data = resp.json()
        except Exception:
            return {"ok": False, "status": status, "raw": resp.text}
        if status == 401:
            return {"ok": False, "status": status, "raw": data, "endpoint": url}
        pr = data.get("PreviewOrderResponse", {})
        pids = pr.get("PreviewIds") or []
        preview_id = pids[0].get("previewId") if (isinstance(pids, list) and pids) else None
        return {
            "ok": status == 200 and bool(preview_id),
            "status": status,
            "preview_id": preview_id,
            "client_order_id": client_order_id,
            "raw": pr or data,
            "endpoint": url
        }

    def place_equity_order(self, account_id_key: str, symbol: str, action: str,
                           quantity: int, preview_id: str, client_order_id: str):
        url = f"{self.base_url}/v1/accounts/{account_id_key}/orders/place.json"
        payload = {
            "PlaceOrderRequest": {
                "orderType": "EQ",
                "clientOrderId": client_order_id,
                "PreviewIds": [{"previewId": preview_id}],
                "Order": [{
                    "allOrNone": "false",
                    "priceType": "MARKET",
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "stopPrice": "",
                    "limitPrice": "",
                    "Instrument": [{
                        "Product": {"securityType": "EQ", "symbol": symbol},
                        "orderAction": action,
                        "quantityType": "QUANTITY",
                        "quantity": quantity
                    }]
                }]
            }
        }
        resp = self._order_post(url, payload)
        status = resp.status_code
        try:
            data = resp.json()
        except Exception:
            return {"ok": False, "status": status, "raw": resp.text}
        if status == 401:
            return {"ok": False, "status": status, "raw": data, "endpoint": url}
        r = data.get("PlaceOrderResponse", data)
        return {
            "ok": status == 200,
            "status": status,
            "raw": r,
            "endpoint": url
        }