# filepath: balancer/portfolio.py
import pandas as pd

def extract_positions(portfolio_json: dict) -> pd.DataFrame:
    rows = []
    acct_list = portfolio_json.get("PortfolioResponse", {}).get("AccountPortfolio", [])
    for ap in acct_list:
        acct = ap.get("accountId")
        for pos in ap.get("Position", []) or []:
            product = pos.get("Product", {})
            rows.append({
                "accountId": acct,
                "positionId": pos.get("positionId"),
                "symbol": product.get("symbol"),
                "marketValue": pos.get("marketValue", 0.0),
                "quantity": pos.get("quantity", 0.0),
                "lotsDetails": pos.get("lotsDetails")
            })
    return pd.DataFrame(rows)