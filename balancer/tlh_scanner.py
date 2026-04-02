# filepath: balancer/tlh_scanner.py
"""
Tax-loss harvesting scanner.

Given current basket positions (with lot-level detail), prices, transaction
history, and swap clusters, identifies harvestable losses and proposes
swap trades.

Lot data format (from E*TRADE or manual):
    [
        {
            "symbol": "AAPL",
            "lot_id": "12345",
            "shares": 10,
            "cost_basis_per_share": 195.00,
            "acquisition_date": "2025-08-15",   # ISO format
        },
        ...
    ]

Transaction history format:
    [
        {
            "symbol": "AAPL",
            "action": "BUY",                    # BUY or SELL
            "date": "2026-03-10",               # ISO format
            "shares": 5,
        },
        ...
    ]
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Lot:
    symbol: str
    lot_id: str
    shares: int
    cost_basis_per_share: float
    acquisition_date: datetime

    @property
    def total_cost_basis(self) -> float:
        return self.shares * self.cost_basis_per_share


@dataclass
class HarvestCandidate:
    """A lot that can be sold at a loss for tax purposes."""
    lot: Lot
    current_price: float
    unrealized_loss: float          # Negative number (loss)
    loss_per_share: float
    holding_days: int
    is_long_term: bool              # > 365 days
    wash_sale_clear: bool           # No buy in prior 30 days
    replacement: Optional[str]      # Suggested replacement symbol
    replacement_correlation: float
    estimated_tax_savings: float


@dataclass
class TlhProposal:
    """A paired sell + buy trade for tax-loss harvesting."""
    sell_symbol: str
    sell_lot_id: str
    sell_shares: int
    sell_est_proceeds: float
    buy_symbol: str
    buy_shares: int
    buy_est_cost: float
    harvested_loss: float
    estimated_tax_savings: float
    replacement_correlation: float
    holding_period: str             # "short_term" or "long_term"


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------
def scan_for_tlh(
    lots: list[dict],
    prices: dict,
    transaction_history: list[dict],
    swap_clusters: dict,
    restricted_symbols: set = None,
    min_loss: float = 200.0,
    wash_sale_window_days: int = 30,
    min_holding_days: int = 2,
    min_correlation: float = 0.85,
    tax_rate_short: float = 0.37,
    tax_rate_long: float = 0.20,
    state_tax_rate: float = 0.0,
    today: datetime = None,
) -> list[TlhProposal]:
    """
    Scan lots for tax-loss harvesting opportunities.

    Steps:
      1. Parse lots, compute unrealized gains/losses at current prices.
      2. Filter to losses above minimum threshold.
      3. Check wash sale eligibility (no same-symbol buy in past 30 days).
      4. Find replacement stock from swap clusters.
      5. Build paired sell/buy proposals.

    Args:
        lots: List of lot dicts (see module docstring for format).
        prices: {symbol: current_price}
        transaction_history: List of transaction dicts.
        swap_clusters: Output of swap_clusters.build_swap_clusters().
        restricted_symbols: Symbols to exclude from sells AND buys.
        min_loss: Minimum dollar loss per lot to consider harvesting.
        wash_sale_window_days: IRS wash sale window (30 days).
        min_holding_days: Skip lots acquired very recently.
        min_correlation: Minimum correlation for replacement stock.
        tax_rate_short: Short-term capital gains tax rate.
        tax_rate_long: Long-term capital gains tax rate.
        state_tax_rate: State income tax rate (added to federal).
        today: Override current date for testing.

    Returns:
        List of TlhProposal, sorted by estimated tax savings descending.
    """
    if today is None:
        today = datetime.now()

    restricted = restricted_symbols or set()

    # -----------------------------------------------------------------------
    # 1. Parse lots
    # -----------------------------------------------------------------------
    parsed_lots = []
    for lot_dict in lots:
        sym = lot_dict["symbol"]
        if sym in restricted:
            continue

        acq_date = _parse_date(lot_dict["acquisition_date"])
        parsed_lots.append(Lot(
            symbol=sym,
            lot_id=str(lot_dict.get("lot_id", "")),
            shares=int(lot_dict["shares"]),
            cost_basis_per_share=float(lot_dict["cost_basis_per_share"]),
            acquisition_date=acq_date,
        ))

    # -----------------------------------------------------------------------
    # 2. Build wash sale history: {symbol: most_recent_buy_date}
    # -----------------------------------------------------------------------
    recent_buys = _recent_buys_by_symbol(transaction_history, today, wash_sale_window_days)

    # -----------------------------------------------------------------------
    # 3. Evaluate each lot
    # -----------------------------------------------------------------------
    candidates = []
    for lot in parsed_lots:
        price = prices.get(lot.symbol, 0)
        if price <= 0:
            continue

        # Unrealized P&L
        loss_per_share = price - lot.cost_basis_per_share
        unrealized = loss_per_share * lot.shares

        # Skip gains and tiny losses
        if unrealized >= 0:
            continue
        if abs(unrealized) < min_loss:
            continue

        # Holding period
        holding_days = (today - lot.acquisition_date).days
        if holding_days < min_holding_days:
            continue
        is_long_term = holding_days > 365

        # Wash sale check: was this symbol bought in the last 30 days?
        wash_sale_clear = lot.symbol not in recent_buys

        if not wash_sale_clear:
            continue  # Can't harvest — would be a wash sale

        # Find replacement
        from .swap_clusters import get_replacements
        replacements = get_replacements(
            lot.symbol,
            swap_clusters,
            restricted=restricted | {lot.symbol},
            prices=prices,
        )

        # Filter to minimum correlation
        replacements = [(sym, corr) for sym, corr in replacements if corr >= min_correlation]

        # Also exclude replacements that were recently sold (would trigger wash sale
        # on the replacement side — buying something you recently sold at a loss)
        recent_sells = _recent_sells_by_symbol(transaction_history, today, wash_sale_window_days)
        replacements = [(sym, corr) for sym, corr in replacements if sym not in recent_sells]

        if not replacements:
            continue  # No valid replacement available

        best_replacement, best_corr = replacements[0]

        # Tax savings estimate
        applicable_rate = (tax_rate_long if is_long_term else tax_rate_short) + state_tax_rate
        tax_savings = abs(unrealized) * applicable_rate

        candidates.append(HarvestCandidate(
            lot=lot,
            current_price=price,
            unrealized_loss=unrealized,
            loss_per_share=loss_per_share,
            holding_days=holding_days,
            is_long_term=is_long_term,
            wash_sale_clear=True,
            replacement=best_replacement,
            replacement_correlation=best_corr,
            estimated_tax_savings=tax_savings,
        ))

    # -----------------------------------------------------------------------
    # 4. Build proposals (sell lot, buy replacement with proceeds)
    # -----------------------------------------------------------------------
    proposals = []
    for c in candidates:
        sell_proceeds = c.lot.shares * c.current_price
        replacement_price = prices.get(c.replacement, 0)
        if replacement_price <= 0:
            continue

        buy_shares = int(sell_proceeds / replacement_price)
        if buy_shares <= 0:
            continue
        buy_cost = buy_shares * replacement_price

        proposals.append(TlhProposal(
            sell_symbol=c.lot.symbol,
            sell_lot_id=c.lot.lot_id,
            sell_shares=c.lot.shares,
            sell_est_proceeds=round(sell_proceeds, 2),
            buy_symbol=c.replacement,
            buy_shares=buy_shares,
            buy_est_cost=round(buy_cost, 2),
            harvested_loss=round(c.unrealized_loss, 2),
            estimated_tax_savings=round(c.estimated_tax_savings, 2),
            replacement_correlation=c.replacement_correlation,
            holding_period="long_term" if c.is_long_term else "short_term",
        ))

    # Sort by tax savings (largest first)
    proposals.sort(key=lambda p: p.estimated_tax_savings, reverse=True)

    return proposals


# ---------------------------------------------------------------------------
# Aggregation: merge proposals by symbol
# ---------------------------------------------------------------------------
def aggregate_by_symbol(proposals: list[TlhProposal]) -> dict:
    """
    Group proposals by sell_symbol for a summary view.

    Returns:
        {sell_symbol: {
            "total_loss": float,
            "total_tax_savings": float,
            "lots": int,
            "total_shares": int,
            "replacement": str,
            "proposals": [TlhProposal, ...],
        }}
    """
    by_symbol = {}
    for p in proposals:
        entry = by_symbol.setdefault(p.sell_symbol, {
            "total_loss": 0.0,
            "total_tax_savings": 0.0,
            "lots": 0,
            "total_shares": 0,
            "replacement": p.buy_symbol,
            "proposals": [],
        })
        entry["total_loss"] += p.harvested_loss
        entry["total_tax_savings"] += p.estimated_tax_savings
        entry["lots"] += 1
        entry["total_shares"] += p.sell_shares
        entry["proposals"].append(p)

    return by_symbol


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------
def format_tlh_report(proposals: list[TlhProposal], prices: dict) -> str:
    """Format TLH scan results as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("TAX-LOSS HARVESTING SCAN".center(70))
    lines.append("=" * 70)

    if not proposals:
        lines.append("No harvestable losses found.")
        lines.append("=" * 70)
        return "\n".join(lines)

    total_loss = sum(p.harvested_loss for p in proposals)
    total_savings = sum(p.estimated_tax_savings for p in proposals)
    total_lots = len(proposals)

    lines.append(f"Found {total_lots} harvestable lot(s)")
    lines.append(f"Total harvestable loss: ${abs(total_loss):,.0f}")
    lines.append(f"Estimated tax savings:  ${total_savings:,.0f}")
    lines.append("")

    # Detailed table
    lines.append(
        f"{'Sell':<7} {'Lot':<10} {'Shares':>6} {'Loss':>10} "
        f"{'TaxSave':>9} {'Buy':<7} {'BuySh':>6} {'Corr':>6} {'Term':>6}"
    )
    lines.append("-" * 72)

    for p in proposals:
        term_str = "LT" if p.holding_period == "long_term" else "ST"
        lines.append(
            f"{p.sell_symbol:<7} {p.sell_lot_id:<10} {p.sell_shares:>6} "
            f"${abs(p.harvested_loss):>9,.0f} ${p.estimated_tax_savings:>8,.0f} "
            f"{p.buy_symbol:<7} {p.buy_shares:>6} {p.replacement_correlation:>5.2f} "
            f"{term_str:>6}"
        )

    lines.append("-" * 72)
    lines.append(
        f"{'TOTAL':<7} {'':10} {'':>6} ${abs(total_loss):>9,.0f} "
        f"${total_savings:>8,.0f}"
    )

    # Summary by symbol
    agg = aggregate_by_symbol(proposals)
    if len(agg) > 1:
        lines.append("")
        lines.append("By symbol:")
        for sym, info in sorted(agg.items(), key=lambda x: x[1]["total_tax_savings"], reverse=True):
            lines.append(
                f"  {sym} -> {info['replacement']}: "
                f"{info['lots']} lot(s), {info['total_shares']} shares, "
                f"${abs(info['total_loss']):,.0f} loss, "
                f"${info['total_tax_savings']:,.0f} tax savings"
            )

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_date(date_val) -> datetime:
    """Parse a date from string or datetime."""
    if isinstance(date_val, datetime):
        return date_val
    if isinstance(date_val, str):
        # Try ISO format first, then common alternatives
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(date_val, fmt)
            except ValueError:
                continue
    raise ValueError(f"Cannot parse date: {date_val}")


def _recent_buys_by_symbol(
    transactions: list[dict],
    today: datetime,
    window_days: int,
) -> set:
    """Return set of symbols that had a BUY within the wash sale window."""
    cutoff = today - timedelta(days=window_days)
    recent = set()
    for tx in transactions:
        if tx.get("action", "").upper() != "BUY":
            continue
        tx_date = _parse_date(tx["date"])
        if tx_date >= cutoff:
            recent.add(tx["symbol"])
    return recent


def _recent_sells_by_symbol(
    transactions: list[dict],
    today: datetime,
    window_days: int,
) -> set:
    """Return set of symbols that had a SELL within the wash sale window."""
    cutoff = today - timedelta(days=window_days)
    recent = set()
    for tx in transactions:
        if tx.get("action", "").upper() != "SELL":
            continue
        tx_date = _parse_date(tx["date"])
        if tx_date >= cutoff:
            recent.add(tx["symbol"])
    return recent
