# Balancer — Automatic E*TRADE Portfolio Rebalancer
> Keep retirement accounts aligned with your target allocation—safely and automatically.

Balancer connects to your E*TRADE accounts, computes trades to move toward your target ETF allocation, and (optionally) executes them.

## Why I built this
I wanted a simple, low‑cost, ETF-only rebalancer for retirement accounts without paying robo fees or accepting quarterly drift. Also a project to teach myself Python 🐍.

> DISCLAIMER: Personal finance software, not investment advice. Use at your own risk. Always start in `preview` mode and test in the E*TRADE sandbox before live trading.  
> E*TRADE developer portal: https://developer.etrade.com/home

## Features
- 🔒 **Safety-first**: `preview` → `confirm` → `auto`, plus a global kill switch.
- 🧮 **Configurable**: set your preferences for ETFs, target allocations, rebalance frequency, etc.
- 🔁 **Loop mode**: run on a schedule for hands-off upkeep.
- 🧰 **Designed for non-taxable accounts** (401k/IRA). Taxable version (with tax-loss harvesting + multi-ticker diversity) is on the roadmap.

---

## Quick Start

### 1. E*TRADE Developer Setup
1. Create a developer account + app.
2. Obtain consumer key & secret.
3. Enable sandbox first.

### 2. Install
```bash
git clone https://github.com/JonnyAis/balancer.git
cd balancer
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Credentials
Preferred (environment variables):
```
ETRADE_CONSUMER_KEY=...
ETRADE_CONSUMER_SECRET=...
```
Alternative (local file you do NOT commit):
- Copy `credentials_example` to `credentials.py`
- Insert key/secret (file is in .gitignore)

### 4. Configure
Edit `balancer/config.yaml` (targets, optimizer, trading mode, filters).

Example excerpt:
```yaml
classes:
  Large:         { target_percent: 40, preferred_symbol: VOO }
  Small:         { target_percent: 12, preferred_symbol: VBR }
  International: { target_percent: 28, preferred_symbol: IXUS }
  Bonds:         { target_percent: 20, preferred_symbol: BND }

trading:
  mode: preview   # preview | confirm | auto
```

### 5. Run (Plan Only)
```bash
python -m balancer.main
```

### 6. Execute with Confirmation
Set `trading.mode: confirm`, rerun, answer `y`.

### 7. Fully Automatic
Set `trading.mode: auto` (use cautiously, especially if enabling loop mode).

---

## Configuration Reference

| Section | Purpose |
|---------|---------|
| classes | Target % allocation + single ETF per asset class |
| rebalance | Heuristic thresholds + integer optimizer settings |
| loop | Optional periodic execution |
| accounts | Include/exclude by description |
| trading | Execution behavior & safety |

### classes
Each class: target percent of total account value (cash included) and one ETF.

### rebalance (heuristic)
- `min_percent_drift`: Ignore small % drifts
- `min_notional_trade`: Skip tiny trades
- `max_notional_trade`: Cap single trade size
- `account_min_turnover`: Skip account if total (buys+sells) below this

### rebalance.integer_optimizer (recommended)
Global whole‑share search minimizing:
```
Σ (allocated_value_c - target_value_c)^2 + turnover_penalty * Σ |Δshares_c|
```
- `enabled`: true → use optimizer
- `window`: share search radius (complexity ~(2w+1)^Nclasses)
- `turnover_penalty`: dampen churn
- `cash_buffer`: leave this much cash uninvested

### loop
- `enabled`: true to re-run forever (requires `trading.mode: auto`)
- `interval_minutes`: sleep between cycles

### accounts
- `exclude_if_desc_contains`: substrings (case-insensitive) to skip
- `include_only_descriptions`: exact matches (if list non-empty)

### trading (single safety lever)
| mode | Behavior |
|------|----------|
| preview | Plan only |
| confirm | Plan + interactive yes/no |
| auto | Plan + immediate execution |

Other fields:
- `max_orders_per_account`: hard cap
- `allow_partial_funding_scale`: scale buys down if post-sell cash insufficient; else drop buys

---

## Funding & Execution Flow
1. Fetch positions, quotes, cash
2. Optimize (integer or heuristic)
3. Print plan (add per-class table with `REBALANCER_DETAILED_PLAN=1`)
4. (confirm) prompt user
5. Execute sells → refresh cash → execute buys (scaling if enabled)
6. No unfunded buys submitted

---

## Environment Toggles
| Var | Effect |
|-----|--------|
| REBALANCER_DETAILED_PLAN=1 | Show per-class before/after table |
| REBALANCER_DEBUG_TRADES=1  | Dump raw trades JSON |
| REBALANCER_KILL=1          | Abort immediately |

---

## Optimizer vs Heuristic
| Aspect | Optimizer | Heuristic |
|--------|-----------|-----------|
| Rounding | Global | Per-class |
| Objective | Explicit (least squared deviation) | Implicit drifts |
| Stability | Tunable via penalty | Threshold-driven |
| Use | Recommended default | Fallback / disabled mode |

---

## Typical Workflow
1. Start with `mode: preview`
2. Move to `confirm`
3. Switch to `auto` (optionally enable loop)

---

## Roadmap (Potential)
- Tax-aware trading for non-retirement accounts 
- Custom diversification based on existing assets (e.g., company stock)
---

## License
MIT (see MIT License.txt)