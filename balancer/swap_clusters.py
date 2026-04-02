# filepath: balancer/swap_clusters.py
"""
Swap cluster computation for tax-loss harvesting.

Groups stocks into clusters of highly correlated same-sector peers so that
when you sell Stock A at a loss, you can immediately buy Stock B (same sector,
high correlation, different security) without triggering a wash sale and without
meaningfully changing your portfolio's risk profile.

Usage:
    clusters = build_swap_clusters(correlation_matrix, sector_map, ...)
    replacements = get_replacements("AAPL", clusters, restricted={"AAPL"})
    # -> [("MSFT", 0.92), ("QCOM", 0.88), ...]
"""


import numpy as np
import pandas as pd


def build_swap_clusters(
    correlation: pd.DataFrame,
    sector_map: dict,
    min_correlation: float = 0.85,
    cluster_size: int = 4,
    candidate_symbols: list = None,
) -> dict:
    """
    For each stock, find the best same-sector replacements ranked by correlation.

    Args:
        correlation: Full correlation matrix (from returns.corr()).
        sector_map: {symbol: sector_name}
        min_correlation: Minimum pairwise correlation to qualify as a swap.
        cluster_size: Max number of replacements to keep per stock.
        candidate_symbols: If provided, only build clusters for these symbols.

    Returns:
        {symbol: [(replacement_symbol, correlation), ...]}
        Sorted by correlation descending, capped at cluster_size.
    """
    if candidate_symbols is None:
        candidate_symbols = [s for s in correlation.index if s in sector_map]

    clusters = {}

    for sym in candidate_symbols:
        if sym not in correlation.index:
            continue

        sym_sector = sector_map.get(sym)
        if not sym_sector:
            continue

        # Find same-sector peers with sufficient correlation
        candidates = []
        for peer in candidate_symbols:
            if peer == sym:
                continue
            if peer not in correlation.index:
                continue
            if sector_map.get(peer) != sym_sector:
                continue

            corr = correlation.loc[sym, peer]
            if np.isnan(corr):
                continue
            if corr >= min_correlation:
                candidates.append((peer, round(float(corr), 4)))

        # Sort by correlation descending, keep top N
        candidates.sort(key=lambda x: x[1], reverse=True)
        clusters[sym] = candidates[:cluster_size]

    return clusters


def get_replacements(
    symbol: str,
    clusters: dict,
    restricted: set = None,
    prices: dict = None,
    max_price_ratio: float = 5.0,
) -> list:
    """
    Get ranked replacement candidates for a symbol, filtering out restricted ones.

    Args:
        symbol: The stock being sold for TLH.
        clusters: Output of build_swap_clusters.
        restricted: Set of symbols that cannot be bought (wash sale risk).
        prices: {symbol: price} — if provided, filter out replacements where the
                price ratio is extreme (avoids swapping a $500 stock for a $20 one).
        max_price_ratio: Maximum price ratio between original and replacement.

    Returns:
        [(replacement_symbol, correlation), ...] — sorted by correlation descending.
    """
    restricted = restricted or set()
    candidates = clusters.get(symbol, [])

    result = []
    for peer, corr in candidates:
        if peer in restricted:
            continue

        # Optional price-ratio filter
        if prices and symbol in prices and peer in prices:
            px_orig = prices[symbol]
            px_peer = prices[peer]
            if px_orig > 0 and px_peer > 0:
                ratio = max(px_orig, px_peer) / min(px_orig, px_peer)
                if ratio > max_price_ratio:
                    continue

        result.append((peer, corr))

    return result


def format_cluster_report(clusters: dict, sector_map: dict) -> str:
    """Human-readable summary of swap clusters."""
    lines = []
    lines.append("=" * 65)
    lines.append("SWAP CLUSTER REPORT".center(65))
    lines.append("=" * 65)

    # Group by sector
    by_sector = {}
    for sym, peers in clusters.items():
        sector = sector_map.get(sym, "Unknown")
        by_sector.setdefault(sector, []).append((sym, peers))

    for sector in sorted(by_sector.keys()):
        lines.append(f"\n--- {sector} ---")
        for sym, peers in sorted(by_sector[sector], key=lambda x: x[0]):
            if not peers:
                lines.append(f"  {sym}: (no qualifying swaps)")
            else:
                peer_strs = [f"{p}({c:.2f})" for p, c in peers]
                lines.append(f"  {sym} -> {', '.join(peer_strs)}")

    # Stats
    total = len(clusters)
    with_swaps = sum(1 for v in clusters.values() if v)
    without = total - with_swaps
    avg_peers = (
        sum(len(v) for v in clusters.values()) / with_swaps if with_swaps else 0
    )

    lines.append(f"\nSummary: {total} stocks, {with_swaps} with swaps, "
                 f"{without} without, avg {avg_peers:.1f} replacements each")
    lines.append("=" * 65)
    return "\n".join(lines)
