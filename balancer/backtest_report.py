# filepath: balancer/backtest_report.py
"""
HTML report generator for backtest results.

Creates a self-contained HTML file with embedded Chart.js charts showing:
  - Portfolio value vs benchmark over time
  - Cumulative harvested losses and tax savings
  - Realized tracking error over time
  - Trade activity
  - Summary statistics
"""

import json
import os
from datetime import datetime


def generate_html_report(
    results: dict,
    output_path: str = None,
    title: str = "Balancer Backtest Report",
) -> str:
    """
    Generate a self-contained HTML report from backtest results.

    Args:
        results: Output of backtest.run_backtest().
        output_path: Where to write the HTML file. Defaults to ~/.balancer/
        title: Report title.

    Returns:
        Path to the generated HTML file.
    """
    if output_path is None:
        report_dir = os.path.join(os.path.expanduser("~"), ".balancer", "reports")
        os.makedirs(report_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(report_dir, f"backtest_{timestamp}.html")

    snapshots = results["snapshots"]
    summary = results["summary"]
    trade_log = results["trade_log"]

    # Prepare chart data
    dates = [s.date.strftime("%Y-%m-%d") for s in snapshots]
    portfolio_values = [s.portfolio_value for s in snapshots]
    benchmark_values = [s.benchmark_value for s in snapshots]
    tracking_errors = [s.tracking_error_bps for s in snapshots]
    cum_harvested = [abs(s.cumulative_harvested_loss) for s in snapshots]
    cum_tax_savings = [s.cumulative_tax_savings for s in snapshots]
    step_trades = [s.step_trades for s in snapshots]
    num_positions = [s.num_positions for s in snapshots]

    # Subsample for chart performance (max ~500 points)
    max_points = 500
    if len(dates) > max_points:
        step = len(dates) // max_points
        indices = list(range(0, len(dates), step))
        if indices[-1] != len(dates) - 1:
            indices.append(len(dates) - 1)
    else:
        indices = list(range(len(dates)))

    chart_dates = [dates[i] for i in indices]
    chart_pv = [portfolio_values[i] for i in indices]
    chart_bm = [benchmark_values[i] for i in indices]
    chart_te = [tracking_errors[i] for i in indices]
    chart_harvest = [cum_harvested[i] for i in indices]
    chart_savings = [cum_tax_savings[i] for i in indices]
    chart_positions = [num_positions[i] for i in indices]

    # Tax-adjusted portfolio values
    chart_tax_adj = [portfolio_values[i] + cum_tax_savings[i] for i in indices]

    # Weekly trade counts (aggregate)
    weekly_trades = _aggregate_weekly(dates, step_trades)

    # Trade log table (most recent 50)
    recent_trades = trade_log[-50:] if len(trade_log) > 50 else trade_log

    html = _build_html(
        title=title,
        summary=summary,
        chart_dates=json.dumps(chart_dates),
        chart_pv=json.dumps(chart_pv),
        chart_bm=json.dumps(chart_bm),
        chart_tax_adj=json.dumps(chart_tax_adj),
        chart_te=json.dumps(chart_te),
        chart_harvest=json.dumps(chart_harvest),
        chart_savings=json.dumps(chart_savings),
        chart_positions=json.dumps(chart_positions),
        weekly_dates=json.dumps(weekly_trades["dates"]),
        weekly_counts=json.dumps(weekly_trades["counts"]),
        trade_log=recent_trades,
        total_trades=len(trade_log),
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[Report] HTML report written to: {output_path}")
    return output_path


def _aggregate_weekly(dates, values):
    """Aggregate daily values into weekly sums."""
    if not dates:
        return {"dates": [], "counts": []}

    weekly_dates = []
    weekly_counts = []
    current_week_total = 0
    current_week_start = dates[0]

    for i, date_str in enumerate(dates):
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        week_start = datetime.strptime(current_week_start, "%Y-%m-%d")

        if (dt - week_start).days >= 7:
            weekly_dates.append(current_week_start)
            weekly_counts.append(current_week_total)
            current_week_start = date_str
            current_week_total = values[i]
        else:
            current_week_total += values[i]

    # Last partial week
    weekly_dates.append(current_week_start)
    weekly_counts.append(current_week_total)

    return {"dates": weekly_dates, "counts": weekly_counts}


def _build_html(
    title, summary, chart_dates, chart_pv, chart_bm, chart_tax_adj,
    chart_te, chart_harvest, chart_savings, chart_positions,
    weekly_dates, weekly_counts, trade_log, total_trades,
) -> str:
    """Build the full HTML string."""

    # Format summary values
    s = summary

    trade_rows = ""
    for t in reversed(trade_log):
        loss_str = f"${abs(t.get('harvested_loss', 0)):,.0f}"
        save_str = f"${t.get('tax_savings', 0):,.0f}"
        corr_str = f"{t.get('correlation', 0):.2f}"
        trade_rows += f"""
            <tr>
                <td>{t['date']}</td>
                <td class="sell">{t['sell_symbol']}</td>
                <td>{t['sell_shares']}</td>
                <td>${t['sell_price']:,.2f}</td>
                <td class="buy">{t['buy_symbol']}</td>
                <td>{t.get('buy_shares', 0)}</td>
                <td>${t.get('buy_price', 0):,.2f}</td>
                <td>{loss_str}</td>
                <td>{save_str}</td>
                <td>{corr_str}</td>
            </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f1117;
        color: #e1e4e8;
        padding: 24px;
        line-height: 1.5;
    }}
    h1 {{
        font-size: 28px;
        font-weight: 600;
        margin-bottom: 8px;
        color: #f0f3f6;
    }}
    .subtitle {{
        color: #8b949e;
        font-size: 14px;
        margin-bottom: 32px;
    }}
    .summary-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-bottom: 32px;
    }}
    .metric-card {{
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px;
    }}
    .metric-label {{
        font-size: 12px;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    .metric-value {{
        font-size: 24px;
        font-weight: 600;
        margin-top: 4px;
    }}
    .metric-value.positive {{ color: #3fb950; }}
    .metric-value.negative {{ color: #f85149; }}
    .metric-value.neutral {{ color: #58a6ff; }}
    .chart-container {{
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 24px;
    }}
    .chart-title {{
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 12px;
        color: #f0f3f6;
    }}
    .chart-wrapper {{
        position: relative;
        height: 300px;
    }}
    .charts-row {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 24px;
        margin-bottom: 24px;
    }}
    @media (max-width: 900px) {{
        .charts-row {{ grid-template-columns: 1fr; }}
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
    }}
    th {{
        text-align: left;
        padding: 10px 12px;
        background: #1c2128;
        border-bottom: 2px solid #30363d;
        color: #8b949e;
        font-weight: 600;
        text-transform: uppercase;
        font-size: 11px;
        letter-spacing: 0.5px;
    }}
    td {{
        padding: 8px 12px;
        border-bottom: 1px solid #21262d;
    }}
    tr:hover {{ background: #1c2128; }}
    .sell {{ color: #f85149; }}
    .buy {{ color: #3fb950; }}
    .section-title {{
        font-size: 20px;
        font-weight: 600;
        margin: 32px 0 16px;
        color: #f0f3f6;
    }}
    .footer {{
        text-align: center;
        color: #484f58;
        font-size: 12px;
        margin-top: 48px;
        padding-top: 24px;
        border-top: 1px solid #21262d;
    }}
</style>
</head>
<body>

<h1>{title}</h1>
<div class="subtitle">
    {s['start_date']} to {s['end_date']}  |
    {s['trading_days']} trading days  |
    Initial value: ${s['initial_value']:,.0f}
</div>

<!-- Summary Metrics -->
<div class="summary-grid">
    <div class="metric-card">
        <div class="metric-label">Portfolio Return</div>
        <div class="metric-value {'positive' if s['portfolio_return_pct'] >= 0 else 'negative'}">
            {s['portfolio_return_pct']:+.2f}%
        </div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Benchmark Return</div>
        <div class="metric-value {'positive' if s['benchmark_return_pct'] >= 0 else 'negative'}">
            {s['benchmark_return_pct']:+.2f}%
        </div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Tax-Adjusted Return</div>
        <div class="metric-value {'positive' if s['tax_adjusted_return_pct'] >= 0 else 'negative'}">
            {s['tax_adjusted_return_pct']:+.2f}%
        </div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Tax Alpha</div>
        <div class="metric-value {'positive' if s['tax_alpha_pct'] >= 0 else 'negative'}">
            {s['tax_alpha_pct']:+.2f}%
        </div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Harvested Losses</div>
        <div class="metric-value neutral">${s['total_harvested_loss']:,.0f}</div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Est. Tax Savings</div>
        <div class="metric-value positive">${s['total_tax_savings']:,.0f}</div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Avg Tracking Error</div>
        <div class="metric-value neutral">{s['avg_tracking_error_bps']:.0f} bps</div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Total Trades</div>
        <div class="metric-value neutral">{s['total_trades']}</div>
    </div>
    <div class="metric-card">
        <div class="metric-label">Turnover</div>
        <div class="metric-value neutral">{s['turnover_pct']:.0f}%</div>
    </div>
</div>

<!-- Chart: Portfolio vs Benchmark -->
<div class="chart-container">
    <div class="chart-title">Portfolio Value vs Benchmark</div>
    <div class="chart-wrapper">
        <canvas id="valueChart"></canvas>
    </div>
</div>

<!-- Side-by-side charts -->
<div class="charts-row">
    <div class="chart-container">
        <div class="chart-title">Realized Tracking Error (bps, rolling 20d)</div>
        <div class="chart-wrapper">
            <canvas id="teChart"></canvas>
        </div>
    </div>
    <div class="chart-container">
        <div class="chart-title">Cumulative Tax-Loss Harvesting</div>
        <div class="chart-wrapper">
            <canvas id="tlhChart"></canvas>
        </div>
    </div>
</div>

<div class="charts-row">
    <div class="chart-container">
        <div class="chart-title">Weekly Trade Count</div>
        <div class="chart-wrapper">
            <canvas id="tradeChart"></canvas>
        </div>
    </div>
    <div class="chart-container">
        <div class="chart-title">Number of Positions</div>
        <div class="chart-wrapper">
            <canvas id="posChart"></canvas>
        </div>
    </div>
</div>

<!-- Trade Log -->
<div class="section-title">Trade Log ({total_trades} total, showing last {len(trade_log)})</div>
<div class="chart-container" style="overflow-x: auto;">
    <table>
        <thead>
            <tr>
                <th>Date</th>
                <th>Sell</th>
                <th>Shares</th>
                <th>Price</th>
                <th>Buy</th>
                <th>Shares</th>
                <th>Price</th>
                <th>Loss</th>
                <th>Tax Save</th>
                <th>Corr</th>
            </tr>
        </thead>
        <tbody>
            {trade_rows}
        </tbody>
    </table>
</div>

<div class="footer">
    Generated by Balancer backtest engine |
    {datetime.now().strftime("%Y-%m-%d %H:%M")}
</div>

<script>
const chartColors = {{
    portfolio: '#58a6ff',
    benchmark: '#8b949e',
    taxAdj: '#3fb950',
    harvest: '#f0883e',
    savings: '#3fb950',
    te: '#bc8cff',
    trades: '#58a6ff',
    positions: '#58a6ff',
}};

const commonOptions = {{
    responsive: true,
    maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
        legend: {{
            labels: {{ color: '#8b949e', font: {{ size: 12 }} }}
        }}
    }},
    scales: {{
        x: {{
            ticks: {{ color: '#484f58', maxTicksLimit: 10 }},
            grid: {{ color: '#21262d' }}
        }},
        y: {{
            ticks: {{ color: '#484f58' }},
            grid: {{ color: '#21262d' }}
        }}
    }}
}};

// 1. Portfolio vs Benchmark
new Chart(document.getElementById('valueChart'), {{
    type: 'line',
    data: {{
        labels: {chart_dates},
        datasets: [
            {{
                label: 'Portfolio',
                data: {chart_pv},
                borderColor: chartColors.portfolio,
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 0,
            }},
            {{
                label: 'Benchmark (VOO)',
                data: {chart_bm},
                borderColor: chartColors.benchmark,
                backgroundColor: 'transparent',
                borderWidth: 2,
                borderDash: [6, 3],
                pointRadius: 0,
            }},
            {{
                label: 'Tax-Adjusted',
                data: {chart_tax_adj},
                borderColor: chartColors.taxAdj,
                backgroundColor: 'transparent',
                borderWidth: 1.5,
                borderDash: [3, 3],
                pointRadius: 0,
            }}
        ]
    }},
    options: {{
        ...commonOptions,
        scales: {{
            ...commonOptions.scales,
            y: {{
                ...commonOptions.scales.y,
                ticks: {{
                    ...commonOptions.scales.y.ticks,
                    callback: v => '$' + v.toLocaleString()
                }}
            }}
        }}
    }}
}});

// 2. Tracking Error
new Chart(document.getElementById('teChart'), {{
    type: 'line',
    data: {{
        labels: {chart_dates},
        datasets: [{{
            label: 'Tracking Error (bps)',
            data: {chart_te},
            borderColor: chartColors.te,
            backgroundColor: 'rgba(188,140,255,0.1)',
            fill: true,
            borderWidth: 1.5,
            pointRadius: 0,
        }}]
    }},
    options: commonOptions
}});

// 3. Cumulative TLH
new Chart(document.getElementById('tlhChart'), {{
    type: 'line',
    data: {{
        labels: {chart_dates},
        datasets: [
            {{
                label: 'Harvested Losses ($)',
                data: {chart_harvest},
                borderColor: chartColors.harvest,
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 0,
            }},
            {{
                label: 'Est. Tax Savings ($)',
                data: {chart_savings},
                borderColor: chartColors.savings,
                backgroundColor: 'rgba(63,185,80,0.1)',
                fill: true,
                borderWidth: 2,
                pointRadius: 0,
            }}
        ]
    }},
    options: {{
        ...commonOptions,
        scales: {{
            ...commonOptions.scales,
            y: {{
                ...commonOptions.scales.y,
                ticks: {{
                    ...commonOptions.scales.y.ticks,
                    callback: v => '$' + v.toLocaleString()
                }}
            }}
        }}
    }}
}});

// 4. Weekly Trades
new Chart(document.getElementById('tradeChart'), {{
    type: 'bar',
    data: {{
        labels: {weekly_dates},
        datasets: [{{
            label: 'Trades',
            data: {weekly_counts},
            backgroundColor: 'rgba(88,166,255,0.6)',
            borderColor: chartColors.trades,
            borderWidth: 1,
        }}]
    }},
    options: {{
        ...commonOptions,
        scales: {{
            ...commonOptions.scales,
            y: {{ ...commonOptions.scales.y, beginAtZero: true }}
        }}
    }}
}});

// 5. Positions
new Chart(document.getElementById('posChart'), {{
    type: 'line',
    data: {{
        labels: {chart_dates},
        datasets: [{{
            label: 'Positions',
            data: {chart_positions},
            borderColor: chartColors.positions,
            backgroundColor: 'rgba(88,166,255,0.1)',
            fill: true,
            borderWidth: 1.5,
            pointRadius: 0,
        }}]
    }},
    options: {{
        ...commonOptions,
        scales: {{
            ...commonOptions.scales,
            y: {{ ...commonOptions.scales.y, beginAtZero: true }}
        }}
    }}
}});
</script>
</body>
</html>"""
